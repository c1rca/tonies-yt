# App Overview

Tonies-YT is a small web app for moving audio onto Creative Tonies. It talks to the Tonies cloud directly over HTTPS; no browser is involved.

## Primary flow
1. Search YouTube.
2. Pick a result.
3. Download audio with yt-dlp and ffmpeg.
4. Upload through the Tonies cloud API.
5. The library shows the new track right away and updates when Tonies finishes processing it.

## UX model

### Search
- Latest search only, with configurable result count: 5 / 10 / 15 / 20
- Realtime updates with SSE and clear progress states
- Search status appears in the Results pane
- Starting a new search can replace an in-flight search request

### Tonies library
- Rename, reorder and delete tracks
- Delete all content **with confirmation**
- Drag local files onto the selected Tonie
- Desktop right-click menu: upload / upload to top / upload to bottom
- Multiple local uploads processed one at a time
- Shows free/used minutes

### Settings
- Change app password
- Update Tonies account credentials
- View installed tool versions
- Update yt-dlp from the UI when available

## Architecture
```mermaid
flowchart LR
  UI[Web UI] --> API[FastAPI]
  API --> JOBS[Job queue]
  JOBS --> YTDLP[yt-dlp + ffmpeg + Deno]
  JOBS --> QUEUE[Action queue]
  API --> QUEUE
  QUEUE --> TONIES[ToniesAPI client]
  API --> TONIES
  TONIES --> AUTH[login.tonies.com OAuth]
  TONIES --> CLOUD[Tonies GraphQL / REST API]
  TONIES --> S3[Presigned S3 upload]
  API --> DATA[(./data)]
```

`ToniesAPI` (`app/tonies_api.py`) owns all Tonies traffic:
- **Auth:** OAuth password grant, refresh in memory, one re-login if the refresh is rejected.
- **Reads:** households, Creative Tonies and their chapters via one GraphQL query.
- **Writes (`apply`):** one read, a batch of edits planned by chapter ID, one PATCH of the full chapter list over REST, one read-back until it matches. Unknown outcomes are never retried.
- **Uploads:** `prepare_upload` allocates a file ID and streams the file to the presigned S3 form (no bearer token); `apply` attaches it at the requested position and confirms it by its server-assigned ID.

`app/actions.py` queues every library change per Tonie and applies them in batches; uploads transfer three at a time in parallel and attach in queue order. `app/tonies_batch.py` is the pure planner that turns a list of changes into one chapter list. The page (`web/index.html`) shows queued changes at once by layering them on the last server content, and settles on the `tonies_actions` events. See [Operations](OPERATIONS.md#action-queue-and-write-safety).

`app/auth.py` guards everything else: per-browser session cookies, sign-in throttling and cross-site write checks (see [Operations](OPERATIONS.md#app-sign-in-and-sessions)).

`app/uploader.py` is the thin app-facing layer: it keeps one client per account and resets it when the vault locks or credentials change.

## Code map

### Backend
- `app/main.py`: routes and API
- `app/auth.py`: sign-in sessions and request guard
- `app/jobs.py`: search/download jobs; uploads hand over to the action queue
- `app/actions.py`: per-Tonie queue of library changes, applied in batches
- `app/tonies_batch.py`: batch planner (pure, no I/O)
- `app/events.py`: event stream for `/api/events`
- `app/tonies_api.py`: Tonies OAuth/HTTP client
- `app/uploader.py`: app-facing Tonies operations
- `app/downloader.py`: YouTube search/download
- `app/credentials.py`: encrypted local vault and app login
- `app/config.py`: environment config

### Frontend
- `web/index.html`: main app UI
- `web/login.html`: login page
- `web/setup.html`: setup page
- `web/account.html`: settings page
- `web/logs.html`: logs page
- `web/assets/session.js`: sends the browser to sign-in when its session ends

## Important data

Stored under `DATA_DIR` (`./data` natively, `/app/data` in Compose):
- `downloads/`: local audio library
- `logs/`: application log (`toni-auto.log` with `.env.example`)
- `jobs-state.json`: the latest 200 jobs. Jobs that were mid-flight at shutdown are marked failed on the next start.
- `secrets/vault-meta.json`, `secrets/vault-credentials.enc`: app login and encrypted Tonies credentials

Kept in memory only:
- sign-in sessions
- the unlocked Tonies credentials
- OAuth access and refresh tokens
- the live job queue and event stream

## Important endpoints

### Core
- `GET /api/health`
- `POST /api/chat`
- `GET /api/jobs`
- `POST /api/jobs/{job_id}/select`

### Tonies library
- `POST /api/tonies-actions`: queue changes (`delete`, `rename`, `move`, `clear`) for one Tonie
- `GET /api/tonies-actions`: recent changes and their status
- `POST /api/tonies-content`
- `POST /api/tonies-content/delete`
- `POST /api/tonies-content/delete-all`
- `POST /api/tonies-content/reorder`
- `POST /api/tonies-content/rename`

The single-change endpoints below `tonies-content` predate the queue and still work; the app itself uses `tonies-actions`. Rename, reorder and delete take the `chapter_id` the UI is acting on. Delete follows that ID if the chapter has moved; rename and reorder refuse if it is no longer at the given index. All three refuse if the ID is gone.

### Files
- `GET /api/files`
- `DELETE /api/files/{filename}`
- `POST /api/upload-existing`

### Setup/auth

Every endpoint except health and the first four below needs a signed-in session.

- `GET /api/setup/status`
- `POST /api/setup/init`
- `POST /api/setup/login`
- `POST /api/setup/lock`
- `POST /api/setup/change-password`
- `POST /api/setup/update-tonies-credentials`
- `POST /api/setup/update-yt-dlp`
