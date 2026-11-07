# BarTVController — Plan & Status

Living document. Update the Status section on every meaningful change.

## Goal

Bar signage + ad-break controller for the LG webOS TV (UQ75003LF):
staff control it from a phone browser; programmed "commercial breaks"
interrupt the shown content on a schedule; everything runs from the bar
laptop with one `start.bat`.

## What exists today (working, tested against mock TV)

- FastAPI backend (`bartv/`): dashboard `/`, kiosk `/display`, WebSocket
  event channel, PIN auth, volume/mute/input controls, ad break
  (`/api/trigger-ad`), media streaming (`/api/stream`), media library
  (`/api/files`, `/media/*`).
- APScheduler: ad break every `ad_interval_minutes` (clock-aligned),
  pause/resume toggle.
- Pairing handshake: dashboard "Pair TV" -> accept on TV remote ->
  client key saved to `config.json`; stale keys auto-cleared.
- Windows `setup.bat` / `start.bat`; `mock_tv: true` mode for
  hardware-free demo.
- 9 pytest tests pass. PR: https://github.com/Thothius/BarTVController/pull/1

## The "commercial running plan" (to build — Phase 2)

Programs + ad pool. Semantics agreed with Ted:

- A **program** is a named package: either
  - `mode: "tv"` — normal TV input (HDMI 1 live feed) is the base layer;
    commercials interrupt it, then switch back to TV; or
  - `mode: "playlist"` — plays media assets from `static/media/` in
    order (optionally shuffled, looping); commercials interrupt the
    current item, then playback **resumes where it left off**.
- **Ad policy** per program, configurable:
  - cadence: every X minutes, or X ads per Y minutes (e.g. 3 per hour);
  - pool: list of commercial media files; pick uniformly at random,
    never the same ad twice in a row;
  - instant: dashboard "Play ad now" plays a random pool ad immediately
    and resumes the program after it ends.
- Example programs: `Default` (mode tv, ~3 ads/hour), `MMA Package`
  (mode playlist with MMA highlight clips + its own ad pool).
- Asset library: everything in `static/media/` is auto-listed; adding
  a file = adding it to the pool or a program playlist via the dashboard.

## Phases

### Phase 0 — Laptop bring-up (IN PROGRESS)

- [x] Clone repo on bar laptop, `setup.bat` (Python 3.12.4, deps OK)
- [ ] Network: bar Wi-Fi "Drink" appears to have client isolation
      (laptop has internet, sees no LAN peers, TV not on 3000/3001/8008).
      Options: TV IP from TV settings + staff SSID, or Windows Mobile
      Hotspot on laptop (TV + phone join it).
- [ ] Set `tv_ip` in laptop `config.json`, `start.bat`, Pair TV, test
      volume/input controls on the real TV.
- [ ] TV-side settings (physical, Ted): Power On Screen = Recent Input,
      Home Auto Launch/Promotion off, Turn On Via Wi-Fi on, DHCP
      reservation for the TV.

### Phase 1 — Demo mode on laptop (no TV needed)

- [x] Demo media in `static/media/`: labeled synthetic clips
      (main content, MMA highlights, 2 ads) + CC0 flower clip
- [ ] Laptop `config.json`: `mock_tv: true` so the dashboard/kiosk demo
      runs without the TV
- [ ] Open `/` and `/display` in laptop browser tabs; verify ad break,
      stream, files, volume/mute against MockClient

### Phase 2 — Programs & ad pool engine

- [ ] `config.json`: `programs` map + `active_program` (keep backward
      compat with existing flat keys for the legacy single-ad mode)
- [ ] Program runner: playlist playback on `/display` via WS events,
      ad policy scheduler (cadence + random pool, no immediate repeat),
      resume-after-ad, fallback to TV input when playlist ends
- [ ] API: list/create/update programs, set active, per-program
      instant-ad; dashboard: program selector + bare-bones editor
- [ ] Tests for engine (mock TV, short durations)

### Phase 3 — Content pipeline

- [ ] `scripts/fetch.py`: yt-dlp wrapper (URL -> `static/media/`,
      optional trim/rename); `pip install yt-dlp` on laptop
- [ ] Gemini-generated commercial (needs `GEMINI_API_KEY` — ask Ted
      when ready); output lands in `static/media/` and joins the pool

### Phase 4 — Electron shell (OPTIONAL, deferred)

- [ ] Thin wrapper: spawn `python -m bartv.run`, open dashboard window,
      tray icon. Phones keep using the browser UI regardless.

### Phase 5 — Bar hardening

- [ ] Autostart on laptop boot (Task Scheduler or startup folder)
- [ ] Kiosk Chrome on HDMI 2 device: `chrome --kiosk
      --autoplay-policy=no-user-gesture-required http://<laptop>:8000/display`
- [ ] Bar runbook section in README (network fix, pairing, daily use)

## Open decisions

- TV network path (Phase 0) — blocked on Ted's info.
- Gemini API key for AI commercials (Phase 3).
- Electron: only if the browser flow annoys Ted in daily use.

## Status log

- 2026-11-07 — Laptop clone + setup OK (Python 3.12.4). TV unreachable:
  "Drink" Wi-Fi client isolation suspected; full /24 scan for webOS
  ports found nothing. Demo media generated (5 labeled clips + flower)
  and pushed to laptop. PLAN.md created. Programs engine spec agreed.
