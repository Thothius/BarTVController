# Demo script — laptop mock mode (LGTestTV)

Everything runs on the laptop; no real TV needed. Mock TV = **LGTestTV**.

## Start

```
cd C:\Users\Administrator\BarTVController
start.bat
```

Open http://localhost:8000/ — PIN **0000** (demo config).

## The 2-minute demo

1. **TV frame on home** — title bar says LGTestTV, preview screen inside the
   bezel shows exactly what the kiosk would show. State chip top-right:
   `TV input` / `Program · MMA Night` / `AD break`.
2. **Active program** — Programs → "MMA Night" is active: playlist
   content_mma1 → content_mma2 looping, commercials pool ad_weekend + ad_drinks
   at 6/hour, RNG never repeats the same ad twice in a row.
3. **Instant ad** — home → "Play ad break now". TV switches to signage input,
   a random pool commercial plays, then the playlist resumes where it left off.
4. **Schedule** — commercials fire automatically per `ads_per_hour`; top bar
   shows "Next ad HH:MM". Pause/resume auto ads from Settings or the home toggle.
5. **Assets** — Assets view: upload a video, thumbnail auto-generates, rename,
   delete. Programs reference assets by file; deleting an asset that's in use
   shows "Missing" in the program editor until removed.
6. **TV base vs content base** — create a second program with base "Regular TV":
   the live TV input is the base layer; commercials interrupt then switch back.

## What to try breaking (edge cases, all handled)

- Spam "Play ad break now" during an ad → refused ("Ad break already running").
- Set ads_per_hour = 0 → auto commercials stop; instant trigger still works.
- Delete the active program → engine falls back to Default; Default has no
  commercials → legacy promo.mp4 break on `ad_interval_minutes` cadence.
- Close the preview/kiosk mid-clip → server advances on timeout; reconnect →
  the kiosk catches up via snapshot replay.
- Mock mode toggle (Settings) drops pairing instantly for testing pair flows.

## Kiosk page

http://localhost:8000/display — fullscreen, click once for sound. This is what
HDMI 2 shows in the bar. Preview variant: `/display?muted=1&preview=1`
(watch-only, never advances the playlist).
