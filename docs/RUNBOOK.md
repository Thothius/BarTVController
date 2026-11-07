# Bar runbook — running BarTVController at the venue

## One-time setup (terminal PC, Windows)

```
git clone https://github.com/Thothius/BarTVController.git
cd BarTVController
setup.bat          :: creates .venv, installs deps, copies config.example.json -> config.json
```

Edit `config.json`:
- `tv_ip` — the LG TV's IP (TV: Settings → General → Network → Wi-Fi Connection)
- `mock_tv` — `false`
- `pin` — a 4-6 digit staff PIN

Every shift: double-click `start.bat`. Console prints the dashboard URL + PIN.
Staff open `http://<pc-ip>:8000/` on their phones (venue Wi-Fi).

## HDMI 2 kiosk (the screen that plays our media)

Anything showing `/display` fullscreen on HDMI 2 works — the terminal PC itself:

```
start "" chrome --kiosk --autoplay-policy=no-user-gesture-required http://localhost:8000/display
```

Click once on the kiosk screen after boot (browser audio unlock). Keep the
"Click once for sound" overlay dismissed.

**One kiosk only.** Each `/display` client reports when its media ends; two
kiosks playing the same content would advance the playlist twice as fast.
The dashboard's built-in preview (`/display?preview=1`) is watch-only and safe.

## Pairing the TV (once, or after TV firmware reset)

Dashboard → Settings (gear) → TV card → **Pair**. Accept "Allow access?" on the
TV remote within 60s. The key saves to `config.json`; later launches are
headless. If the TV ever says "unknown application", the server auto-clears
the stale key — pair once more.

TV settings for a clean experience (Settings → General):
- Home Settings → Power On Screen: **Recent Input**
- Home Settings: disable **Home Auto Launch** / **Home Promotion**
- Devices → External Devices: **TV On With Mobile** ON

## Daily operation

- **Play ad break now** (home) — instant commercial from the active program's pool.
- **Programs** — set the active program (e.g. "MMA Night"); it owns the signage
  screen: playlist loops, commercials interrupt on schedule, playback resumes.
- **Assets** — drop videos/images/audio; thumbnails auto-generate.
- **Settings** — pair/unpair, volume/mute, scheduler pause, mock mode for testing.

## Autostart (optional)

Task Scheduler → Create Task → trigger "At log on" → action:
`C:\Users\Administrator\BarTVController\start.bat` (run whether logged on or not,
highest privileges not required).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Dashboard unreachable from phone | Same Wi-Fi; check `netstat -ano \| findstr :8000`; guest Wi-Fi client isolation blocks device-to-device — use a staff network or phone hotspot |
| "TV not paired" / unknown application | Settings → Pair, accept on remote |
| Black screen on HDMI 2 | Kiosk Chrome not running `/display`; check `chrome --kiosk` command |
| No sound | Click the kiosk screen once (audio unlock overlay) |
| Ad plays but TV doesn't switch | TV offline or wrong `tv_ip`; check Settings TV card |
| Playlist races through clips | No `/display` connected — the 12s fallback timeout advances items; open the kiosk or the preview |
