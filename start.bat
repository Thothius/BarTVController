@echo off
REM BarTVController - start the server and open the dashboard
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
  echo .venv missing - run setup.bat first
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
if not exist config.json copy config.example.json config.json >nul

REM Open the dashboard once the server answers (instead of guessing a delay).
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://localhost:8000/api/status ^| Out-Null; Start-Process http://localhost:8000/; break}catch{Start-Sleep -Milliseconds 500}}"
python -m bartv.run 8000
pause
