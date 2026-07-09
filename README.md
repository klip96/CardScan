# Business Card Scanning Agent

A local web service that turns a photo of a paper business card into a row in
a Google Sheet. A field employee at a trade show photographs a card with
their phone's browser — the service recognizes the data, optionally enriches
it from the web, and appends the lead to the sheet. Everything runs on your
PC; only requests to the selected recognizer and the web search go to the
cloud.

## Features

- Mobile capture page (opens in the phone's browser, no app installation needed).
- Upload an already-taken photo from the gallery/files, if the card was photographed in advance.
- Installable as a phone app (PWA): home-screen icon, full-screen launch.
- Automatic phone-to-PC connection over the network — no manual IP entry.
- Card recognition via one of several engines:
  - cloud LLMs: Gemini / OpenAI / Claude (accurate, needs an API key and internet);
  - local OCR (Tesseract) — offline, free, light on the PC;
  - local vision model via Ollama (offline, more demanding on hardware).
- Optional data enrichment from the web (exact company name, address, industry, website).
- Automatic lead logging to Google Sheets with a dropdown list of responsible managers.
- Queue-based processing: an employee can photograph cards back-to-back without waiting for each one to finish recognizing.

## Project structure

```
Agent_LOCAL/
├─ app/
│  ├─ main.py              # FastAPI application (entry point: app.main:app)
│  ├─ config.py            # loads config.yaml
│  ├─ pipeline.py          # job queue and processing pipeline
│  ├─ enrich.py            # data enrichment from the web
│  ├─ sheets.py            # writes to Google Sheets
│  ├─ recognizers/
│  │  ├─ base.py           # CardData / Recognizer contracts
│  │  ├─ gemini.py         # cloud:gemini
│  │  ├─ openai.py         # cloud:openai
│  │  ├─ claude.py         # cloud:claude
│  │  ├─ local_ocr.py      # local-ocr  (Tesseract)
│  │  └─ local_vision.py   # local-vision (Ollama)
│  ├─ discovery.py         # publishes the server on the network via mDNS (cardscan.local)
│  └─ web/
│     ├─ index.html        # mobile capture page
│     ├─ connect.html      # connection page with a QR code (on the PC)
│     ├─ manifest.webmanifest  # PWA manifest (icons, name, theme)
│     ├─ sw.js             # service worker for PWA installation
│     ├─ icon-192.png      # app icon 192×192
│     ├─ icon-512.png      # app icon 512×512
│     └─ apple-touch-icon.png  # icon for iOS "Add to Home Screen"
├─ data/photos/            # saved card photos (created automatically)
├─ ollama_setup/           # local vision model installation
│  ├─ install_and_pull.ps1
│  └─ install_and_pull.sh
├─ installer/              # Windows installer build
│  ├─ build_all.bat        # build the exe + installer in one step
│  ├─ installer.iss        # Inno Setup
│  └─ build_exe.spec       # PyInstaller
├─ config.example.yaml     # configuration template
├─ config.yaml             # your config (created from the template, not tracked in git)
├─ service_account.json    # Google key (not tracked in git)
├─ requirements.txt
├─ run.bat                 # run on Windows
└─ run.sh                  # run on macOS/Linux
```

## Requirements

- Python 3.9 or newer.
- OS: Windows 10/11, macOS, or Linux.
- Internet — only needed for cloud engines and enrichment (not needed for `local-ocr`).
- For writing to Google Sheets — a Google account and access to Google Cloud Console.
- For `local-vision` — Ollama installed (see the section below).
- For `local-ocr` — the Tesseract system binary with the `rus` and `eng` language packs.

## Installation

1. Open a terminal in the project folder.

2. Create and activate a virtual environment:

   Windows (PowerShell or cmd):
   ```
   python -m venv venv
   venv\Scripts\activate
   ```

   macOS / Linux:
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install the dependencies:
   ```
   pip install -r requirements.txt
   ```

   You don't need to install all the cloud packages (`google-genai`, `openai`,
   `anthropic`) — only the one matching your chosen engine. Importing the
   project doesn't fail if an optional package is missing; the error only
   shows up when you actually try to use that engine.

