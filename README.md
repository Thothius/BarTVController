# BarTVController

Local signage & ad-break controller for an LG webOS TV (built for the UQ75003LF). Staff control the TV from any phone browser; every 30 minutes (:00 and :30) the TV switches to the signage input, plays the promo video with sound, then switches back to live TV.

```
[ Staff phone browser ]
        |  HTTP (venue Wi-Fi)
        v
+----------------------------------------------+
| FastAPI backend (Windows terminal PC)        |
|  /            staff dashboard (PIN)          |
|  /display     fullscreen kiosk page          |
|  /api/*       ad, input, volume, stream, pin |
|  scheduler    ad break every N minutes       |
+-----------+------------------------+---------+
            | webOS WebSocket API    | WebSocket /ws/display
            v                        v
   LG TV: HDMI_1 = live TV   HDMI_2 = PC running /display in kiosk mode
```

Ad break: switch TV to `signage_input` → wait `input_switch_delay_seconds` → `/display` plays the promo → after `ad_duration_seconds` switch back to `normal_input`.

## Quick start (Windows)

```bat
git clone https://github.com/Thothius/BarTVController.git
cd BarTVController
setup.bat      :: once: creates .venv, installs deps, makes config.json
:: edit config.json -> set "tv_ip" to your TV's IP
start.bat      :: starts server, opens http://localhost:8000/ once it's up (re-running it just reopens the dashboard)
```

Python 3.10+ must be installed (tick "Add python.exe to PATH" in the installer). On the phone, open `http://<terminal-pc-ip>:8000/` on the venue Wi-Fi.

- **[docs/DEMO.md](docs/DEMO.md)** — laptop mock-mode demo script (LGTestTV).
- **[docs/RUNBOOK.md](docs/RUNBOOK.md)** — bar deployment runbook (setup, kiosk, pairing, troubleshooting).

## Pairing (the handshake)

The first time — and again whenever the TV forgets the app (e.g. after a firmware update, which shows "An unknown application requested access"):

1. Dashboard → **Pair TV**.
2. The TV shows **"Allow BarTVController to access this TV?"** → press **Accept** on the remote within 60 s.
3. The `client_key` is saved to `config.json`; everything is headless after that.
4. Right after pairing (and on every reconnect) the server reads the TV's input list — it shows up under **TV inputs** on the dashboard and a warning is logged if `normal_input`/`signage_input` don't match (e.g. your TV calls them `HDMI_1`/`HDMI_2` vs `HDMI1`/`HDMI2`).

On every `start.bat` launch the server also does a best-effort check with the saved key; if the TV rejects it, the key is cleared and the dashboard shows "not paired" — just pair once more.

## Dashboard

Bare bones, one screen:

- **Ad Break** — interrupt now (refused while one is running; auto ads every 30 min can be paused below)
- **Live / Signage** — switch TV input
- **Volume slider + Mute**
- **Play on TV** — pick a file dropped into `static/media/` (listed automatically) or paste any direct media URL; Video / Audio / Image modes
- **Pair TV**, **Auto ads** toggle

Every action asks for the **PIN** once per device (stored in the browser afterwards). The PIN is generated on first run, printed in the `start.bat` console, and stored in `config.json` — change it there and restart to rotate, or delete it to regenerate. "Reset saved PIN" on the dashboard forgets it on that device.

## HDMI 2 display device

Something must be plugged into HDMI 2 showing `/display` fullscreen — the terminal PC itself works:

```bat
kiosk.bat          :: fullscreen Edge/Chrome kiosk on this PC, sound allowed, Alt+F4 to exit
kiosk.bat 1920     :: same, but on a second monitor that starts at x=1920 (TV as extended display)
```

The kiosk skips files that are missing or won't play (after 2 s) instead of freezing, resumes content where it paused after each ad, and reconnects by itself if the server restarts.

(Any small stick/mini PC works too — point its browser at `http://<pc-ip>:8000/display`. Without the autoplay flag, click the page once so promo audio is allowed.)

## TV setup (bypass the smart home screen)

1. Settings → General → System → Additional Settings → Home Settings: **Power On Screen** = **Recent Input**; disable **Home Auto Launch** and **Home Promotion**.
2. Settings → General → Devices → External Devices: **TV On With Mobile** / **Turn On Via Wi-Fi** = on.
3. Give the TV a fixed IP (router DHCP reservation).

## `config.json`

| Key | Default | Meaning |
|---|---|---|
| `tv_ip` | `192.168.1.150` | TV address |
| `client_key` | `null` | Filled in by pairing |
| `normal_input` / `signage_input` | `HDMI_1` / `HDMI_2` | webOS input IDs |
| `ad_interval_minutes` | `30` | Divisors of 60 align to the clock (:00, :30) |
| `ad_duration_seconds` | `30` | How long signage stays on |
| `input_switch_delay_seconds` | `2` | HDMI settle time before playing |
| `promo_video_path` | `static/media/promo.mp4` | Ad video |
| `scheduler_enabled` | `true` | Auto ads on/off |
| `mock_tv` | `false` | Fake TV for testing without hardware |
| `pin` | generated | Dashboard PIN (6 digits) |
| `media_dir` | `static/media` | Folder served under `/media/` and listed on the dashboard |

Edit `config.json` then **restart** the server for changes to take effect (the dashboard's auto-ads toggle is the only live setting). `promo_video_path` must point inside `static/` — if the file is missing, "Ad Break" is refused with a clear message instead of showing a black screen.

## API

All `POST` endpoints need header `X-Pin: <pin>` (the dashboard handles this).

| Method | Path | Body |
|---|---|---|
| GET | `/api/status` | — |
| POST | `/api/trigger-ad` | — |
| POST | `/api/volume` | `{"level": 0-100}` |
| POST | `/api/mute` | `{"mute": true}` |
| POST | `/api/input` | `{"input": "HDMI_1"}` (only the two configured inputs) |
| POST | `/api/stream` | `{"url": "…", "kind": "video"\|"audio"\|"image"}` → pushes to all `/display` screens |
| POST | `/api/stop` | — |
| POST | `/api/pair` | — triggers the TV pairing prompt |
| POST | `/api/scheduler` | `{"enabled": false}` |
| GET | `/api/files` | media files in `media_dir` |
| WS | `/ws/display` | kiosk event channel |

## Development

```bat
pip install -r requirements-dev.txt
pytest
```

Linux/macOS works the same minus the .bat files: `uvicorn bartv.main:app --host 0.0.0.0 --port 8000`.
