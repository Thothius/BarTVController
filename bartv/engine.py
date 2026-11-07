"""Program engine: runs the active program's base content and its commercials.

- base "tv": the TV's normal input is the base layer; commercials interrupt it.
- base "content": assets play as a looping playlist on the signage input;
  commercials interrupt and the playlist resumes afterwards (the /display
  page pauses and resumes client-side, so the server just keeps the loop waiting).
- Ad policy per program: `ads_per_hour` random picks from the commercials pool,
  never the same ad twice in a row. Programs without commercials fall back to
  the legacy promo.mp4 ad break on the old cadence.
"""
import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

from .config import Config
from .library import AssetStore, ProgramStore
from .tv import TVController

log = logging.getLogger("bartv.engine")

DEFAULT_ITEM_TIMEOUT = 12.0


class ProgramEngine:
    def __init__(self, cfg: Config, tv: TVController, hub, programs: ProgramStore, assets: AssetStore):
        self.cfg = cfg
        self.tv = tv
        self.hub = hub
        self.programs = programs
        self.assets = assets
        self._content_task: Optional[asyncio.Task] = None
        self._ad_task: Optional[asyncio.Task] = None
        self._ended: dict[str, asyncio.Event] = {}
        self.last_ad: Optional[str] = None
        self.last_error: Optional[str] = None
        self.state = {"mode": "idle", "program": None, "index": 0, "count": 0, "item": None}
        self._last_pool_pick: Optional[str] = None
        self._last_program_ad: Optional[datetime] = None
        self._last_legacy_ad: Optional[datetime] = None
        self._last_ad_event: Optional[dict] = None
        self._last_content_event: Optional[dict] = None
        self._started = datetime.now()

    # ---------- program resolution ----------

    def active_program(self) -> Optional[dict]:
        pid = self.cfg.active_program or "default"
        p = self.programs.get(pid)
        if p is None:
            all_p = self.programs.list()
            p = all_p[0] if all_p else None
        return p

    def _existing(self, filenames: list[str]) -> list[str]:
        have = {a["file"] for a in self.assets.list()}
        return [f for f in filenames if f in have]

    def _asset(self, filename: str) -> Optional[dict]:
        return next((a for a in self.assets.list() if a["file"] == filename), None)

    def commercial_pool(self) -> Optional[list[str]]:
        """Existing commercial files of the active program; None when not in program mode."""
        p = self.active_program()
        if not p:
            return None
        return self._existing(p["commercials"])

    # ---------- ended events from /display ----------

    def _event(self, what: str) -> asyncio.Event:
        return self._ended.setdefault(what, asyncio.Event())

    def notify_ended(self, what: str):
        self._event(what).set()

    async def _wait_ended(self, what: str, timeout: float):
        # The caller clears the event BEFORE broadcasting the start event, so an
        # 'ended' from the display can never arrive before we start waiting.
        ev = self._event(what)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # ---------- content playlist ----------

    @property
    def content_running(self) -> bool:
        return self._content_task is not None and not self._content_task.done()

    @property
    def ad_running(self) -> bool:
        return self._ad_task is not None and not self._ad_task.done()

    async def apply_program(self):
        """(Re)start the base layer for the active program. Safe to call anytime."""
        had_content = self.content_running
        if self.content_running:
            self._content_task.cancel()
            try:
                await self._content_task
            except (asyncio.CancelledError, Exception):
                pass
            self._content_task = None
        p = self.active_program()
        if p and p["base"] == "content" and self._existing(p["content"]):
            self._content_task = asyncio.create_task(self._content_loop())
        else:
            self.state.update(mode="idle", program=None, index=0, count=0, item=None)
            if had_content:
                try:
                    await self.tv.set_input(self.cfg.normal_input)
                except Exception:
                    log.debug("restore normal input failed", exc_info=True)

    async def shutdown(self):
        for task in (self._content_task, self._ad_task):
            if task and not task.done():
                task.cancel()
        for task in (self._content_task, self._ad_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _content_loop(self):
        idx = 0
        try:
            # Our playlist owns the signage input while it runs (ads share it).
            while True:
                try:
                    await self.tv.set_input(self.cfg.signage_input)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("Content loop: TV not ready, retrying in 10s", exc_info=True)
                    await asyncio.sleep(10)
            while True:
                p = self.active_program()
                files = self._existing(p["content"]) if p and p["base"] == "content" else []
                if not files:
                    self.state.update(mode="idle", program=None, index=0, count=0, item=None)
                    await asyncio.sleep(5)
                    continue
                f = files[idx % len(files)]
                idx += 1
                a = self._asset(f)
                url = "/media/" + f
                kind = (a or {}).get("kind") or "video"
                name = (a or {}).get("name") or f
                self.state.update(mode="content", program=p["id"], index=(idx - 1) % len(files),
                                  count=len(files), item=f)
                event = {"type": "content_start", "url": url, "kind": kind, "name": name,
                         "index": (idx - 1) % len(files), "count": len(files)}
                self._last_content_event = event
                self._event("content").clear()
                await self.hub.broadcast(event)
                await self._wait_ended("content", self._item_timeout(a))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Content loop failed")
            self.last_error = "Content loop failed - see server log"
            self.state["mode"] = "idle"

    def _item_timeout(self, asset: Optional[dict]) -> float:
        d = (asset or {}).get("duration")
        return (d + 4) if d else DEFAULT_ITEM_TIMEOUT

    # ---------- commercials ----------

    def play_ad(self, reason: str = "manual") -> bool:
        """Start a commercial break in the background. False when one is already running."""
        if self.ad_running:
            return False
        self._ad_task = asyncio.create_task(self._play_ad(reason))
        return True

    async def _play_ad(self, reason: str):
        pool = self.commercial_pool() or []
        program = self.active_program()
        ad = self._pick_ad(pool)
        self.last_ad = datetime.now().isoformat(timespec="seconds")
        self.last_error = None
        try:
            if ad:
                asset = self._asset(ad)
                url, kind, name = "/media/" + ad, (asset or {}).get("kind") or "video", \
                    (asset or {}).get("name") or ad
                timeout = self._item_timeout(asset)
                event = {"type": "ad_start", "url": url, "kind": kind, "name": name, "reason": reason}
            else:
                # Legacy promo break (no program commercials configured).
                url, kind, name = self.cfg.promo_url, "video", "Ad break"
                timeout = self.cfg.ad_duration_seconds + 4
                event = {"type": "ad_start", "src": url}
            self._last_ad_event = event
            self.state.update(mode="ad", ad={"name": name, "reason": reason})
            await self.tv.set_input(self.cfg.signage_input)
            await asyncio.sleep(self.cfg.input_switch_delay_seconds)
            self._event("ad").clear()
            await self.hub.broadcast(event)
            await self._wait_ended("ad", timeout)
        except Exception as e:
            log.exception("Ad break failed")
            self.last_error = str(e)
        finally:
            await self.hub.broadcast({"type": "ad_end"})
            self.state["mode"] = "content" if self.content_running else "idle"
            self.state["ad"] = None
            if not self.content_running:
                try:
                    await self.tv.set_input(self.cfg.normal_input)
                except Exception as e:
                    log.exception("Failed to restore normal input")
                    self.last_error = str(e)

    def _pick_ad(self, pool: list[str]) -> Optional[str]:
        if not pool:
            return None
        choices = [f for f in pool if f != self._last_pool_pick] or pool
        pick = random.choice(choices)
        self._last_pool_pick = pick
        return pick

    # ---------- scheduling ----------

    def tick(self):
        """Called every minute by APScheduler. Fires due commercials."""
        if not self.cfg.scheduler_enabled:
            return
        now = datetime.now()
        pool = self.commercial_pool()
        if pool:
            # Program commercials own the schedule (ads_per_hour=0 disables auto ads).
            p = self.active_program()
            per_hour = int(p.get("ads_per_hour", 3)) if p else 0
            if per_hour <= 0:
                return
            slot = timedelta(minutes=60.0 / per_hour)
            last = self._last_program_ad or self._started
            if now - last >= slot:
                self._last_program_ad = now
                if self.play_ad("schedule"):
                    log.info("Scheduled commercial from program '%s'", p["name"] if p else "?")
            return
        # Legacy mode: no program commercials configured -> promo ad on the old cadence.
        last = self._last_legacy_ad or self._started
        if now - last >= timedelta(minutes=self.cfg.ad_interval_minutes):
            self._last_legacy_ad = now
            if self.play_ad("schedule"):
                log.info("Scheduled legacy ad break")

    def next_ad_at(self) -> Optional[datetime]:
        if not self.cfg.scheduler_enabled:
            return None
        pool = self.commercial_pool()
        if pool:
            p = self.active_program()
            per_hour = int(p.get("ads_per_hour", 3)) if p else 0
            if per_hour <= 0:
                return None
            last = self._last_program_ad or self._started
            return last + timedelta(minutes=60.0 / per_hour)
        last = self._last_legacy_ad or self._started
        return last + timedelta(minutes=self.cfg.ad_interval_minutes)

    # ---------- status / websocket replay ----------

    def snapshot_event(self) -> Optional[dict]:
        """Event to send a freshly connected /display so it catches up with current state."""
        if self.ad_running and self._last_ad_event:
            return self._last_ad_event
        if self.content_running and self.state.get("mode") == "content" and self._last_content_event:
            return self._last_content_event
        return None

    def status(self) -> dict:
        p = self.active_program()
        pool = self.commercial_pool()
        return {
            "mode": self.state.get("mode", "idle"),
            "item": self.state.get("item"),
            "program": {"id": p["id"], "name": p["name"], "base": p["base"],
                        "ads_per_hour": p.get("ads_per_hour", 3)} if p else None,
            "pool_size": len(pool) if pool is not None else 0,
        }
