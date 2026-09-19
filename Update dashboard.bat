@echo off
setlocal
cd /d "%~dp0"
echo.
echo  Rebuilding the health dashboard from every w_* folder here...
echo.

rem Optional: point this at a Google Drive for Desktop folder and data.json is
rem copied there on every build. Left off on purpose - on this machine Drive for
rem Desktop has the whole Desktop (~235k files) queued for upload, so anything
rem dropped in G: sits in that backlog for hours instead of reaching the cloud.
rem Upload data.json from drive.google.com in the browser instead. Re-enable
rem this line only once Drive's queue is actually healthy.
rem set HEALTH_DRIVE_DIR=G:\Il mio Drive\health-dashboard

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
