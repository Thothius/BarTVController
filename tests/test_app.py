import json
import time
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
    import bartv.config, bartv.main, bartv.tv
    importlib.reload(bartv.config)
    importlib.reload(bartv.tv)
    bartv.tv.MockClient.PAIR_DELAY_SECONDS = 0
    main = importlib.reload(bartv.main)
    main.assets.dir = tmp_path / "media"
    main.cfg.media_dir = str(tmp_path / "media")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "promo.mp4").write_bytes(b"fake-mp4")
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


def test_pin_required(app_env):
    client, main, _ = app_env
    for url in ["/api/trigger-ad", "/api/volume", "/api/mute", "/api/input", "/api/pair", "/api/scheduler",
                "/api/stream", "/api/stop", "/api/pair/cancel", "/api/unpair", "/api/tv", "/api/programs"]:
        assert client.post(url, json={}).status_code == 401, url
    assert client.put("/api/assets/x.mp4", content=b"x").status_code == 401
    assert client.patch("/api/assets/promo.mp4", json={}).status_code == 401
    assert client.delete("/api/assets/promo.mp4").status_code == 401
    assert client.delete("/api/programs/default").status_code == 401
    s = client.get("/api/status").json()
    assert s["pin_set"] is True and s["paired"] is False and s["promo_exists"] is True


def test_pin_accepted(app_env):
    client, _, _ = app_env
    pair(client)
    assert client.post("/api/volume", json={"level": 10}, headers=H).status_code == 200
    assert client.post("/api/volume", json={"level": 10}, headers={"X-Pin": "000000"}).status_code == 401


def test_pair_saves_key_and_inputs(app_env):
    client, main, cfg_path = app_env
    assert client.post("/api/volume", json={"level": 10}, headers=H).status_code == 409
    assert pair(client)["paired"] is True
    assert json.loads(cfg_path.read_text())["client_key"] == "mock-client-key"
    assert client.get("/api/status").json()["tv_inputs"] == ["HDMI_1", "HDMI_2"]
    assert client.post("/api/unpair", json={}, headers=H).status_code == 200
    assert client.get("/api/status").json()["paired"] is False


def test_pair_cancel(app_env):
    client, main, _ = app_env
    main.tv.cfg.mock_tv = True
    import bartv.tv
    bartv.tv.MockClient.PAIR_DELAY_SECONDS = 5
    assert client.post("/api/pair", json={}, headers=H).status_code == 200
    assert client.get("/api/status").json()["pairing"] is True
    assert client.post("/api/pair", json={}, headers=H).status_code == 409
    assert client.post("/api/pair/cancel", json={}, headers=H).status_code == 200
    for _ in range(50):
        s = client.get("/api/pair/status").json()
        if not s["running"]:
            break
        time.sleep(0.02)
    assert s == {"running": False, "paired": False, "error": "cancelled"}


def test_tv_switching_keeps_keys(app_env):
    client, main, cfg_path = app_env
    main.cfg.mock_tv = False
    main.cfg.tvs = [{"ip": "10.0.0.5", "name": "Bar TV", "client_key": "key-a"}]
    main.cfg.tv_ip, main.cfg.client_key = "10.0.0.5", "key-a"
    s = client.post("/api/tv", json={"tv_ip": "10.0.0.6", "name": "Back room"}, headers=H).json()
    assert s["paired"] is False and s["tv_ip"] == "10.0.0.6"
    assert {t["ip"]: t["paired"] for t in s["tvs"]} == {"10.0.0.5": True, "10.0.0.6": False}
    s = client.post("/api/tv", json={"tv_ip": "10.0.0.5"}, headers=H).json()
    assert s["paired"] is True and main.cfg.client_key == "key-a"
    assert "key-a" not in json.dumps(s)  # keys never exposed
    s = client.delete("/api/tvs/10.0.0.6", headers=H).json()
    assert [t["ip"] for t in s["tvs"]] == ["10.0.0.5"]
    s = client.post("/api/tv", json={"mock_tv": True}, headers=H).json()
    assert s["mock_tv"] is True and s["paired"] is False


