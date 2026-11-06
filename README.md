# BarTVController

Local signage & ad-break controller for an LG webOS TV (built for the UQ75003LF). Staff control the TV from a phone; every 30 minutes (:00 and :30) the TV switches to the signage input, plays the promo video with sound, then switches back to live TV.

```
[ Staff phone / terminal browser ]
        |  HTTP (venue Wi-Fi)
        v
+----------------------------------------------+
| FastAPI backend (terminal PC)                |
|  /            staff dashboard                |
|  /display     fullscreen kiosk page          |
|  /api/*       ad, input, volume, mute, pair  |
|  scheduler    ad break every N minutes       |
+-----------+------------------------+---------+
            | webOS WebSocket API    | WebSocket /ws/display
            v                        v
   LG TV: HDMI_1 = live TV   HDMI_2 = PC running /display in kiosk mode
```

Ad break: switch TV to `signage_input` -> wait `input_switch_delay_seconds` -> `/display` plays the promo -> after `ad_duration_seconds` switch back to `normal_input`.

## Hardware hookup
- **HDMI 1**: live TV / sports box.
- **HDMI 2**: the terminal PC (or any mini PC / stick) showing `http://<server>:8000/display` fullscreen in Chrome:
  ```
  google-chrome --kiosk --autoplay-policy=no-user-gesture-required http://localhost:8000/display
  ```
  Without the autoplay flag, click the page once after opening it so the promo can play with sound.

## TV setup (bypass the smart home screen)
1. Settings -> General -> System -> Additional Settings -> Home Settings: set **Power On Screen** to **Recent Input**; disable **Home Auto Launch** and **Home Promotion**.
2. Settings -> General -> Devices -> External Devices: turn on **TV On With Mobile** / **Turn On Via Wi-Fi**.
3. Give the TV a fixed IP (DHCP reservation on the router).

## Install & run
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # set tv_ip
cp /path/to/your/promo.mp4 static/media/promo.mp4
uvicorn bartv.main:app --host 0.0.0.0 --port 8000
```
Open `http://<terminal-pc-ip>:8000/` on a phone connected to the venue Wi-Fi.

## Pairing
Tap **Pair TV** on the dashboard (or run `python -m bartv.pair`), then press **Accept** on the TV prompt with the physical remote within 60 s. The client key is saved to `config.json`, so later runs are headless.

## Configuration (`config.json`)
| Key | Default | Meaning |
|---|---|---|
| `tv_ip` | `192.168.1.150` | TV address |
| `client_key` | `null` | Filled in by pairing |
| `normal_input` / `signage_input` | `HDMI_1` / `HDMI_2` | webOS input IDs |
| `ad_interval_minutes` | `30` | Divisors of 60 align to the clock (:00, :30) |
| `ad_duration_seconds` | `30` | How long the signage input stays on |
| `input_switch_delay_seconds` | `2` | Wait for HDMI switch before playing |
| `promo_video_path` | `static/media/promo.mp4` | Video served to `/display` |
| `scheduler_enabled` | `true` | Auto ads on/off (also toggled from the dashboard) |
| `mock_tv` | `false` | Fake TV for testing without hardware |

## API
| Method | Path | Body |
|---|---|---|
| GET | `/api/status` | - |
| POST | `/api/trigger-ad` | - |
| POST | `/api/volume` | `{"level": 0-100}` |
| POST | `/api/mute` | `{"mute": true}` |
| POST | `/api/input` | `{"input": "HDMI_1"}` |
| POST | `/api/pair` | - |
| POST | `/api/scheduler` | `{"enabled": false}` |

`scripts/tv_control.py [seconds]` does a one-off input switch test from the terminal.

## Development
```
pip install -r requirements-dev.txt
pytest
```
There is no auth: run it only on a trusted venue network.
