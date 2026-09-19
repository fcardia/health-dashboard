@echo off
setlocal
cd /d "%~dp0"
echo.
echo  Rebuilding the health dashboard from every w_* folder here...
echo.

rem Optional: uncomment and point this at your Google Drive for Desktop folder.
rem data.json is copied there on every build, so the phone app can pick it up
rem straight from the Drive app. Leave it commented out to skip the copy.
rem set HEALTH_DRIVE_DIR=G:\My Drive\health

set PY=python
where python >nul 2>nul || set PY=py

%PY% "_build/build_dashboard.py"
if errorlevel 1 (
  echo.
  echo  Build failed. Check that Python 3 is installed and on your PATH.
  echo.
  pause
  exit /b 1
)

echo.
echo  Opening dashboard.html ...
start "" "dashboard.html"

rem Brief pause so you can read the summary above.
ping -n 4 127.0.0.1 >nul
