# Tonies-YT

Fast web app for managing Creative Tonies audio.

![Tonies-YT app screenshot](docs/assets/app-screenshot.png)

## API-only branch

This checkout (`feat/http-api-transport`) replaces Tonies browser automation with direct OAuth and HTTP API calls through `httpx`. It does not require Playwright, Chromium, browser cookies or a persisted browser session. This describes the branch, not an already-deployed production migration.

Tonies account credentials still come from the app's encrypted local vault (normal setup) or the existing `TONIES_EMAIL` / `TONIES_PASSWORD` environment variables. OAuth access/refresh tokens and renewal state live in memory, not in a browser-state file. After restart, unlock the app vault again when prompted; the next Tonies operation obtains fresh tokens using the available account credentials.

## What it does
- Search YouTube, pick a result and download audio
- Upload audio to Creative Tonies through the Tonies cloud API
- Manage Tonies tracks: rename, reorder, delete, **delete all content with confirmation**
- Maintain a Local MP3 Library for drag/drop or upload-to-Tonies flows
- Queue multiple local uploads sequentially, with queued-state highlighting
- Import local audio using **Browse** and normalize it to Tonies-friendly MP3
- Right-click local files on desktop for upload / upload to top / upload to bottom

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
- FastAPI + `httpx` for direct Tonies OAuth/API requests
- `cryptography` for the existing encrypted app vault
- `yt-dlp`, `ffmpeg` and Deno for YouTube/audio processing
- Lean `python:3.12-slim` container + Docker Compose

Deno remains required for yt-dlp's JavaScript extraction support. Removing the Tonies browser runtime does not remove the YouTube/audio tools or change the existing yt-dlp upgrade workflow.

## Quick start (recommended)

From this branch's checkout, use Docker Compose on Linux or Docker Desktop with Linux containers on Windows.

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

Open `http://localhost:8090`, then complete setup/login. Normal setup stores Tonies credentials in the app vault; no credentials need to be added to `.env`.

Compose keeps its existing `8090:8080` port mapping and relative `./data:/app/data` mount. Back up `data/` and `.env`; do not commit either. Container Uvicorn still listens on port 8080.

## Native setup (without Docker)

See [Quick Start](docs/QUICKSTART.md) for Python 3.12 instructions on Linux and Windows. Install ffmpeg and Deno on the host, then install `requirements.txt`; no browser installation is needed.

**Do not use the legacy `scripts/setup-windows.ps1` to evaluate this branch.** It targets `main` and resets its checkout; browser installation has been removed, but it would still switch an evaluation checkout away from this branch. Use Compose or the manual native steps instead.

## Optional config
- `LOG_LEVEL`
- `LOG_FILE` (the Compose example uses `/app/data/logs/toni-auto.log`)
- `DATA_DIR` (`/app/data` under Compose; use `./data` for native runs)
- `TONIES_EMAIL` / `TONIES_PASSWORD` (legacy environment credentials override the vault; keep them private)

Browser selector overrides and `TONIES_STORAGE_STATE_FILE` are obsolete. Existing `tonies-storage-state.json` files are not used by this branch; do not delete the whole data directory to troubleshoot login.

## Validation boundaries

Prior standalone HTTP acceptance exercised disposable upload, processing, rename, reorder and exact-ID deletion with original content preserved. It did not deploy this app branch or revalidate full-length YouTube downloads. **Live clear-to-empty was explicitly excluded to preserve existing libraries**; the confirmed delete-all feature remains, but is not claimed as a live acceptance pass. Test it only on an authorized empty/spare target, never a populated library merely for coverage.

## Docs
- Quick start: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- App overview: [`docs/APP_OVERVIEW.md`](docs/APP_OVERVIEW.md)
- Operations: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md)
