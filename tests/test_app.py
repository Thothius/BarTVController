import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROMO = Path(__file__).resolve().parent.parent / "static" / "media" / "promo.mp4"


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    # trigger-ad requires a real promo file under static/; create a dummy if absent.
    if not PROMO.exists():
        PROMO.write_bytes(b"fake-mp4")
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


H = {"X-Pin": "123456"}


def test_pin_required(app_env):
    client, main, _ = app_env
    for url in ["/api/trigger-ad", "/api/volume", "/api/mute", "/api/input", "/api/pair", "/api/scheduler", "/api/stream", "/api/stop"]:
        assert client.post(url, json={}).status_code == 401, url
    s = client.get("/api/status").json()
    assert s["pin_set"] is True and s["paired"] is False and s["promo_exists"] is True


def test_pin_accepted(app_env):
    client, _, _ = app_env
    client.post("/api/pair", json={}, headers=H)
    assert client.post("/api/volume", json={"level": 10}, headers=H).status_code == 200
    assert client.post("/api/volume", json={"level": 10}, headers={"X-Pin": "000000"}).status_code == 401


def test_pair_saves_key_and_inputs(app_env):
    client, main, cfg_path = app_env
    assert client.post("/api/volume", json={"level": 10}, headers=H).status_code == 409
    assert client.post("/api/pair", json={}, headers=H).json()["paired"] is True
    assert json.loads(cfg_path.read_text())["client_key"] == "mock-client-key"
    assert client.get("/api/status").json()["tv_inputs"] == ["HDMI_1", "HDMI_2"]


def test_controls(app_env):
    client, main, _ = app_env
    client.post("/api/pair", json={}, headers=H)
    assert client.post("/api/volume", json={"level": 25}, headers=H).status_code == 200
    assert client.post("/api/mute", json={"mute": True}, headers=H).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_2"}, headers=H).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_9"}, headers=H).status_code == 400
    assert main.tv.client.calls == [("set_volume", 25), ("set_mute", True), ("set_input", "HDMI_2")]


def test_ad_break_switches_and_notifies_display(app_env):
    client, main, _ = app_env
    client.post("/api/pair", json={}, headers=H)
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/trigger-ad", headers=H).status_code == 200
        assert client.post("/api/trigger-ad", headers=H).status_code == 409
        assert json.loads(ws.receive_text()) == {"type": "ad_start", "src": "/static/media/promo.mp4"}
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
    assert main.tv.client.calls == [("set_input", "HDMI_2"), ("set_input", "HDMI_1")]


def test_ad_requires_promo_file(app_env):
    client, main, _ = app_env
    client.post("/api/pair", json={}, headers=H)
    main.cfg.promo_video_path = "static/media/does-not-exist.mp4"
    r = client.post("/api/trigger-ad", headers=H)
    assert r.status_code == 409 and "Promo video missing" in r.json()["detail"]
    main.cfg.promo_video_path = "media/promo.mp4"  # exists on disk but outside static/
    r = client.post("/api/trigger-ad", headers=H)
    assert r.status_code == 409


def test_stream_and_files(app_env):
    client, main, _ = app_env
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/stream", json={"url": "https://x/v.mp4", "kind": "video"}, headers=H).status_code == 200
        assert json.loads(ws.receive_text()) == {"type": "media_start", "url": "https://x/v.mp4", "kind": "video", "name": ""}
        assert client.post("/api/stop", json={}, headers=H).status_code == 200
        assert json.loads(ws.receive_text()) == {"type": "media_stop"}
        assert client.post("/api/stream", json={"url": "https://x/v.mp4", "kind": "bogus"}, headers=H).status_code == 422
    names = [f["name"] for f in client.get("/api/files").json()["files"]]
    assert "promo.mp4" in names


def test_media_serving_and_traversal(app_env):
    client, main, _ = app_env
    assert client.get("/media/promo.mp4").status_code == 200
    assert client.get("/media/../../bartv/main.py").status_code in (404, 400)
    assert client.get("/media/%2e%2e/%2e%2e/bartv/main.py").status_code in (404, 400)
    # sibling-prefix path must not be served (media_evil vs media)
    sibling = Path(main.media_root()).parent / "media_evil"
    sibling.mkdir(exist_ok=True)
    (sibling / "secret.txt").write_text("x")
    assert client.get("/media/../media_evil/secret.txt").status_code in (404, 400)
    (sibling / "secret.txt").unlink()
    sibling.rmdir()


def test_scheduler_toggle(app_env):
    client, main, cfg_path = app_env
    assert str(main.ad_trigger(30).fields[6]) == "*/30"
    s = client.post("/api/scheduler", json={"enabled": False}, headers=H).json()
    assert s["scheduler_enabled"] is False and s["next_ad"] is None
    assert client.post("/api/scheduler", json={"enabled": True}, headers=H).json()["next_ad"]
