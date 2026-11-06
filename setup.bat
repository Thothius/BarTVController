@echo off
REM BarTVController - one-time setup (run once after git clone / pull)
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>&1 && set PY=py -3
if "%PY%"=="" (where python >nul 2>&1 && set PY=python)
if "%PY%"=="" (
  echo Python 3 not found. Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" during install, then re-run this file.
  pause
  exit /b 1
)

%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Python found but not working. Reinstall with "Add python.exe to PATH" checked.
  pause
  exit /b 1
)

if not exist .venv %PY% -m venv .venv
if errorlevel 1 (
  echo Failed to create .venv
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed
  pause
  exit /b 1
)

if not exist config.json copy config.example.json config.json >nul

echo.
echo Setup complete. Set tv_ip in config.json, then run start.bat
echo.
pause
