import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "mock_tv": True, "client_key": None,
        "ad_duration_seconds": 0.2, "input_switch_delay_seconds": 0,
    }))
    monkeypatch.setenv("BARTV_CONFIG", str(cfg_path))
    import importlib
    import bartv.config, bartv.main
    importlib.reload(bartv.config)
    main = importlib.reload(bartv.main)
    with TestClient(main.app) as client:
        yield client, main, cfg_path


def test_pair_saves_key(app_env):
    client, main, cfg_path = app_env
    assert client.get("/api/status").json()["paired"] is False
    assert client.post("/api/volume", json={"level": 10}).status_code == 409
    assert client.post("/api/pair").json()["paired"] is True
    assert json.loads(cfg_path.read_text())["client_key"] == "mock-client-key"


def test_controls(app_env):
    client, main, _ = app_env
    client.post("/api/pair")
    assert client.post("/api/volume", json={"level": 25}).status_code == 200
    assert client.post("/api/volume", json={"level": 101}).status_code == 422
    assert client.post("/api/mute", json={"mute": True}).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_2"}).status_code == 200
    assert main.tv.client.calls == [("set_volume", 25), ("set_mute", True), ("set_input", "HDMI_2")]


def test_ad_break_switches_and_notifies_display(app_env):
    client, main, _ = app_env
    client.post("/api/pair")
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/trigger-ad").status_code == 200
        assert client.post("/api/trigger-ad").status_code == 409
        assert json.loads(ws.receive_text()) == {"type": "ad_start", "src": "/static/media/promo.mp4"}
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
    assert main.tv.client.calls == [("set_input", "HDMI_2"), ("set_input", "HDMI_1")]


def test_scheduler_toggle_and_trigger(app_env):
    client, main, cfg_path = app_env
    trig = main.ad_trigger(30)
    assert str(trig.fields[6]) == "*/30"  # minute field
    s = client.post("/api/scheduler", json={"enabled": False}).json()
    assert s["scheduler_enabled"] is False and s["next_ad"] is None
    assert json.loads(cfg_path.read_text())["scheduler_enabled"] is False
    assert client.post("/api/scheduler", json={"enabled": True}).json()["next_ad"]
