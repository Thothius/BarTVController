import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import WebSocket

from .config import Config
from .tv import TVController

log = logging.getLogger("bartv.ads")


class DisplayHub:
    """Tracks /display kiosk pages connected over WebSocket."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, event: dict):
        msg = json.dumps(event)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                self.clients.discard(ws)


class AdRunner:
    def __init__(self, cfg: Config, tv: TVController, hub: DisplayHub):
        self.cfg = cfg
        self.tv = tv
        self.hub = hub
        self._task: Optional[asyncio.Task] = None
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def trigger(self) -> bool:
        """Start an ad break in the background. Returns False if one is already running."""
        if self.running:
            return False
        self._task = asyncio.create_task(self._run())
        return True

    async def run_now(self):
        if self.trigger():
            await self._task

    async def _run(self):
        self.last_run = datetime.now().isoformat(timespec="seconds")
        self.last_error = None
        try:
            await self.tv.set_input(self.cfg.signage_input)
            await asyncio.sleep(self.cfg.input_switch_delay_seconds)
            await self.hub.broadcast({"type": "ad_start", "src": self.cfg.promo_url})
            await asyncio.sleep(self.cfg.ad_duration_seconds)
        except Exception as e:
            log.exception("Ad break failed")
            self.last_error = str(e)
        finally:
            await self.hub.broadcast({"type": "ad_end"})
            try:
                await self.tv.set_input(self.cfg.normal_input)
            except Exception as e:
                log.exception("Failed to restore normal input")
                self.last_error = str(e)
