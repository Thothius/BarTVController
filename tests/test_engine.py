"""Program engine tests: pool ads, content playlist loop, scheduling cadence."""
import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bartv.config import Config
from bartv.engine import ProgramEngine
from bartv.library import AssetStore, ProgramStore
from bartv.tv import TVController

PROMO = Path(__file__).resolve().parent.parent / "static" / "media" / "promo.mp4"


class FakeHub:
    def __init__(self):
        self.sent = []

    async def broadcast(self, event):
        self.sent.append(event)


async def wait_sent(hub, kind, timeout=2.0):
    for _ in range(int(timeout / 0.02)):
        hits = [e for e in hub.sent if e["type"] == kind]
        if hits:
            return hits[-1]
        await asyncio.sleep(0.02)
    raise AssertionError(f"no {kind} event within {timeout}s")


def make_engine(tmp_path, **cfg_kw):
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    for name in ("ad1.mp4", "ad2.mp4", "c1.mp4", "c2.mp4", "promo.mp4"):
        (media / name).write_bytes(b"fake")
    cfg = Config(mock_tv=True, client_key="k", pin="123456", input_switch_delay_seconds=0,
                 ad_duration_seconds=1.0, ad_interval_minutes=30, **cfg_kw)
    tv = TVController(cfg)
    hub = FakeHub()
    programs = ProgramStore(tmp_path / "programs.json")
    assets = AssetStore(media, tmp_path / "assets.json")
    engine = ProgramEngine(cfg, tv, hub, programs, assets)
    return engine, tv, hub, programs


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    if not PROMO.exists():
        PROMO.write_bytes(b"fake-mp4")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "mock_tv": True, "client_key": None, "pin": "123456",
        "ad_duration_seconds": 1.0, "input_switch_delay_seconds": 0,
        "ad_interval_minutes": 30,
    }))
    monkeypatch.setenv("BARTV_CONFIG", str(cfg_path))
    import importlib
    import bartv.config, bartv.main, bartv.tv
    importlib.reload(bartv.config)
    importlib.reload(bartv.tv)
    bartv.tv.MockClient.PAIR_DELAY_SECONDS = 0
    main = importlib.reload(bartv.main)
    main.assets.dir = tmp_path / "media"
    main.cfg.media_dir = str(tmp_path / "media")
    (tmp_path / "media").mkdir()
    for name in ("promo.mp4", "ad1.mp4", "ad2.mp4", "c1.mp4", "c2.mp4"):
        (tmp_path / "media" / name).write_bytes(b"fake-" + name.encode())
    with TestClient(main.app) as client:
        yield client, main, cfg_path


H = {"X-Pin": "123456"}


def pair(client):
    assert client.post("/api/pair", json={}, headers=H).status_code == 200
    for _ in range(100):
        s = client.get("/api/pair/status").json()
        if not s["running"]:
            return s
        time.sleep(0.02)
    raise AssertionError("pairing never finished")


# ---------- unit-level engine tests ----------

def test_content_loop_advances_and_loops(tmp_path):
    async def run():
        engine, tv, hub, programs = make_engine(tmp_path)
        p = programs.create("MMA", ["c1.mp4", "c2.mp4"], [], base="content")
        engine.cfg.active_program = p["id"]
        await engine.apply_program()
        await asyncio.sleep(0.3)
        starts = [e for e in hub.sent if e["type"] == "content_start"]
        assert len(starts) == 1 and starts[0]["url"] == "/media/c1.mp4"
        assert engine.state["mode"] == "content"
        assert tv.client.calls[0] == ("set_input", "HDMI_2")  # playlist owns signage input
        # First clip ends -> second starts
        engine.notify_ended("content")
        await asyncio.sleep(0.2)
        starts = [e for e in hub.sent if e["type"] == "content_start"]
        assert starts[-1]["url"] == "/media/c2.mp4" and starts[-1]["index"] == 1
        # Second ends -> loops back to first
        engine.notify_ended("content")
        await asyncio.sleep(0.2)
        starts = [e for e in hub.sent if e["type"] == "content_start"]
        assert starts[-1]["url"] == "/media/c1.mp4" and starts[-1]["index"] == 0
        await engine.shutdown()
    asyncio.run(run())


def test_pool_ad_no_immediate_repeat_and_legacy_fallback(tmp_path):
    async def run():
        engine, tv, hub, programs = make_engine(tmp_path)
        p = programs.create("Ads", [], ["ad1.mp4", "ad2.mp4"], base="tv", ads_per_hour=6)
        engine.cfg.active_program = p["id"]
        # no-repeat picker
        picks = {engine._pick_ad(["ad1.mp4", "ad2.mp4"]) for _ in range(4)}
        assert picks == {"ad1.mp4", "ad2.mp4"}
        # manual ad from pool
        assert engine.play_ad("manual") is True
        await asyncio.sleep(0.2)
        ad_start = next(e for e in hub.sent if e["type"] == "ad_start")
        assert ad_start["url"].startswith("/media/ad") and ad_start["reason"] == "manual"
        assert engine.ad_running
        engine.notify_ended("ad")
        for _ in range(50):
            if not engine.ad_running:
                break
            await asyncio.sleep(0.02)
        assert any(e["type"] == "ad_end" for e in hub.sent)
        # base tv -> input restored to normal after the break
        assert tv.client.calls[-1] == ("set_input", "HDMI_1")
        # second ad must differ (pool of 2, no immediate repeat)
        first = ad_start["url"]
        assert engine.play_ad("manual") is True
        await asyncio.sleep(0.2)
        second = next(e for e in reversed(hub.sent) if e["type"] == "ad_start")
        assert second["url"] != first
        engine.notify_ended("ad")
        await engine.shutdown()
    asyncio.run(run())


