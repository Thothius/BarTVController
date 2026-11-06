import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "mock_tv": True, "client_key": None, "pin": "123456",
        "ad_duration_seconds": 1.0, "input_switch_delay_seconds": 0,
    }))
    monkeypatch.setenv("BARTV_CONFIG", str(cfg_path))
    import importlib
    import bartv.config, bartv.main
    importlib.reload(bartv.config)
    main = importlib.reload(bartv.main)
    with TestClient(main.app) as client:
        yield client, main, cfg_path


def test_pin_required(app_env):
    client, main, _ = app_env
    for url in ["/api/trigger-ad", "/api/volume", "/api/mute", "/api/input", "/api/pair", "/api/scheduler", "/api/stream", "/api/stop"]:
        assert client.post(url, json={}).status_code == 401, url
    assert client.get("/api/status").json()["pin_set"] is True
    assert client.get("/api/status").json()["paired"] is False


def test_pin_accepted(app_env):
    client, _, _ = app_env
    h = {"X-Pin": "123456"}
    client.post("/api/pair", json={}, headers=h)
    assert client.post("/api/volume", json={"level": 10}, headers=h).status_code == 200
    assert client.post("/api/volume", json={"level": 10}, headers={"X-Pin": "000000"}).status_code == 401


def test_pair_saves_key(app_env):
    client, main, cfg_path = app_env
    h = {"X-Pin": "123456"}
    assert client.post("/api/volume", json={"level": 10}, headers=h).status_code == 409
    assert client.post("/api/pair", json={}, headers=h).json()["paired"] is True
    assert json.loads(cfg_path.read_text())["client_key"] == "mock-client-key"


def test_controls(app_env):
    client, main, _ = app_env
    h = {"X-Pin": "123456"}
    client.post("/api/pair", json={}, headers=h)
    assert client.post("/api/volume", json={"level": 25}, headers=h).status_code == 200
    assert client.post("/api/mute", json={"mute": True}, headers=h).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_2"}, headers=h).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_9"}, headers=h).status_code == 400
    assert main.tv.client.calls == [("set_volume", 25), ("set_mute", True), ("set_input", "HDMI_2")]


def test_ad_break_switches_and_notifies_display(app_env):
    client, main, _ = app_env
    h = {"X-Pin": "123456"}
    client.post("/api/pair", json={}, headers=h)
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/trigger-ad", headers=h).status_code == 200
        assert client.post("/api/trigger-ad", headers=h).status_code == 409
        assert json.loads(ws.receive_text()) == {"type": "ad_start", "src": "/static/media/promo.mp4"}
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
    assert main.tv.client.calls == [("set_input", "HDMI_2"), ("set_input", "HDMI_1")]


def test_stream_and_files(app_env):
    client, main, _ = app_env
    h = {"X-Pin": "123456"}
    with client.websocket_connect("/ws/display") as ws:
        r = client.post("/api/stream", json={"url": "https://x/v.mp4", "kind": "video"}, headers=h)
        assert r.status_code == 200
        assert json.loads(ws.receive_text()) == {"type": "media_start", "url": "https://x/v.mp4", "kind": "video", "name": ""}
        assert client.post("/api/stop", json={}, headers=h).status_code == 200
        assert json.loads(ws.receive_text()) == {"type": "media_stop"}
    names = [f["name"] for f in client.get("/api/files").json()["files"]]
    assert "promo.mp4" in names


def test_media_serving(app_env):
    client, main, _ = app_env
    assert client.get("/media/promo.mp4").status_code == 200
    assert client.get("/media/../../bartv/main.py").status_code in (404, 400)


def test_scheduler_toggle(app_env):
    client, main, cfg_path = app_env
    h = {"X-Pin": "123456"}
    assert str(main.ad_trigger(30).fields[6]) == "*/30"
    s = client.post("/api/scheduler", json={"enabled": False}, headers=h).json()
    assert s["scheduler_enabled"] is False and s["next_ad"] is None
    assert client.post("/api/scheduler", json={"enabled": True}, headers=h).json()["next_ad"]
