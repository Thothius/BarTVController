import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ads import AdRunner, DisplayHub
from .config import ROOT, load_config, save_config
from .tv import TVController

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bartv")

cfg = load_config()
tv = TVController(cfg)
hub = DisplayHub()
ads = AdRunner(cfg, tv, hub)
scheduler = AsyncIOScheduler()
JOB_ID = "ad_break"

MEDIA_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".m3u8", ".mp3", ".aac", ".wav", ".m4a", ".jpg", ".jpeg", ".png", ".webp"}


def ad_trigger(minutes: int):
    if 0 < minutes <= 60 and 60 % minutes == 0:
        return CronTrigger(minute=f"*/{minutes}")
    return IntervalTrigger(minutes=minutes)


async def scheduled_ad():
    ads.trigger()


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
    scheduler.add_job(scheduled_ad, ad_trigger(cfg.ad_interval_minutes), id=JOB_ID)
    scheduler.start()
    if not cfg.scheduler_enabled:
        scheduler.pause_job(JOB_ID)
    yield
    scheduler.shutdown(wait=False)
    await tv.disconnect()


app = FastAPI(title="BarTVController", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# ---------- helpers ----------

def require_pin(request: Request):
    if not cfg.pin:
        return
    if request.headers.get("x-pin") != cfg.pin:
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


@app.get("/media/{path:path}")
async def media(path: str):
    file = (media_root() / path).resolve()
    if not str(file).startswith(str(media_root().resolve())) or not file.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(file)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse({}, status_code=204)


# ---------- API ----------

@app.get("/api/status")
async def status():
    job = scheduler.get_job(JOB_ID)
    return {
        "paired": bool(cfg.client_key),
        "tv_ip": cfg.tv_ip,
        "normal_input": cfg.normal_input,
        "signage_input": cfg.signage_input,
        "ad_running": ads.running,
        "last_ad": ads.last_run,
        "last_error": ads.last_error,
        "scheduler_enabled": cfg.scheduler_enabled,
        "next_ad": job.next_run_time.isoformat(timespec="seconds") if job and job.next_run_time else None,
        "displays_connected": len(hub.clients),
        "mock_tv": cfg.mock_tv,
        "pin_set": bool(cfg.pin),
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
    kind: str = "video"  # video | audio | image


@app.post("/api/trigger-ad", dependencies=[Depends(require_pin)])
async def trigger_ad():
    if not cfg.client_key:
        raise HTTPException(409, "Pair the TV first")
    if not ads.trigger():
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


@app.post("/api/pair", dependencies=[Depends(require_pin)])
async def pair():
    """Trigger a fresh pairing handshake. The TV shows 'Allow access?' - accept on the remote."""
    try:
        await tv.pair()
    except Exception as e:
        raise HTTPException(502, f"Pairing failed: {e}")
    return {"ok": True, "paired": True}


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


@app.websocket("/ws/display")
async def ws_display(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
