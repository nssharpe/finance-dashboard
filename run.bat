@echo off
REM ----------------------------------------------------------------------
REM  Finance Dashboard - local launcher
REM  Starts the FastAPI app on http://127.0.0.1:8765 and opens the browser.
REM ----------------------------------------------------------------------
cd /d "%~dp0"

set "URL=http://127.0.0.1:8765"

REM Already running? Pinned to the taskbar this gets clicked more than once,
REM and a second server is a silent failure rather than a loud one:
REM Windows lets both processes bind 8765 without erroring, so you end up with
REM two uvicorns on one port and no message saying so. If the dashboard already
REM answers, just surface it and stop.
REM   --noproxy: a stray http_proxy would route the loopback probe through it
REM   and make a live server look dead, which is the case we're guarding.
REM   No curl (pre-1803 Windows) gives errorlevel 9009 and falls through to a
REM   normal start, which is the right default.
"%SystemRoot%\System32\curl.exe" -s -o nul --noproxy "*" --max-time 2 "%URL%/" 2>nul
if not errorlevel 1 (
    start "" "%URL%"
    exit /b 0
)

REM Install dependencies on first run (fast no-op once satisfied).
python -c "import fastapi, uvicorn, openpyxl" 2>nul || python -m pip install -r requirements.txt

REM Open the browser a few seconds after launch (give the server time to bind).
start "" /min cmd /c "timeout /t 3 >nul & start "" %URL%"

REM Run the server (blocks until you close this window / Ctrl+C).
python -m app.main
