# Tonies-YT

Fast web app for managing Creative Tonies audio.

![Tonies-YT app screenshot](docs/assets/app-screenshot.png)

## What it does
- Search YouTube, pick a result and download audio
- Upload audio to Creative Tonies through the Tonies cloud API
- Manage Tonies tracks: rename, reorder, delete, **delete all content with confirmation**
- Maintain a Local MP3 Library for drag/drop or upload-to-Tonies flows
- Queue multiple local uploads sequentially, with queued-state highlighting
- Import local audio using **Browse**; it is converted to a Tonies-friendly MP3 once, on import
- Right-click local files on desktop for upload / upload to top / upload to bottom

## How it talks to Tonies

The app calls the Tonies cloud directly over HTTPS (`httpx`): OAuth login at `login.tonies.com`, then the same API the my.tonies.com web app uses. There is no browser, Playwright or Chromium involved.

- Every change (upload, rename, reorder, delete) re-reads the Tonie first, writes the full chapter list by exact chapter ID, then reads it back. If the Tonie changed in between, the app refuses instead of guessing.
- A write whose outcome is unknown (for example a timeout) is never retried automatically; the app asks you to refresh first.
- Uploads go straight to Tonies' storage with a presigned form; your login token is never sent there.
- OAuth tokens live in memory only and renew automatically. After a restart you unlock the app again and the next Tonies action signs in fresh.

## Main UI
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`
- Logs: `http://localhost:8090/logs`
- Settings: `http://localhost:8090/settings`

## Core features
- Realtime job updates with SSE + fallback polling
- Configurable YouTube result count (5 / 10 / 15 / 20)
- Local vault/login flow for app access
- Tonies credential management in Settings
- Local file uploads + YouTube downloads
- Built-in logs viewer + export

## Stack
- FastAPI + `httpx` for the Tonies OAuth/API client (`app/tonies_api.py`)
- `cryptography` for the encrypted app vault
- `yt-dlp`, `ffmpeg` and Deno for YouTube/audio processing (Deno is yt-dlp's JavaScript runtime)
- `python:3.12-slim` container (about 1.1 GB) + Docker Compose

## Quick start (recommended)

Docker Compose on Linux, or Docker Desktop with Linux containers on Windows.

Linux:
```bash
cp .env.example .env
docker compose up -d --build
```

Windows PowerShell:
```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Open `http://localhost:8090` and complete first-run setup: choose an app username and password, then enter your Tonies email and password. They are stored encrypted in `data/secrets/`; nothing needs to go in `.env`.

Compose maps host port 8090 to container port 8080 and mounts `./data` at `/app/data`. Back up `data/` and `.env`; do not commit either.

## Native setup (without Docker)

See [Quick Start](docs/QUICKSTART.md) for Python 3.12 instructions on Linux and Windows. Install ffmpeg and Deno on the host, then install `requirements.txt`.

On Windows, `scripts/setup-windows.ps1` installs the prerequisites and checks out `main`. It runs `git reset --hard origin/main`, so do not run it in a checkout with local changes.

## Optional config
- `LOG_LEVEL`
- `LOG_FILE` (the Compose example uses `/app/data/logs/toni-auto.log`)
- `DATA_DIR` (`/app/data` under Compose; use `./data` for native runs)
- `TONIES_EMAIL` / `TONIES_PASSWORD`: when both are set they override the vault and the app needs no unlock. Keep them private.
- `TONIES_CREATIVE_UPLOAD_URL` or `TONIES_CHARACTER_NAME`: default target Tonie (edit URL or name) when a job doesn't pick one.

## Upgrading from the browser-automation version

Earlier versions drove my.tonies.com with Playwright. When upgrading:
- Keep your `data/` directory; the vault, job history and library carry over.
- `TONIES_STORAGE_STATE_FILE`, `TONIES_APP_URL`, `TONIES_LOGIN_URL` and the `TONIES_*_SELECTOR` settings are ignored and can be removed from `.env`.
- `data/tonies-storage-state.json` is no longer read. It holds an old browser session, so delete it or keep it private.

## Forgot the app password?

It cannot be recovered: the vault is encrypted with it. To start over, stop the app, move `data/secrets/vault-meta.json` and `data/secrets/vault-credentials.enc` somewhere safe, start the app and complete first-run setup again. Job history and the local library are unaffected. See [Operations](docs/OPERATIONS.md#reset-a-forgotten-app-password).

## Docs
- Quick start: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- App overview: [`docs/APP_OVERVIEW.md`](docs/APP_OVERVIEW.md)
- Operations: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md)