def test_assets_crud(app_env):
    client, main, _ = app_env
    r = client.put("/api/assets/My Clip!.mp4", content=b"video-bytes", headers=H)
    assert r.status_code == 200 and r.json()["file"] == "My_Clip_.mp4"
    assert r.json()["name"] == "My Clip " or r.json()["name"].startswith("My Clip")
    assert client.put("/api/assets/My_Clip_.mp4", content=b"x", headers=H).status_code == 409
    assert client.put("/api/assets/My_Clip_.mp4?replace=true", content=b"x2", headers=H).status_code == 200
    assert client.put("/api/assets/evil.exe", content=b"x", headers=H).status_code == 400
    assert client.put("/api/assets/empty.mp4", content=b"", headers=H).status_code == 400
    thumb = "data:image/jpeg;base64,AAAA"
    r = client.patch("/api/assets/My_Clip_.mp4", json={"name": "Opening", "thumb": thumb, "duration": 12.345}, headers=H)
    assert r.json()["name"] == "Opening" and r.json()["thumb"] == thumb and r.json()["duration"] == 12.35
    assert client.patch("/api/assets/My_Clip_.mp4", json={"thumb": "javascript:x"}, headers=H).status_code == 400
    assert client.patch("/api/assets/nope.mp4", json={"name": "x"}, headers=H).status_code == 404
    listed = {a["file"]: a for a in client.get("/api/assets").json()["assets"]}
    assert listed["My_Clip_.mp4"]["kind"] == "video" and "promo.mp4" in listed
    assert client.get("/media/My_Clip_.mp4").content == b"x2"
    p = client.post("/api/programs", json={"name": "Test", "content": ["My_Clip_.mp4"], "commercials": ["My_Clip_.mp4"]}, headers=H).json()
    assert client.delete("/api/assets/My_Clip_.mp4", headers=H).status_code == 200
    assert client.delete("/api/assets/My_Clip_.mp4", headers=H).status_code == 404
    progs = {x["id"]: x for x in client.get("/api/programs").json()["programs"]}
    assert progs[p["id"]]["content"] == [] and progs[p["id"]]["commercials"] == []


def test_programs_crud(app_env):
    client, main, _ = app_env
    progs = client.get("/api/programs").json()["programs"]
    assert progs[0]["id"] == "default" and progs[0]["base"] == "tv"
    a = client.post("/api/programs", json={"name": "MMA Package", "content": ["a.mp4"]}, headers=H).json()
    b = client.post("/api/programs", json={"name": "MMA Package"}, headers=H).json()
    assert a["id"] == "mma-package" and b["id"] == "mma-package-2" and a["base"] == "content"
    assert client.post("/api/programs", json={"name": ""}, headers=H).status_code == 422
    r = client.patch("/api/programs/mma-package", json={"name": "MMA Night", "commercials": ["ad.mp4"]}, headers=H).json()
    assert r["name"] == "MMA Night" and r["content"] == ["a.mp4"] and r["commercials"] == ["ad.mp4"]
    assert client.patch("/api/programs/nope", json={"name": "x"}, headers=H).status_code == 404
    assert client.delete("/api/programs/mma-package-2", headers=H).status_code == 200
    assert client.delete("/api/programs/mma-package-2", headers=H).status_code == 404
    assert [p["id"] for p in client.get("/api/programs").json()["programs"]] == ["default", "mma-package"]


def test_controls(app_env):
    client, main, _ = app_env
    pair(client)
    assert client.post("/api/volume", json={"level": 25}, headers=H).status_code == 200
    assert client.post("/api/mute", json={"mute": True}, headers=H).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_2"}, headers=H).status_code == 200
    assert client.post("/api/input", json={"input": "HDMI_9"}, headers=H).status_code == 400
    assert main.tv.client.calls == [("set_volume", 25), ("set_mute", True), ("set_input", "HDMI_2")]


def test_ad_break_switches_and_notifies_display(app_env):
    client, main, _ = app_env
    pair(client)
    with client.websocket_connect("/ws/display") as ws:
        assert client.post("/api/trigger-ad", headers=H).status_code == 200
        assert client.post("/api/trigger-ad", headers=H).status_code == 409
        assert json.loads(ws.receive_text()) == {"type": "ad_start", "src": "/static/media/promo.mp4", "seq": 1}
        assert json.loads(ws.receive_text()) == {"type": "ad_end"}
    assert main.tv.client.calls == [("set_input", "HDMI_2"), ("set_input", "HDMI_1")]


def test_ad_requires_promo_file(app_env):
    client, main, _ = app_env
    pair(client)
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
    s = client.post("/api/scheduler", json={"enabled": False}, headers=H).json()
    assert s["scheduler_enabled"] is False and s["next_ad"] is None
    assert s["engine"]["mode"] == "idle"
    s = client.post("/api/scheduler", json={"enabled": True}, headers=H).json()
    assert s["next_ad"] and s["scheduler_job"] is True


def test_delete_in_use_file_returns_409(app_env, monkeypatch):
    client, main, _ = app_env
    def locked(_name):
        raise PermissionError("in use")
    monkeypatch.setattr(main.assets, "delete", locked)
    r = client.delete("/api/assets/promo.mp4", headers=H)
    assert r.status_code == 409 and "in use" in r.json()["detail"]


def test_real_tv_unreachable_gives_clear_errors(app_env):
    client, main, _ = app_env
    client.post("/api/tv", json={"mock_tv": False, "tv_ip": "127.0.0.1"}, headers=H)
    chk = client.get("/api/tv/check").json()
    assert chk["reachable"] is False and chk["tv_ip"] == "127.0.0.1"
    client.post("/api/pair", headers=H)
    for _ in range(50):
        st = client.get("/api/pair/status").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert "Can't reach the TV at 127.0.0.1" in st["error"]