4. Copy the configuration template and edit it:

   Windows:
   ```
   copy config.example.yaml config.yaml
   ```
   macOS / Linux:
   ```
   cp config.example.yaml config.yaml
   ```

   Open `config.yaml` and fill in the fields you need (engine, API key, Google Sheets).

## Getting an API key

Only needed for the cloud engines (`cloud:gemini`, `cloud:openai`, `cloud:claude`).
Fill in the key in `config.yaml` under the matching `cloud:` section, or via the settings UI.

- Gemini: https://aistudio.google.com/apikey — create a key, paste it into
  `cloud.gemini.api_key`.
- OpenAI: https://platform.openai.com/api-keys — create a key, paste it into
  `cloud.openai.api_key`.
- Claude (Anthropic): https://console.anthropic.com/ — the API Keys section, paste it
  into `cloud.claude.api_key`.

Keys are stored locally in `config.yaml`. The web UI shows them masked
(`••••XXXX`).

## Setting up Google Sheets

> **The easiest way is through the app's UI.** Start the app and open
> **http://localhost:8000/setup** on the PC (or the "⚙ Settings" link in the header).
> There you can: upload the key file, see the service account's email (which
> needs access to the spreadsheet) with a "Copy" button, paste the
> spreadsheet link, and click **"Test connection"** — no need to manually edit
> `config.yaml`. Below is what you need to prepare in Google Cloud first
> (steps 1-4) and how to grant access.

Leads are written under a Google service account. Steps:

1. Open https://console.cloud.google.com/ and create a new project
   (or select an existing one).
2. Enable the **Google Sheets API**: "APIs & Services" menu → "Library" →
   search for "Google Sheets API" → "Enable". (If you want spreadsheets to be
   auto-created, also enable "Google Drive API".)
3. Create a service account: "APIs & Services" → "Credentials" →
   "Create credentials" → "Service account". Any name works, roles are optional.
4. Create a key for the service account: on the account page, the "Keys" tab →
   "Add key" → "Create new key" → type **JSON** → "Create". A JSON file downloads.
5. Rename the downloaded file to `service_account.json` and place it in the
   project root (next to `config.yaml`). The filename is configurable via
   `google_sheets.credentials_file`.
6. Open the JSON file and find the `client_email` field (looks like
   `name@project.iam.gserviceaccount.com`).
7. Create a Google Sheet. Click "Share" and grant that email **Editor**
   access. Without this the service can't write to the sheet.
8. Copy the `spreadsheet_id` from the sheet's URL. In the address
   `https://docs.google.com/spreadsheets/d/<THIS_IS_THE_ID>/edit#gid=0`,
   the identifier is the part between `/d/` and `/edit`. Paste it into
   `google_sheets.spreadsheet_id`.
9. Optionally set the worksheet name in `google_sheets.worksheet` (defaults to "Лиды").

If you don't need Sheets writing yet, set `google_sheets.enabled: false`.

## Running

Windows — double-click `run.bat` (or from a terminal):
```
run.bat
```

macOS / Linux:
```
chmod +x run.sh    # once
./run.sh
```

The scripts create a venv if needed, install dependencies, start the server
`uvicorn app.main:app --host 0.0.0.0 --port 8000`, and open a browser at
http://localhost:8000.

Stop the server with Ctrl+C in the terminal.

## Connecting from a phone

1. The PC and phone must be on the same Wi-Fi network.
2. Find the PC's IP address:
   - Windows: `ipconfig` → the "IPv4 Address" field (e.g. `192.168.1.42`);
   - macOS: "System Settings" → "Network", or `ipconfig getifaddr en0`;
   - Linux: `hostname -I` or `ip addr`.
3. On the phone, open `http://<pc-ip>:8000` in the browser
   (e.g. `http://192.168.1.42:8000`).
4. If the page doesn't load — check that the PC's firewall allows
   incoming connections on port 8000.

This is the manual-IP fallback method. It's usually easier to use
auto-connect at `cardscan.local` or the QR code — see the "Automatic
phone-to-PC connection" section below.

