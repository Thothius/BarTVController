import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Literal, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ads import DisplayHub
from .config import CONFIG_PATH, ROOT, load_config, save_config
from .engine import ProgramEngine
from .library import MAX_UPLOAD_BYTES, AssetStore, ProgramStore
from .tv import TVController, discover_tvs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bartv")

cfg = load_config()
tv = TVController(cfg)
hub = DisplayHub()
assets = AssetStore(ROOT / cfg.media_dir, CONFIG_PATH.parent / "assets.json")
programs = ProgramStore(CONFIG_PATH.parent / "programs.json")
engine = ProgramEngine(cfg, tv, hub, programs, assets)
scheduler = AsyncIOScheduler()
JOB_ID = "ad_break"

MEDIA_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".m3u8", ".mp3", ".aac", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp"}


async def scheduled_tick():
    engine.tick()


async def _startup_handshake():
    """Best-effort connect at launch so a stale key after a TV firmware update is caught."""
    if not cfg.client_key:
        return
    try:
        ok = await asyncio.wait_for(tv._ensure(), timeout=5)
        if not ok:
            cfg.client_key = None
            save_config(cfg)
            log.warning("Saved client key rejected by TV - press 'Pair TV' once")
    except Exception:
        log.info("TV unreachable at startup (offline?)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup_handshake()
    scheduler.add_job(scheduled_tick, IntervalTrigger(minutes=1), id=JOB_ID)
    scheduler.start()
    if not cfg.scheduler_enabled:
        scheduler.pause_job(JOB_ID)
    await engine.apply_program()
    yield
    scheduler.shutdown(wait=False)
    await engine.shutdown()
    await tv.disconnect()


app = FastAPI(title="BarTVController", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# ---------- helpers ----------

def require_pin(request: Request):
    if not cfg.pin:
        return
    if not secrets.compare_digest(request.headers.get("x-pin", ""), cfg.pin):
        raise HTTPException(401, "Wrong or missing PIN")


def allowed_inputs():
    return {cfg.normal_input, cfg.signage_input}


def media_root():
    return ROOT / cfg.media_dir


async def tv_call(coro):
    try:
        await coro
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"TV error: {e}")
    return {"ok": True}


# ---------- pages ----------

@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/display", include_in_schema=False)
async def display():
    return FileResponse(ROOT / "static" / "display.html")


def promo_file():
    # promo_video_path is repo-relative and must live under static/ to be servable.
    return ROOT / cfg.promo_video_path


@app.get("/media/{path:path}")
async def media(path: str):
    root = media_root().resolve()
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(file)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse({}, status_code=204)


# ---------- API ----------

@app.get("/api/status")
async def status():
    job = scheduler.get_job(JOB_ID)
    next_ad = engine.next_ad_at()
    return {
        "paired": bool(cfg.client_key),
        "tv_ip": cfg.tv_ip,
        "normal_input": cfg.normal_input,
        "signage_input": cfg.signage_input,
        "ad_running": engine.ad_running,
        "last_ad": engine.last_ad,
        "last_error": engine.last_error,
        "scheduler_enabled": cfg.scheduler_enabled,
        "next_ad": next_ad.isoformat(timespec="seconds") if next_ad else None,
        "scheduler_job": bool(job and job.next_run_time),
        "displays_connected": len(hub.clients),
        "mock_tv": cfg.mock_tv,
        "pin_set": bool(cfg.pin),
        "promo_exists": promo_file().is_file(),
        "tv_inputs": tv.last_inputs,
        "pairing": pair_state["running"],
        "pair_error": pair_state["error"],
        "tvs": tv_list(),
        "ad_interval_minutes": cfg.ad_interval_minutes,
        "active_program": engine.active_program(),
        "engine": engine.status(),
    }


class VolumeReq(BaseModel):
    level: int = Field(ge=0, le=100)


class MuteReq(BaseModel):
    mute: bool


class InputReq(BaseModel):
    input: str


class SchedulerReq(BaseModel):
    enabled: bool


class StreamReq(BaseModel):
    url: str = Field(min_length=1)
    name: str = ""
    kind: Literal["video", "audio", "image"] = "video"


@app.post("/api/trigger-ad", dependencies=[Depends(require_pin)])
async def trigger_ad():
    if not cfg.client_key:
        raise HTTPException(409, "Pair the TV first")
    if engine.commercial_pool():
        # Active program has commercials: play a random pool ad, then resume.
        if not engine.play_ad("manual"):
            raise HTTPException(409, "Ad break already running")
        return {"ok": True}
    # Legacy promo break.
    if not promo_file().is_file():
        raise HTTPException(409, "Promo video missing - drop an mp4 into static/media/ "
                                 "(or set promo_video_path in config.json)")
    if not cfg.promo_video_path.startswith("static/"):
        raise HTTPException(409, "promo_video_path must point inside static/ to be playable")
    if not engine.play_ad("manual"):
        raise HTTPException(409, "Ad break already running")
    return {"ok": True}


@app.post("/api/volume", dependencies=[Depends(require_pin)])
async def volume(req: VolumeReq):
    return await tv_call(tv.set_volume(req.level))


@app.post("/api/mute", dependencies=[Depends(require_pin)])
async def mute(req: MuteReq):
    return await tv_call(tv.set_mute(req.mute))


@app.post("/api/input", dependencies=[Depends(require_pin)])
async def set_input(req: InputReq):
    if req.input not in allowed_inputs():
        raise HTTPException(400, f"Input must be one of {sorted(allowed_inputs())}")
    return await tv_call(tv.set_input(req.input))


@app.post("/api/stream", dependencies=[Depends(require_pin)])
async def stream(req: StreamReq):
    """Push a video/audio/image URL to every connected /display kiosk."""
    await hub.broadcast({"type": "media_start", "url": req.url, "kind": req.kind, "name": req.name})
    return {"ok": True}


@app.post("/api/stop", dependencies=[Depends(require_pin)])
async def stop_media():
    await hub.broadcast({"type": "media_stop"})
    return {"ok": True}


pair_task: Optional[asyncio.Task] = None
pair_state = {"running": False, "error": None}


def _tv_entry(ip: str) -> Optional[dict]:
    return next((t for t in cfg.tvs if t.get("ip") == ip), None)


def _remember_active_tv():
    """Store the active TV's pairing in the known-TVs list (real TVs only)."""
    if cfg.mock_tv:
        return
    entry = _tv_entry(cfg.tv_ip)
    if entry is None:
        entry = {"ip": cfg.tv_ip, "name": "LG TV", "client_key": None}
        cfg.tvs.append(entry)
    entry["client_key"] = cfg.client_key


def tv_list():
    out = [{"ip": t["ip"], "name": t.get("name") or "LG TV", "paired": bool(t.get("client_key")),
            "active": t["ip"] == cfg.tv_ip and not cfg.mock_tv} for t in cfg.tvs]
    if not cfg.mock_tv and not _tv_entry(cfg.tv_ip):
        out.insert(0, {"ip": cfg.tv_ip, "name": "LG TV", "paired": bool(cfg.client_key), "active": True})
    return out


async def _run_pair():
    global pair_task
    pair_state.update(running=True, error=None)
    try:
        await tv.pair()
        _remember_active_tv()
        save_config(cfg)
    except asyncio.CancelledError:
        pair_state["error"] = "cancelled"
    except Exception as e:
        pair_state["error"] = str(e)
    finally:
        pair_state["running"] = False
        pair_task = None


@app.post("/api/pair", dependencies=[Depends(require_pin)])
async def pair():
    """Start the pairing handshake in the background - the TV shows 'Allow access?'.

    Poll GET /api/pair/status; cancel with POST /api/pair/cancel.
    """
    global pair_task
    if pair_task and not pair_task.done():
        raise HTTPException(409, "Pairing already in progress")
    pair_state.update(running=True, error=None)
    pair_task = asyncio.create_task(_run_pair())
    return {"ok": True, "started": True}


@app.get("/api/pair/status")
async def pair_status():
    return {"running": pair_state["running"], "paired": bool(cfg.client_key), "error": pair_state["error"]}


@app.post("/api/pair/cancel", dependencies=[Depends(require_pin)])
async def pair_cancel():
    global pair_task
    if pair_task and not pair_task.done():
        pair_task.cancel()
    pair_state.update(running=False, error="cancelled")
    return {"ok": True}


@app.post("/api/unpair", dependencies=[Depends(require_pin)])
async def unpair():
    global pair_task
    if pair_task and not pair_task.done():
        pair_task.cancel()
    await tv.disconnect()
    tv.last_inputs = []
    cfg.client_key = None
    _remember_active_tv()
    save_config(cfg)
    return {"ok": True}


@app.post("/api/scheduler", dependencies=[Depends(require_pin)])
async def set_scheduler(req: SchedulerReq):
    cfg.scheduler_enabled = req.enabled
    save_config(cfg)
    scheduler.resume_job(JOB_ID) if req.enabled else scheduler.pause_job(JOB_ID)
    return await status()


@app.get("/api/files")
async def files():
    root = media_root()
    if not root.exists():
        return {"files": []}
    out = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS:
            out.append({"name": f.name, "url": "/media/" + str(f.relative_to(root)).replace("\\", "/"), "size": f.stat().st_size})
    return {"files": out}


class AssetPatch(BaseModel):
    name: Optional[str] = None
    thumb: Optional[str] = Field(None, max_length=400_000)
    duration: Optional[float] = Field(None, ge=0)
    role: Optional[str] = None  # "content" | "commercial" | "unsorted" (clears)


class TVSettings(BaseModel):
    tv_ip: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, max_length=60)
    mock_tv: Optional[bool] = None


