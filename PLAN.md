# BarTVController — Plan & Status

Living document. Update the Status section on every meaningful change.

## Goal

Bar signage + ad-break controller for the LG webOS TV (UQ75003LF):
staff control it from a phone browser; programmed "commercial breaks"
interrupt the shown content on a schedule; everything runs from the bar
laptop with one `start.bat`.

## Master plan (agreed 2026-11-07): laptop first, real TV last

Everything is built and polished on the bar laptop against the mock TV
**LGTestTV** — interface, media, programs, commercials, scheduling —
before any real hardware is touched. The real LG TV enters only at the
end, once the laptop side is demo-ready and the network path is solved.

1. **Laptop-only build & polish (ACTIVE)** — mock LGTestTV:
   - [x] Desktop dashboard: top bar, ASSETS / PROGRAMS / Settings views,
         assets CRUD + thumbnails, program wrappers, multi-TV settings
   - [ ] Phase 2 programs engine: full "commercial running plan" demoable
         on the laptop (Default = TV base + commercials 2–3x/hour; content
         programs with playlists; RNG pool, no immediate repeat; instant
         ad; resume after ad)
   - [ ] Polish pass: edge cases, demo script, bar runbook
2. **Real TV bring-up (LAST)** — needs TV IP / staff SSID or laptop
   hotspot (Phase 0 network fix); then pair the real TV and re-run the
   same flows.
3. **Bar hardening** — autostart, kiosk Chrome on HDMI 2, runbook.

## What exists today (working, tested against mock TV)

- FastAPI backend (`bartv/`): dashboard `/`, kiosk `/display`, WebSocket
  event channel, PIN auth, volume/mute/input controls, ad break
  (`/api/trigger-ad`), media streaming (`/api/stream`).
- Desktop-first dashboard SPA (`static/index.html`): top bar with TV
  status pill + next-ad chip + screens count + settings gear; home view
  with ASSETS / PROGRAMS tiles and a live-control panel; full Assets
  view (upload with progress, drag-drop, search/filter, rename, delete,
  auto-thumbnails via canvas, show-on-TV); Programs view (list + editor:
  name, base TV/content, content & commercials pickers with reorder);
  Settings view (pair/re-pair/unpair with live countdown + cancel,
  multi-TV list with SSDP discovery + add-by-IP, demo mode toggle, ad
  schedule, kiosk URL + copy command, PIN forget).
- Asset library: flat files in `static/media/` + `assets.json` sidecar
  (name/thumb/duration); raw-body PUT upload with 1 GB cap, 409 replace
  flow, path-traversal hardened.
- Programs: `programs.json` store; `Default` (base tv) seeded; CRUD
  only for now — the playback engine is Phase 2.
- Multi-TV registry: `config.json` `tvs` list with per-TV client keys
  (keys never exposed by the API); switching TVs restores pairing;
  SSDP discovery for LG webOS TVs.
- APScheduler: ad break every `ad_interval_minutes` (clock-aligned),
  pause/resume toggle.
- Pairing handshake: settings "Pair" -> accept on TV remote -> client
  key saved per TV; cancellable; stale keys auto-cleared.
- Windows `setup.bat` / `start.bat`; `mock_tv: true` mode for
  hardware-free demo.
- 14 pytest tests pass. PR: https://github.com/Thothius/BarTVController/pull/1

## The "commercial running plan" (to build — Phase 2)

Programs + ad pool. Semantics agreed with Ted:

- A **program** is a named wrapper (already CRUD-able in the dashboard):
  - `base: "tv"` — normal TV input (HDMI 1 live feed) is the base layer;
    commercials interrupt it, then switch back to TV; or
  - `base: "content"` — plays `content[]` asset files (playlist,
    optionally shuffled/looping); commercials interrupt the current
    item, then playback **resumes where it left off**.
  - `commercials[]` — pool of ad asset files picked from per break.
- **Ad policy** per program, configurable:
  - cadence: every X minutes, or X ads per Y minutes (e.g. 3 per hour);
  - pool: list of commercial media files; pick uniformly at random,
    never the same ad twice in a row;
  - instant: dashboard "Play ad now" plays a random pool ad immediately
    and resumes the program after it ends.
- Example programs: `Default` (base tv, ~3 ads/hour), `MMA Package`
  (base content with MMA highlight clips + its own ad pool).
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
- [x] Laptop `config.json`: `mock_tv: true`, 2-min ad cadence for demo
- [x] Server running on laptop (`python -m bartv.run 8000`, detached);
      pair + manual ad break + scheduled ad verified via API
- [ ] Ted: open `http://localhost:8000/` (dashboard, PIN in config.json)
      and `http://localhost:8000/display` (kiosk) in browser tabs

### Phase 2 — Programs & ad pool engine

- [x] Program/asset data model + CRUD API + dashboard UI (wrappers only,
      no playback yet) — built ahead of schedule per Ted's UI spec