## Uploading an existing photo

You don't have to take the photo inside the app. Besides the camera-capture
button, the page has a separate button for picking an already-taken photo
from the phone's gallery or files.

This is handy when:

- the card was photographed in advance (e.g. at a trade show without internet);
- you need to upload a scan or a photo received via messenger;
- the phone's camera doesn't open from the browser.

A picked photo goes into the same recognition queue as camera shots: the
data is recognized and appended to the Google Sheet the same way as a
regular capture.

## Installing as a phone app (PWA)

The capture page can be installed on the phone as a regular app — no app
stores needed. This is a web app (PWA): after installation, a home-screen
icon appears, and the page itself launches full-screen, without the
browser's address bar.

How to install:

1. On the phone, open the PC's address in the browser — `http://cardscan.local:8000`
   (see the auto-connect section below) or `http://<pc-ip>:8000`.
2. In the browser menu, choose **"Add to Home Screen"**:
   - Android (Chrome): "⋮" menu → "Install app" / "Add to Home screen";
   - iPhone (Safari): "Share" button → "Add to Home Screen".
3. Confirm the addition. A "Card Scanner" icon appears on the home screen.

After installation, launch the app straight from the icon — it will open
the capture page full-screen on its own.

The app's icon, name, and theme are set by `app/web/manifest.webmanifest`;
the service worker `app/web/sw.js` handles installation. No further
configuration is needed.

## Automatic phone-to-PC connection

So you don't have to type the IP address by hand, the server publishes
itself on the local network via mDNS (Bonjour/Zeroconf) under the name
**`cardscan.local`**. A phone on the same Wi-Fi network simply opens in
its browser:

```
http://cardscan.local:8000
```

The address is stable and doesn't depend on whatever IP the router
assigned — no configuration needed. The PWA loads from the PC itself, so
all requests (capture, queue, photo upload) automatically go to your PC;
there's no need to enter a server address in the app.

### Fallback — QR code

If the phone can't open `cardscan.local` (or it's just faster this way),
open the connection page on the PC:

```
http://localhost:8000/connect
```

It shows a QR code linking to the server. Point the phone's camera at it
and follow the link — the same capture page opens.

### Requirements and limitations

- The PC and phone must be on the **same Wi-Fi network**.
- The `cardscan.local` name works out of the box on modern Android and iOS.
- Corporate and guest networks with **client isolation** (AP isolation)
  enabled block device-to-device communication and mDNS. In that case, use
  a regular home Wi-Fi network, or connect via a direct IP address or the
  QR code.

## Remote access over the internet (optional)

