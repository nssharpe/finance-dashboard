@echo off
REM ----------------------------------------------------------------------
REM  Finance Dashboard - local launcher
REM  Starts the FastAPI app on http://127.0.0.1:8765 and opens the browser.
REM ----------------------------------------------------------------------
cd /d "%~dp0"

REM Install dependencies on first run (fast no-op once satisfied).
python -c "import fastapi, uvicorn, openpyxl" 2>nul || python -m pip install -r requirements.txt

REM Open the browser a few seconds after launch (give the server time to bind).
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8765"

REM Run the server (blocks until you close this window / Ctrl+C).
python -m app.main
