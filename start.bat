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

timeout /t 2 /nobreak >nul
start "" http://localhost:8000/
python -m bartv.run 8000
pause
