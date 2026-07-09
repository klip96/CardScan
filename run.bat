@echo off
REM ============================================================
REM  CardScan launcher for Windows.
REM  Creates a venv if missing, installs dependencies, starts the
REM  server, and opens a browser at http://localhost:8000
REM
REM  NOTE: this file must stay plain ASCII (no Cyrillic). cmd.exe
REM  reads BOM-less .bat files in the legacy OEM codepage; mixing
REM  that with UTF-8 multibyte text makes it lose its parse
REM  position and try to run garbled byte fragments as commands.
REM  Put any localized/Cyrillic messages in a .ps1 instead.
REM ============================================================
setlocal

REM Move to the script's folder (project root)
cd /d "%~dp0"

REM --- Virtual environment ---
if exist "venv\Scripts\activate.bat" (
    echo [info] Activating venv...
    call "venv\Scripts\activate.bat"
) else (
    echo [info] venv not found, creating...
    python -m venv venv
    if errorlevel 1 (
        echo [error] Could not create venv. Is Python installed and on PATH?
        pause
        exit /b 1
    )
    call "venv\Scripts\activate.bat"
    echo [info] Installing dependencies...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

REM --- Config ---
if not exist "config.yaml" (
    echo [warn] config.yaml not found. Copying from config.example.yaml
    copy /Y "config.example.yaml" "config.yaml" >nul
    echo [warn] Open config.yaml and fill in settings - engine, keys, Google Sheets.
)

REM --- Remote access via ngrok (optional) ---
REM Domain is read from config.yaml (remote.ngrok_domain) - see config.example.yaml
REM for ngrok setup notes. Leave empty to use LAN only.
set NGROK_DOMAIN=
for /f "usebackq delims=" %%i in (`python -m app.print_config remote.ngrok_domain 2^>nul`) do set NGROK_DOMAIN=%%i

if not "%NGROK_DOMAIN%"=="" (
    where ngrok >nul 2>nul
    if errorlevel 1 (
        echo [warn] ngrok not found in PATH - remote access not started.
        echo [warn] Install: winget install Ngrok.Ngrok, then ngrok config add-authtoken YOUR_TOKEN
    ) else (
        echo [info] Starting remote tunnel: https://%NGROK_DOMAIN%
        start "CardScan - remote access" ngrok http --url=%NGROK_DOMAIN% 8000
    )
)

REM --- Open browser (server will be up in a couple seconds) ---
start "" "http://localhost:8000"

REM --- Start server ---
echo [info] Starting server at http://0.0.0.0:8000  (Ctrl+C to stop)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

endlocal