def test_tick_cadence_program_vs_legacy(tmp_path):
    async def run():
        engine, tv, hub, programs = make_engine(tmp_path)
        # program with commercials, 60 ads/hour -> 1 minute slot
        p = programs.create("P", [], ["ad1.mp4"], base="tv", ads_per_hour=60)
        engine.cfg.active_program = p["id"]
        engine._last_program_ad = datetime.now() - timedelta(minutes=2)
        engine.tick()
        assert engine.ad_running
        await wait_sent(hub, "ad_start")
        engine.notify_ended("ad")
        for _ in range(100):
            if not engine.ad_running:
                break
            await asyncio.sleep(0.02)
        # not due yet -> no new ad
        engine.tick()
        await asyncio.sleep(0.1)
        assert engine.ad_running is False
        await engine.shutdown()
    asyncio.run(run())


def test_tick_legacy_when_no_pool_and_disabled_when_zero(tmp_path):
    async def run():
        engine, tv, hub, programs = make_engine(tmp_path)
        # Default program has no commercials -> legacy promo cadence
        engine._last_legacy_ad = datetime.now() - timedelta(minutes=31)
        engine.tick()
        assert engine.ad_running
        await wait_sent(hub, "ad_start")
        engine.notify_ended("ad")
        for _ in range(100):
            if not engine.ad_running:
                break
            await asyncio.sleep(0.02)
        # ads_per_hour = 0 disables auto ads even with a pool
        p = programs.create("Off", [], ["ad1.mp4"], base="tv", ads_per_hour=0)
        engine.cfg.active_program = p["id"]
        engine._last_program_ad = datetime.now() - timedelta(hours=2)
        engine.tick()
        await asyncio.sleep(0.1)
        assert engine.ad_running is False
        await engine.shutdown()
    asyncio.run(run())


# ---------- API-level tests ----------

def test_active_program_endpoint(app_env):
    client, main, _ = app_env
    assert client.post("/api/programs/active", json={"id": "nope"}, headers=H).status_code == 404
    assert client.post("/api/programs/active", json={"id": "default"}).status_code == 401
    s = client.post("/api/programs/active", json={"id": "default"}, headers=H).json()
    assert s["active_program"]["id"] == "default"
    progs = client.get("/api/programs").json()
    assert progs["active_id"] == "default"
    assert {p["id"]: p["active"] for p in progs["programs"]} == {"default": True}


def test_program_ad_over_websocket(app_env):
    client, main, _ = app_env
    pair(client)
    client.post("/api/programs", json={"name": "Night", "base": "tv", "commercials": ["ad1.mp4", "ad2.mp4"],
                                       "ads_per_hour": 6}, headers=H)
    pid = client.get("/api/programs").json()["programs"][-1]["id"]
    client.post("/api/programs/active", json={"id": pid}, headers=H)
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/trigger-ad", headers=H).status_code == 200
        first = json.loads(ws.receive_text())
        assert first["type"] == "ad_start" and first["url"].startswith("/media/ad")
        assert first["reason"] == "manual" and first["kind"] == "video"
        ws.send_text(json.dumps({"type": "ended", "what": "ad"}))
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
        # pool of 2 -> next ad differs
        assert client.post("/api/trigger-ad", headers=H).status_code == 200
        second = json.loads(ws.receive_text())
        assert second["url"] != first["url"]
        ws.send_text(json.dumps({"type": "ended", "what": "ad"}))
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
    calls = [c for c in main.tv.client.calls if c[0] == "set_input"]
    assert calls == [("set_input", "HDMI_2"), ("set_input", "HDMI_1"), ("set_input", "HDMI_2"), ("set_input", "HDMI_1")]


def test_content_program_streams_playlist(app_env):
    client, main, _ = app_env
    pair(client)
    client.post("/api/programs", json={"name": "Show", "base": "content", "content": ["c1.mp4", "c2.mp4"],
                                       "commercials": []}, headers=H)
    pid = client.get("/api/programs").json()["programs"][-1]["id"]
    client.post("/api/programs/active", json={"id": pid}, headers=H)
    with client.websocket_connect("/ws/display") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "content_start" and first["url"] == "/media/c1.mp4"
        assert first["count"] == 2
        ws.send_text(json.dumps({"type": "ended", "what": "content"}))
        second = json.loads(ws.receive_text())
        assert second["url"] == "/media/c2.mp4" and second["index"] == 1
    st = client.get("/api/status").json()
    assert st["engine"]["mode"] == "content" and st["active_program"]["id"] == pid
