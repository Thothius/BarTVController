import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ads import AdRunner, DisplayHub
from .config import ROOT, load_config, save_config
from .tv import TVController

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

cfg = load_config()
tv = TVController(cfg)
hub = DisplayHub()
ads = AdRunner(cfg, tv, hub)
scheduler = AsyncIOScheduler()
JOB_ID = "ad_break"


def ad_trigger(minutes: int):
    # Align to the clock (:00, :30) when the interval divides an hour.
    if 0 < minutes <= 60 and 60 % minutes == 0:
        return CronTrigger(minute=f"*/{minutes}")
    return IntervalTrigger(minutes=minutes)


async def scheduled_ad():
    ads.trigger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_ad, ad_trigger(cfg.ad_interval_minutes), id=JOB_ID)
    scheduler.start()
    if not cfg.scheduler_enabled:
        scheduler.pause_job(JOB_ID)
    yield
    scheduler.shutdown(wait=False)
    await tv.disconnect()


app = FastAPI(title="BarTVController", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class VolumeReq(BaseModel):
    level: int = Field(ge=0, le=100)


class MuteReq(BaseModel):
    mute: bool


class InputReq(BaseModel):
    input: str = Field(pattern=r"^[A-Za-z0-9_]+$")


class SchedulerReq(BaseModel):
    enabled: bool


async def tv_call(coro):
    try:
        await coro
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"TV error: {e}")
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/display", include_in_schema=False)
async def display():
    return FileResponse(ROOT / "static" / "display.html")


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
    }


@app.post("/api/trigger-ad")
async def trigger_ad():
    if not cfg.client_key:
        raise HTTPException(409, "TV not paired yet - use 'Pair TV' first")
    if not ads.trigger():
        raise HTTPException(409, "Ad break already running")
    return {"ok": True}


@app.post("/api/volume")
async def volume(req: VolumeReq):
    return await tv_call(tv.set_volume(req.level))


@app.post("/api/mute")
async def mute(req: MuteReq):
    return await tv_call(tv.set_mute(req.mute))


@app.post("/api/input")
async def set_input(req: InputReq):
    return await tv_call(tv.set_input(req.input))


@app.post("/api/pair")
async def pair():
    try:
        await tv.pair()
    except Exception as e:
        raise HTTPException(502, f"Pairing failed: {e}")
    return {"ok": True, "paired": True}


@app.post("/api/scheduler")
async def set_scheduler(req: SchedulerReq):
    cfg.scheduler_enabled = req.enabled
    save_config(cfg)
    scheduler.resume_job(JOB_ID) if req.enabled else scheduler.pause_job(JOB_ID)
    return await status()


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