@app.get("/api/tv/discover")
async def tv_discover():
    """SSDP search for LG webOS TVs on the local network (~3s)."""
    from . import netdiag
    my_ip = netdiag.local_ip(cfg.tv_ip or "8.8.8.8")
    found = await asyncio.get_running_loop().run_in_executor(None, netdiag.ssdp, my_ip, 3.0)
    tvs = [{"ip": ip, "name": d.get("name") or "LG webOS TV"} for ip, d in found.items()
           if netdiag.is_lg(d.get("server", ""), " ".join(d.get("types", [])), d.get("maker", ""), d.get("name", ""))]
    return {"tvs": tvs or await discover_tvs()}


@app.get("/api/network/scan", dependencies=[Depends(require_pin)])
async def network_scan():
    """Venue network diagnostics: Wi-Fi, LAN devices (+vendor), SSDP/mDNS, LG TVs, Bluetooth (~10-20s)."""
    from . import netdiag
    return await netdiag.scan(None if cfg.mock_tv else cfg.tv_ip, CONFIG_PATH.parent / "vendors.json")


@app.post("/api/tv", dependencies=[Depends(require_pin)])
async def tv_settings(req: TVSettings):
    """Select a TV (by IP) or toggle demo mode. Known TVs keep their pairing key."""
    if pair_task and not pair_task.done():
        raise HTTPException(409, "Cancel pairing first")
    ip = req.tv_ip.strip() if req.tv_ip else None
    switching = (ip and ip != cfg.tv_ip) or (req.mock_tv is not None and req.mock_tv != cfg.mock_tv)
    if switching:
        _remember_active_tv()
        await tv.disconnect()
        tv.last_inputs = []
        if req.mock_tv is not None:
            cfg.mock_tv = req.mock_tv
        if ip:
            cfg.tv_ip = ip
        entry = _tv_entry(cfg.tv_ip)
        cfg.client_key = None if cfg.mock_tv else (entry or {}).get("client_key")
    if not cfg.mock_tv and (ip or req.name):
        entry = _tv_entry(cfg.tv_ip)
        if entry is None:
            entry = {"ip": cfg.tv_ip, "name": "LG TV", "client_key": cfg.client_key}
            cfg.tvs.append(entry)
        if req.name:
            entry["name"] = req.name.strip()
    save_config(cfg)
    return await status()


