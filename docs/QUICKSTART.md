# Quick Start — API-only branch

These steps apply to the `feat/http-api-transport` checkout. They do not update or deploy an existing production instance automatically.

## Prerequisites
- Docker Desktop using Linux containers (Windows), or Docker Engine + Compose plugin (Linux)
- A checkout of this branch
- Tonies account credentials for the app's setup flow

## 1) Create `.env`

Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

Linux:
```bash
cp .env.example .env
```

No Tonies credentials need to be added to `.env` for normal setup. The app stores account credentials in its encrypted local vault. Legacy `TONIES_EMAIL` / `TONIES_PASSWORD` environment configuration remains supported and takes precedence over that vault.

## 2) Start with Docker Compose (recommended)

Run from the branch checkout on either platform:
```bash
docker compose up -d --build
```

Open:
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`

Compose maps host port 8090 to container port 8080 and mounts the relative host directory `./data` at `/app/data`. No host networking or Linux-only host path is required. The lean Python 3.12 image includes ffmpeg, curl, unzip, CA certificates and Deno; yt-dlp remains installed/upgraded for audio extraction. No Playwright/browser install step is required.

## 3) First use
1. Open the app.
2. Complete setup/login and enter Tonies credentials if prompted.
3. Select a Creative Tonie.
4. Search YouTube, drag a Local MP3 Library file onto a Tonie, or use **Browse** to import local audio first.

Tonies login uses direct OAuth requests. Access/refresh tokens and renewal state stay in process memory; no browser-state file is written. Restarting loses those tokens and the vault's unlocked state, but not the saved encrypted account credentials. Unlock again when prompted; the next operation authenticates using the available credentials.

`./data` contains downloads, logs, encrypted vault files and app settings. Keep it private, back it up and do not commit it.

## Native setup (without Docker)

Use Python 3.12, with **ffmpeg and Deno on PATH**. Use this branch's checkout rather than the legacy Windows bootstrap script: `scripts/setup-windows.ps1` targets/reset-checks out `main`. Browser installation has been removed from the script, but it must not be used to switch an evaluation checkout back to another branch.

Linux:
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --upgrade yt-dlp
export DATA_DIR=./data
export LOG_FILE=./data/logs/toni-auto.log
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Windows PowerShell:
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
$env:DATA_DIR = "./data"
$env:LOG_FILE = "./data/logs/toni-auto.log"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Native runs use exported environment variables; Compose loads `.env` through its `env_file`. Do not reuse `/app/data` paths from the container example on a native host.

## Stop / restart
```bash
docker compose stop tonies-yt
docker compose up -d
```

After changing source/build inputs, use `docker compose up -d --build`. Run these only in the intended checkout/project; do not rebuild a production instance to test this branch.

## Logs
- UI: `http://localhost:8090/logs`
- File with the supplied `.env.example`: `./data/logs/toni-auto.log`
- Container: `docker compose logs --tail 100 tonies-yt`

## Fast fixes

### Port 8090 already in use
Change only the host side of the mapping in your Compose configuration, for example `"8091:8080"`. Uvicorn's container command remains on 8080; changing `APP_PORT` alone does not rewrite that command.

### Tonies authentication issues
Check/unlock the app vault and confirm the account credentials in Settings. Environment credentials override the vault if both variables are set. Restarting the intended app service clears in-memory tokens:
```bash
docker compose restart tonies-yt
```

Do not remove vault files or the data directory. Old browser selectors and `TONIES_STORAGE_STATE_FILE` no longer configure authentication; removing `tonies-storage-state.json` is not an API-token reset.
