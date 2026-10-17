# Operations — API-only branch

This runbook describes the branch's HTTP transport, not a claim that production has been migrated. Operate only the intended Compose checkout/project. An isolated test stack needs its own project name, container name, host port and data directory; do not share the default production mounts or run broad cleanup commands.

## Start / stop

Start:
```bash
docker compose up -d --build
```

Stop only the app service:
```bash
docker compose stop tonies-yt
```

Restart without rebuilding:
```bash
docker compose restart tonies-yt
```

After changing `.env`, recreate the service so it receives the new values:
```bash
docker compose up -d --force-recreate tonies-yt
```

## Health checks
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`
- Health: `http://localhost:8090/api/health`

CLI:
```bash
curl -s http://localhost:8090/api/health
```

Health alone does not prove authenticated Tonies access or completed audio processing.

## Logs
- UI/export: `http://localhost:8090/logs`
- File with `.env.example`: `./data/logs/toni-auto.log`
- Container:
```bash
docker compose logs --tail 100 tonies-yt
```

Keep exported logs private and redact account identifiers, credentials and tokens before sharing.

## Config and runtime layout
- `DATA_DIR=/app/data` in Compose, mounted from relative `./data` on Linux and Windows Docker Desktop.
- Native runs should set `DATA_DIR=./data` and a host-relative `LOG_FILE`.
- `LOG_LEVEL` controls logging verbosity.
- `LOG_FILE=/app/data/logs/toni-auto.log` is the supplied example path.
- Container Uvicorn listens on 8080, mapped to host 8090. `APP_PORT` alone does not change the Dockerfile's explicit command.
- `TONIES_EMAIL` / `TONIES_PASSWORD`, when both populated, override account credentials from the app vault. Normal setup does not require either variable.

Browser selectors and `TONIES_STORAGE_STATE_FILE` are obsolete. The API-only branch does not read or write a Tonies browser-state file. Legacy browser configuration can be removed from a private `.env` without deleting the app's persistent data.

## Authentication lifecycle

The Tonies client acquires OAuth tokens directly and renews them in memory. Access tokens, refresh tokens and token-renewal state are not persisted to disk by this branch. Restarting discards them; the next authenticated operation obtains fresh tokens using account credentials from the unlocked vault or environment. Refresh can be revoked or expire: no indefinite session is promised.

The **app vault is separate from token state**. Encrypted account credentials and vault metadata remain under `data/secrets/`; the app's unlocked credentials are process-local. After restart, unlock the vault again when prompted. If credentials are wrong, update them in Settings (or correct the overriding environment variables and recreate the service). Do not delete vault files or all of `data/` to reset Tonies access.

An old `tonies-storage-state.json` is unused. Deleting it does not reset OAuth tokens, and it is not needed for restore. Treat any retained legacy file as sensitive.

## Setup / auth routes
- `/setup` — first-time setup
- `/login` — app login
- `/settings` — app password, Tonies credentials, search settings, tool versions
- `/account` — legacy redirect to `/settings`

## Audio tooling

The runtime is `python:3.12-slim`, with ffmpeg, curl, unzip and CA certificates. Deno remains on PATH for yt-dlp JavaScript extraction; the downloader still uses `--js-runtimes deno` and `--remote-components ejs:github`. Build-time installation retains the existing yt-dlp upgrade, so builds are not fully reproducible from Python pins alone. Downloads also require access to YouTube and the configured remote JS component source. No browser is required for Tonies API access.

`cryptography` is an explicit pinned requirement because the existing vault imports Fernet. It must not be assumed to arrive from a browser base image or an unrelated transitive dependency. Other existing dependency pins, including `httpx==0.27.2`, remain unchanged.

## Backup / restore

Back up `.env` and the complete `data/` directory securely, including `secrets/`, downloads and app settings. Do not commit backups. Stop the intended app before copying/restoring changing files when consistency is required. Restore to the same relative Compose mount (or configured native `DATA_DIR`), then start the intended service. No saved OAuth tokens or browser session need to be restored; normal app unlock/login is sufficient to make account credentials available.

## Validation and release boundaries

Offline packaging contracts:
```bash
python -m pytest tests/test_runtime_dependencies.py tests/test_downloader.py -q
```

These tests check the declared dependencies, lean base, media/HTTPS/Deno tooling, explicit vault crypto, app command, Compose port/data contract and a browser-blocked offline crypto/HTTP import probe. They do **not** build an image, run a live OAuth exchange or validate the full UI. An isolated image build and app integration gate remain separate release requirements.

Before release:
- Verify search → download → upload → completed processing → fresh library sync.
- Verify local Browse normalization, button upload and drag/drop upload.
- Verify rename / reorder / exact-ID delete while preserving unrelated tracks/order.
- Verify Settings, app vault unlock, credential changes and logs.
- Verify concurrent mutation behavior and continued YouTube search responsiveness.
- Verify token renewal and fresh authentication after restart without browser state.
- Keep production untouched until an explicitly authorized deployment.

**Clear-to-empty:** delete-all remains a feature with confirmation. Prior standalone live acceptance explicitly excluded clearing populated Tonies to preserve their original libraries. Do not claim a live delete-all pass or use a populated personal library to obtain one; require a separately authorized empty/spare target.