def _lan_ip() -> Optional[str]:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((cfg.tv_ip or "10.255.255.255", 1))  # picks the interface that routes to the TV
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


@app.get("/api/tv/check")
async def tv_check():
    """Reachability of the configured TV's control ports - first thing to look at on a new network."""
    from .tv import probe
    ports = {} if cfg.mock_tv else await probe(cfg.tv_ip)
    return {"tv_ip": cfg.tv_ip, "mock_tv": cfg.mock_tv, "ports": ports, "laptop_ip": _lan_ip(),
            "reachable": True if cfg.mock_tv else any(ports.values()),
            "paired": bool(cfg.client_key), "connected": bool(tv.client and tv.client.is_connected()),
            "tv_inputs": tv.last_inputs}


@app.delete("/api/tvs/{ip}", dependencies=[Depends(require_pin)])
async def forget_tv(ip: str):
    cfg.tvs = [t for t in cfg.tvs if t.get("ip") != ip]
    if ip == cfg.tv_ip and not cfg.mock_tv:
        await tv.disconnect()
        tv.last_inputs = []
        cfg.client_key = None
    save_config(cfg)
    return await status()


@app.get("/api/assets")
async def list_assets():
    return {"assets": assets.list()}


@app.put("/api/assets/{filename}", dependencies=[Depends(require_pin)])
async def upload_asset(filename: str, request: Request, replace: bool = False):
    """Raw-body upload streamed to disk (body = file bytes)."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 1 GB)")
    try:
        target = assets.target_for(filename, replace=replace)
    except FileExistsError:
        raise HTTPException(409, "File exists - confirm replace to overwrite")
    except ValueError as e:
        raise HTTPException(400, str(e))
    tmp = target.with_name(target.name + ".part")
    written = 0
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large (max 1 GB)")
                fh.write(chunk)
        if written == 0:
            raise HTTPException(400, "Empty file")
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return assets.register(target.name)


@app.patch("/api/assets/{filename}", dependencies=[Depends(require_pin)])
async def patch_asset(filename: str, req: AssetPatch):
    if req.thumb is not None and req.thumb and not req.thumb.startswith("data:image/"):
        raise HTTPException(400, "thumb must be a data:image/ URL")
    if req.role is not None and req.role not in ("content", "commercial", "unsorted"):
        raise HTTPException(400, "role must be content, commercial or unsorted")
    try:
        asset = assets.update(filename, name=req.name, thumb=req.thumb, duration=req.duration,
                              role=req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return asset


@app.delete("/api/assets/{filename}", dependencies=[Depends(require_pin)])
async def delete_asset(filename: str):
    try:
        ok = assets.delete(filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError:
        # Windows keeps files open while they stream to a screen.
        raise HTTPException(409, "File is in use (playing on a screen) - try again in a moment")
    if not ok:
        raise HTTPException(404, "Asset not found")
    programs.scrub(filename)
    return {"ok": True}


class ProgramReq(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base: Literal["tv", "content"] = "content"
    content: list[str] = []
    commercials: list[str] = []
    ads_per_hour: int = Field(3, ge=0, le=48)
    shuffle: bool = False


class ProgramPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    base: Optional[Literal["tv", "content"]] = None
    content: Optional[list[str]] = None
    commercials: Optional[list[str]] = None
    ads_per_hour: Optional[int] = Field(None, ge=0, le=48)
    shuffle: Optional[bool] = None


class ActiveProgramReq(BaseModel):
    id: str = Field(min_length=1, max_length=80)


@app.get("/api/programs")
async def list_programs():
    active = engine.active_program()
    active_id = active["id"] if active else None
    return {"programs": [dict(p, active=(p["id"] == active_id)) for p in programs.list()],
            "active_id": active_id}


@app.post("/api/programs", dependencies=[Depends(require_pin)])
async def create_program(req: ProgramReq):
    return programs.create(req.name, req.content, req.commercials, base=req.base,
                           ads_per_hour=req.ads_per_hour, shuffle=req.shuffle)


@app.patch("/api/programs/{pid}", dependencies=[Depends(require_pin)])
async def update_program(pid: str, req: ProgramPatch):
    before = programs.get(pid)
    p = programs.update(pid, name=req.name, content=req.content, commercials=req.commercials,
                        base=req.base, ads_per_hour=req.ads_per_hour, shuffle=req.shuffle)
    if p is None:
        raise HTTPException(404, "Program not found")
    playback_changed = before and (before["base"], before["content"]) != (p["base"], p["content"])
    if pid == cfg.active_program and playback_changed:
        await engine.apply_program()
    return p


@app.delete("/api/programs/{pid}", dependencies=[Depends(require_pin)])
async def delete_program(pid: str):
    if not programs.delete(pid):
        raise HTTPException(404, "Program not found")
    if cfg.active_program == pid:
        cfg.active_program = "default" if programs.get("default") else None
        save_config(cfg)
        await engine.apply_program()
    return {"ok": True}


@app.post("/api/programs/skip", dependencies=[Depends(require_pin)])
async def skip_content():
    """Skip the currently playing playlist clip and advance to the next one."""
    if not engine.skip_content():
        raise HTTPException(409, "Nothing playing to skip")
    return {"ok": True}


@app.post("/api/programs/active", dependencies=[Depends(require_pin)])
async def set_active_program(req: ActiveProgramReq):
    """Make a program the running one: its base content plays and its commercials fire on schedule."""
    if not programs.get(req.id):
        raise HTTPException(404, "Program not found")
    cfg.active_program = req.id
    save_config(cfg)
    await engine.apply_program()
    return await status()


@app.websocket("/ws/display")
async def ws_display(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    snap = engine.snapshot_event()
    if snap:
        try:
            await ws.send_text(json.dumps(snap))
        except Exception:
            pass
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "ended":
                seq = msg.get("seq")
                engine.notify_ended(str(msg.get("what") or ""), seq if isinstance(seq, int) else None)
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