Everything above requires the phone and PC to share a Wi-Fi network. If
field reps need to log in from anywhere with internet access instead — a
different office, mobile data, home — the server can be exposed through
[ngrok](https://ngrok.com), a free tunneling service that gives it a stable
public HTTPS address without touching your router (no port forwarding, no
static IP, works behind CGNAT).

Setup (one-time):

1. Sign up at ngrok.com (free, no credit card).
2. Install the client: `winget install Ngrok.Ngrok` (Windows) — see
   [ngrok's docs](https://ngrok.com/docs/getting-started/) for macOS/Linux.
3. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
   and run: `ngrok config add-authtoken <your token>`
4. Claim a free persistent domain at https://dashboard.ngrok.com/domains
   (something like `cardscan-office.ngrok-free.dev` — it won't change on restart).
5. Put that domain in `config.yaml` under `remote.ngrok_domain` (see
   `config.example.yaml` for the exact key). `run.bat` reads it from there
   and starts the tunnel automatically alongside the server; leave it blank
   to only use the local network as before.

**Before opening this up, create the first admin account locally** (open
`http://localhost:8000/login` on the PC itself while it's still LAN-only) —
otherwise anyone who finds the public URL before you do could claim the
first admin account. Once at least one account exists, this is no longer
possible (`/api/auth/bootstrap` only works while zero users exist).

Once the tunnel is running, its address and a QR code for it show up
automatically at `http://localhost:8000/connect`, right below the local
network's. Login attempts are rate-limited (10 per 15 minutes per IP) since
the login page is now reachable from the public internet, not just your LAN.

## Choosing a recognition engine: cloud vs local

The engine is set via the `recognizer` field in `config.yaml` or in the settings UI.

| Engine         | Internet | Cost      | Accuracy   | PC load |
|----------------|----------|-----------|------------|---------|
| `cloud:gemini` | required | low       | high       | minimal |
| `cloud:openai` | required | medium    | high       | minimal |
| `cloud:claude` | required | medium    | high       | minimal |
| `local-ocr`    | not needed | free    | medium     | low     |
| `local-vision` | not needed | free    | above OCR  | high    |

Recommendations:
- if you have internet and want the best result with minimal hassle — `cloud:gemini`;
- if full autonomy matters (no network / air-gapped) — `local-ocr` (lightweight) or
  `local-vision` (more accurate, but needs a capable PC).

## Local model and Ollama

The `local-vision` engine talks to a local [Ollama](https://ollama.com) server
over HTTP (`local.vision.ollama_host`, defaults to `http://localhost:11434`)
and uses a vision model (defaults to `moondream`).

Installing and pulling the model — via the scripts in `ollama_setup/`:

- Windows (PowerShell):
  ```
  powershell -ExecutionPolicy Bypass -File ollama_setup\install_and_pull.ps1
  ```
- macOS / Linux:
  ```
  chmod +x ollama_setup/install_and_pull.sh
  ./ollama_setup/install_and_pull.sh
  ```

The scripts check whether Ollama is installed, install it if not, pull the
`moondream` model, and start `ollama serve`. Alternative models (more
accurate but heavier): `qwen2.5-vl:3b`, `gemma3:4b` — set via `local.vision.model`.

The `local-ocr` engine uses Tesseract and doesn't need Ollama. Install the
system Tesseract binary along with the `rus` and `eng` language packs:
- Windows: the UB Mannheim installer (https://github.com/UB-Mannheim/tesseract/wiki);
- macOS: `brew install tesseract tesseract-lang`;
- Linux (Debian/Ubuntu): `sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng`.

## One-step installer build (Windows)

The simplest way to build a ready-to-use installer is to run a single
script. **Nothing needs to be installed beforehand** — the script checks
for and installs Python and Inno Setup itself if they're missing.

Just run (double-click):

```
installer\build_all.bat
```

What happens:

1. `build_all.bat` launches `build_all.ps1` (PowerShell).
2. The script checks for **Python** and **Inno Setup 6**. If they're missing, it
   installs them automatically: first it tries **winget** (built into Windows
   10/11), and if that's unavailable, it downloads the official installers and
   installs them silently. Installation will trigger an administrator
   (**UAC**) prompt — approve it.
3. It creates the environment, installs dependencies, builds the `.exe`
   (PyInstaller), and compiles the installer (Inno Setup).

The only requirement is **internet access** during the build (for installing
components and dependencies). The finished installer file appears in the
**`installer\Output\`** folder.

> If auto-install didn't work (no internet / corporate restrictions) —
> install **Python 3.9+** manually (https://www.python.org/downloads/, check
> "Add Python to PATH") and **Inno Setup 6** (https://jrsoftware.org/isdl.php),
> then run `build_all.bat` again.

If you need to run the steps manually, or want to understand what happens
under the hood — see the section below.

## Building the Windows installer manually

To distribute to a PC without Python, you can build a single `.exe` and an
installer. The templates live in `installer/`.

1. Build the executable via PyInstaller:
   ```
   pip install pyinstaller
   pyinstaller installer\build_exe.spec
   ```
   The result appears in `dist/` (with the `app/web` folder bundled in).

2. Build the installer via [Inno Setup](https://jrsoftware.org/isinfo.php):
   open `installer\installer.iss` in the Inno Setup Compiler and click
   Compile, or from the console:
   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
   ```
   The installer will install the exe, create a shortcut, and may invoke
   `ollama_setup` on first run for the local model.

Double-check the placeholders in both files (paths, app name, version)
before building — they're marked with comments.
