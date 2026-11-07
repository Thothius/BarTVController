@echo off
REM BarTVController - open the signage screen fullscreen (run on the PC wired to the TV's signage HDMI)
REM Usage: kiosk.bat            -> fullscreen on the main monitor
REM        kiosk.bat 1920       -> fullscreen on a monitor that starts at x=1920 (TV as 2nd screen)
REM Exit the kiosk with Alt+F4.
setlocal
set URL=http://localhost:8000/display
set POS=0
if not "%~1"=="" set POS=%~1

set BROWSER=
for %%B in (
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if exist %%B if not defined BROWSER set BROWSER=%%B

if not defined BROWSER (
  echo Edge or Chrome not found - open %URL% in any browser and press F11.
  pause
  exit /b 1
)

REM Separate profile so the kiosk never merges into an open browser window;
REM autoplay flag lets ads play with sound without a click.
start "" %BROWSER% --kiosk %URL% --edge-kiosk-type=fullscreen --no-first-run ^
  --autoplay-policy=no-user-gesture-required --user-data-dir="%LocalAppData%\BarTVKiosk" ^
  --window-position=%POS%,0
