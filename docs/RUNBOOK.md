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

## First real-TV test (checklist)

1. **Same network.** Guest Wi-Fi ("Drink") isolates devices - the laptop can't see the TV there.
   On the TV: Settings → General → Network → Wi-Fi Connection shows its network + IP.
   Put the laptop on that network (likely the staff SSID), or run Windows Mobile Hotspot and join the TV to it.
2. **TV settings.** Turn on *LG Connect Apps* / *Mobile TV On* (name varies by model) so ports 3000/3001 accept control.
   Ideally reserve the TV's IP on the router (DHCP reservation) so it never changes.
3. **App settings.** Dashboard → Settings: turn Mock mode **off**, enter the TV's IP.
4. **Reachability.** Open `http://localhost:8000/api/tv/check` - `"reachable": true` means the TV's
   control port answers. `false` = wrong IP / TV off / still on an isolated network (pairing can't work yet).
5. **Pair.** Settings → Pair, press **Allow** on the TV remote within 60 s. Errors now say what's wrong
   (unreachable vs declined vs timed out).
6. **Inputs.** After pairing, `/api/tv/check` lists `tv_inputs` (e.g. `HDMI_1`, `HDMI_2`). Make sure
   `normal_input` (cable/live TV) and `signage_input` (this laptop) in `config.json` match those IDs.
7. **Kiosk.** Laptop HDMI → TV signage input, run `kiosk.bat` (or `kiosk.bat 1920` if the TV is a 2nd screen).
8. **Ad break.** Press *Ad break*: TV should switch to the signage input, play the ad, switch back.

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
| "Can't reach the TV at …" | `/api/tv/check` - TV off, wrong IP, or laptop on a different/isolated Wi-Fi |
| "TV not paired" / unknown application | Settings → Pair, accept on remote |
| Black screen on HDMI 2 | Kiosk Chrome not running `/display`; check `chrome --kiosk` command |
| No sound | Click the kiosk screen once (audio unlock overlay) |
| Ad plays but TV doesn't switch | TV offline or wrong `tv_ip`; check Settings TV card |
| Playlist races through clips | No `/display` connected — the 12s fallback timeout advances items; open the kiosk or the preview |
