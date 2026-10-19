# App Overview

Tonies-YT moves audio onto Creative Tonies through the Tonies cloud API over HTTPS. It does not automate a browser.

## Primary flow

1. Choose a Creative Tonie.
2. Search YouTube, paste a video/playlist link, or import a local file.
3. Preview or clip the audio, then add it.
4. Check Activity while the file transfers and Tonies processes it.

Playlists show their first 50 videos. Multiple selections attach in the displayed order, even if downloads finish out of order.

## UX model

- **Tracks:** rename, reorder or remove tracks using row actions; select several for batch removal. **Reverse display** changes the view, not playback order. **Clear all** is separate and requires confirmation.
- **Add audio:** switch between YouTube and Local files. Local rows offer **Play**, **Add** and a **⋯** menu for clipping, placement, downloading and deletion. **Select** enables batch actions.
- **Local preview:** one browser player provides seeking and volume; it does not play on the physical Tonie or upload audio. Close, deleting the playing file and Lock stop it. Opening the editor also stops the local preview.
- **Editor:** drag handles or type clip times, listen, then save a new clip or add it. The original file stays. Loading names the current stage rather than estimating a percentage.
- **Activity:** separates queued work, transfers, saved attachments, cloud processing and refresh-needed failures. A failed follow-up refresh does not resubmit an accepted upload.
- **Settings:** change app/Tonies credentials, choose light/dark/system appearance and inspect tool versions. Tools are updated outside the UI.

Desktop shows Tracks and Add audio side by side. Mobile uses Tracks, Add audio and Activity tabs. The app remembers the selected source and mobile view when returning from Settings or Logs.

### Undo mistakes

Rename, Move and confirmed new uploads may be undone for up to ten minutes, only while the server still considers that submission valid. A newer submission, changed Tonie state, Lock, credential changes or restart can revoke it.

Tonie removals have **five seconds to cancel before submission**. Local-file deletion has a separate short cancellation toast. Once deleted, neither local files nor committed cloud removals can be restored by Undo. **Clear all is not recoverable.** See [exact limits](OPERATIONS.md#undo-and-cancellation).

## Architecture

```text
Browser → FastAPI → download / media jobs → per-Tonie action queue → Tonies API
              ↘ private data volume           ↑ local file uploads
```

- **Web:** plain HTML, CSS and JavaScript; no frontend build step. Scripts load in page order, then `app-boot.js` initializes the workspace. Live updates use SSE with polling fallback.
- **Jobs:** up to three YouTube downloads run together; editor fetches have a separate one-at-a-time lane. Plain download failures retry once; timeouts and size-limit failures do not.
- **Cloud:** uploads transfer up to three at a time and attach in queue order. Changes use chapter IDs, a per-Tonie queue and readback verification. An uncertain write is never automatically retried.
- **Auth:** each browser has an app session. The password-protected vault stores Tonies credentials; OAuth tokens remain server-side in memory. Lock/restart ends sessions.

See [queued-write safety](OPERATIONS.md#tonies-tokens-and-queued-writes) and [security boundaries](SECURITY.md).

## Code map

- `app/main.py`, `app/routes/`: startup, pages and HTTP endpoints.
- `app/auth.py`, `app/credentials.py`, `app/vault_storage.py`, `app/request_security.py`: sessions, vault and request protections.
- `app/job_*.py`, `app/downloader.py`, `app/media_*.py`: jobs, downloads, imports and audio processing.
- `app/actions.py`, `app/tonies_*.py`, `app/undo.py`, `app/uploader.py`: cloud client, queued edits and guarded Undo.
- `web/`, `web/assets/`: page markup and small feature-focused scripts/styles.
- `tests/`: offline contracts, fake cloud and UI regressions. `tests/test_no_monoliths.py` enforces file/function size limits.

## Important data

`DATA_DIR` is `/app/data` in Compose's named volume, or `./data` by default natively:

- `downloads/`: local audio; `logs/`: application logs.
- `jobs-state.json`: latest 200 jobs; interrupted jobs fail on the next start.
- `secrets/vault-meta.json` and `secrets/vault-credentials.enc`: app login and encrypted Tonies credentials.

Sessions, unlocked credentials, OAuth tokens, action queues and live events are memory-only. Back up the **entire** data directory, not just these listed files; see [backup/restore](OPERATIONS.md#backup--restore).

## Important endpoints

After sign-in, open `/docs` for the full API. The workspace uses `/api/jobs`, `/api/files`, `/api/tonies-actions` and `/api/events`; `/api/health` is public and proves only web availability.

API scripts must sign in at `POST /api/setup/login` and retain the session cookie. Queued actions target chapter IDs; legacy single-change `/api/tonies-content/*` routes remain supported. Undo accepts no client library snapshot; the server decides whether it is safe.
