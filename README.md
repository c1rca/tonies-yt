# Tonies-YT

Fast web app for managing Creative Tonies audio.

> **Unofficial.** Tonies-YT is a hobby project, not affiliated with or endorsed by tonies GmbH. It uses the same private web API as my.tonies.com, which can change without notice and break the app. "Tonies", "Toniebox" and "Creative-Tonie" are trademarks of tonies GmbH. Only upload audio you have the right to use.

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
- OAuth tokens live in memory only and renew automatically. After a restart you sign in to the app again and the next Tonies action signs in to Tonies fresh.

## Security model

- Each browser signs in with the app username and password and gets its own session cookie (HttpOnly, SameSite=Strict). Every page and API call needs it, except sign-in, first-run setup and `/api/health`.
- **Lock** (top bar) signs every browser out and locks the vault. Changing the app password signs other browsers out. A restart signs everyone out.
- Five wrong passwords from one address within five minutes trigger a wait. Writes sent from other websites are refused.
- Tonies credentials are stored encrypted with a key derived from the app password; they are only decrypted in memory after sign-in.
- The app speaks plain HTTP. On a home network that is the usual trade-off; **to reach it from the internet, put it behind an HTTPS reverse proxy** (see [Operations](docs/OPERATIONS.md#running-behind-a-reverse-proxy)) and use a strong app password. Until first-run setup is done, whoever opens the app first creates the login, so set it up right after starting it.

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

Open `http://localhost:8090` and complete first-run setup: choose an app username and password, then enter your Tonies email and password. They are stored encrypted in `data/secrets/`; nothing needs to go in `.env`. Each browser then signs in with the app username and password.

Compose maps host port 8090 to container port 8080 and mounts `./data` at `/app/data`. Back up `data/` and `.env`; do not commit either.

## Native setup (without Docker)

See [Quick Start](docs/QUICKSTART.md) for Python 3.12 instructions on Linux and Windows. Install ffmpeg and Deno on the host, then install `requirements.txt`.

On Windows, `scripts/setup-windows.ps1` installs the prerequisites and checks out `main`. It runs `git reset --hard origin/main`, so do not run it in a checkout with local changes.

## Optional config
- `LOG_LEVEL`
- `LOG_FILE` (the Compose example uses `/app/data/logs/toni-auto.log`)
- `DATA_DIR` (`/app/data` under Compose; use `./data` for native runs)
- `TONIES_EMAIL` / `TONIES_PASSWORD`: when both are set they choose the Tonies account instead of the one saved in the vault. You still create and use an app login. Keep them private.
- `TONIES_CREATIVE_UPLOAD_URL` or `TONIES_CHARACTER_NAME`: default target Tonie (edit URL or name) when a job doesn't pick one.

## Upgrading from 1.x

Version 1.x drove my.tonies.com with Playwright. See the [changelog](CHANGELOG.md) for everything that changed. When upgrading:
- Keep your `data/` directory; the vault, job history and library carry over, and you sign in with your existing app login.
- If you used `TONIES_EMAIL` / `TONIES_PASSWORD` instead of the vault, the first visit now asks you to create an app login (the Tonies fields can stay empty).
- Scripts that call the API must sign in first (`POST /api/setup/login`) and send the session cookie back.
- `TONIES_STORAGE_STATE_FILE`, `TONIES_APP_URL`, `TONIES_LOGIN_URL` and the `TONIES_*_SELECTOR` settings are ignored and can be removed from `.env`.
- `data/tonies-storage-state.json` is no longer read. It holds an old browser session, so delete it or keep it private.

## Forgot the app password?

It cannot be recovered: the vault is encrypted with it. To start over, stop the app, move `data/secrets/vault-meta.json` and `data/secrets/vault-credentials.enc` somewhere safe, start the app and complete first-run setup again. Job history and the local library are unaffected. See [Operations](docs/OPERATIONS.md#reset-a-forgotten-app-password).

## Docs
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Quick start: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- App overview: [`docs/APP_OVERVIEW.md`](docs/APP_OVERVIEW.md)
- Operations: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md)