- [ ] Program runner: playlist playback on `/display` via WS events,
      ad policy scheduler (cadence + random pool, no immediate repeat),
      resume-after-ad, fallback to TV input when playlist ends
- [ ] Wire `active_program` selection + per-program instant-ad into the
      runner; legacy flat config keys stay for the single-ad fallback
- [ ] Tests for engine (mock TV, short durations)

### Phase 3 — Content pipeline

- [ ] `scripts/fetch.py`: yt-dlp wrapper (URL -> `static/media/`,
      optional trim/rename); `pip install yt-dlp` on laptop
- [x] AI commercial generation: DROPPED — Ted makes media himself

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
- Electron: only if the browser flow annoys Ted in daily use.

## Status log

- 2026-11-07 — Laptop clone + setup OK (Python 3.12.4). TV unreachable:
  "Drink" Wi-Fi client isolation suspected; full /24 scan for webOS
  ports found nothing. Demo media generated (5 labeled clips + flower)
  and copied to laptop `static/media/`. PLAN.md created. Programs engine
  spec agreed.
- 2026-11-07 (later) — Laptop server LIVE in mock mode on port 8000
  (PIN auto-generated into config.json). `promo.mp4` aliased to
  `ad_weekend.mp4` so the legacy ad break works. Verified: pair, manual
  trigger-ad, scheduled 2-min ad cycle, /api/files (7 files). Gemini
  dropped (Ted makes media).
- 2026-11-07 (UI round) — Desktop-first dashboard rebuilt per Ted's
  spec: top bar (TV status, next-ad, screens, gear), ASSETS + PROGRAMS
  home tiles, Assets view (upload/rename/delete/thumbs), Programs view
  (CRUD wrappers), Settings view (pair/cancel/unpair, multi-TV + SSDP
  discovery, demo mode, kiosk URL). Backend: asset/program JSON stores,
  upload streaming, pair task with cancel, TV registry, /api/tv/discover.
  Verified in browser (Playwright): all 4 views, PIN modal -> ad
  trigger, pairing countdown/cancel/complete, upload -> thumb ->
  delete. 14 tests pass. NEXT: push + laptop pull/restart, then Phase 2
  programs engine.
- 2026-11-07 (master plan) — Ted: run ALL development on the laptop
  first against mock TV **LGTestTV** (interface, media, commercials,
  scheduling), real TV last. Master plan section added; mock TV renamed
  from "Demo TV" to "LGTestTV" across the dashboard. NEXT: restart
  laptop server on new build, then Phase 2 programs engine.
- 2026-11-07 (Phase 2 engine) — Programs engine built: ProgramEngine
  (content playlist loop owning the signage input, commercial pool with
  RNG no-immediate-repeat, ads_per_hour cadence, instant ad, resume
  after ad, legacy promo fallback when no pool), 1-min scheduler tick,
  /api/programs + /api/programs/active endpoints, WS snapshot replay +
  ended events. Kiosk display.html v2: content/ad/media state machine,
  pause+resume across commercials, image timeouts, reconnect replay.
  Dashboard: commercials-per-hour control, Set-as-active button, active
  badges, program summaries show rate + ACTIVE. 21 tests pass (engine
  unit + API/WS). Browser-verified home/programs/kiosk. NEXT: push,
  laptop pull + restart, live demo of MMA Night program on LGTestTV.
- 2026-11-07 (TV frame) — Dashboard home now shows a stylized TV frame:
  title bar with the TV name (LGTestTV in mock mode) + "live preview",
  the real /display kiosk screen embedded muted inside the bezel, and a
  state chip (TV input / Program · name / AD break). Kiosk page gained
  ?muted=1&preview=1 params: preview mode never reports 'ended' events
  and loops content locally, so it cannot double-advance the playlist.
- 2026-11-07 (polish pass) — Edge-case sweep: fixed content playlist
  advancing during a commercial (engine now waits for the ad to finish
  before broadcasting the next content_start; regression test added).
  Docs: docs/DEMO.md (laptop mock-mode demo script) and
  docs/RUNBOOK.md (bar deployment: setup, HDMI 2 kiosk, pairing, daily
  ops, autostart, troubleshooting). 22 tests pass. PIN on laptop demo
  set to 0000 (local config). NEXT: Ted human-tests the demo, then
  Phase 2 leftovers (multi-display guard documented) and Phase 3
  YouTube import polish.
- 2026-11-07 (home session view) — Home rebuilt per Ted: big TV frame
  (LGTestTV title bar, "muted preview" badge, live kiosk preview, state
  chip) is now the main view with a "Now running" panel (active program,
  current clip, next ad) and a Switch-program quick picker. Assets and
  Programs moved to top-bar icons next to Settings; old tiles removed
  from home. Live control stays in the right rail. Static-only change.

- CageFiction startup channel + mock ad pool (3 rendered 15s slates: black bg, bold promo text); playlist shuffle per program + Skip clip control; live rail regrouped (Playback / TV input / Sound / Auto ads); 30 tests green
