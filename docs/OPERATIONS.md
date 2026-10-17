# Operations

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

To run a second, isolated copy (for testing), give it its own Compose project name, container name, host port and data directory. Never point two copies at the same `data/`.

## Health checks
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`
- Health: `http://localhost:8090/api/health`

```bash
curl -s http://localhost:8090/api/health
```

Health only shows the web server is up. It does not prove the vault is unlocked or that Tonies sign-in works; loading a Tonie in the UI does.

## Logs
- UI/export: `http://localhost:8090/logs`
- File with `.env.example`: `./data/logs/toni-auto.log`
- Container:
```bash
docker compose logs --tail 100 tonies-yt
```

The app does not log tokens, passwords or presigned upload URLs (`httpx`/`httpcore` logging is capped at WARNING). Logs do contain Tonie URLs and chapter titles, so keep exported logs private.

## Config and runtime layout
- `DATA_DIR=/app/data` in Compose, mounted from `./data`.
- Native runs should set `DATA_DIR=./data` and a host-relative `LOG_FILE`.
- `LOG_LEVEL` controls logging verbosity.
- `LOG_FILE=/app/data/logs/toni-auto.log` is the supplied example path.
- The container listens on 8080, mapped to host 8090. `APP_PORT` alone does not change the Dockerfile's command.
- `TONIES_EMAIL` / `TONIES_PASSWORD`, when both are set, override the vault and the app needs no unlock.

## Tonies sign-in and tokens

The client signs in with OAuth (password grant against `login.tonies.com`) and renews its access token in memory with the refresh token. Tokens are never written to disk. A restart discards them, and the next Tonies action signs in again with the credentials from the unlocked vault or the environment. Locking the app, signing in again or changing Tonies credentials in Settings also discards the current tokens.

Tonies can revoke or expire a refresh token at any time; the client then signs in again once. If that fails, check the credentials in Settings.

## Write safety

Every change goes through the same steps: read the Tonie, check the chapter you acted on is still where the UI saw it (by chapter ID), write the whole chapter list, then read it back until it matches. If the Tonie changed in between, or a chapter ID or title doesn't match, the write is refused with a "refresh first" message. A write that may or may not have landed (timeout, connection drop) is never retried automatically.

## Vault

`data/secrets/vault-meta.json` and `data/secrets/vault-credentials.enc` hold the app login and the encrypted Tonies credentials. The vault key is derived from the app password (PBKDF2-SHA256, 390,000 rounds), so neither file is usable without it.

### Reset a forgotten app password

The password cannot be recovered. To start over:

1. Stop the app: `docker compose stop tonies-yt`
2. Move both vault files out of `data/secrets/` to a private location (keep them in case the password comes back to you). The files are owned by root when created by the container, so use `sudo mv`, or `docker compose run --rm tonies-yt mv ...` from inside the container.
3. Start the app: `docker compose up -d`
4. Open the app; it shows first-run setup. Choose a new app username and password and enter your Tonies email and password.

Job history, downloads and settings are not touched.

## Setup / auth routes
- `/setup`: first-time setup
- `/login`: app login
- `/settings`: app password, Tonies credentials, search settings, tool versions
- `/account`: redirects to `/settings`

## Audio tooling

The image is `python:3.12-slim` with ffmpeg, curl, unzip and CA certificates. Deno is on PATH for yt-dlp's JavaScript extraction (`--js-runtimes deno`, `--remote-components ejs:github`). The build upgrades yt-dlp to the latest release, so builds are not fully reproducible from the Python pins alone; Settings can update yt-dlp later.

`cryptography` is pinned explicitly because the vault uses Fernet.

## Backup / restore

Back up `.env` and the whole `data/` directory, including `secrets/`. Stop the app first if you need a consistent copy. Restore to the same `./data` mount (or native `DATA_DIR`) and start the app; sign in with the app password. No tokens or browser sessions need restoring.

## Upgrading from the browser-automation version

- Keep `data/`; vault, job history and library carry over unchanged.
- Remove `TONIES_STORAGE_STATE_FILE`, `TONIES_APP_URL`, `TONIES_LOGIN_URL` and the `TONIES_*_SELECTOR` lines from `.env`; they are ignored.
- `data/tonies-storage-state.json` (old browser session cookies) is no longer read. Delete it or keep it private.
- `data/staging/` and `data/debug/` were browser-era working folders and can be removed once the upgrade is confirmed.

## Tests

```bash
DATA_DIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

The suite is offline: the Tonies cloud is a fake (`tests/tonies_fake.py`) that follows the observed OAuth, GraphQL, REST and S3 contracts. It covers token renewal, exact-ID writes, stale and ambiguous writes, no-retry on unknown outcomes, upload placement, processing readback, log privacy and the container dependency contract. It does not build an image or talk to Tonies.

Before a release, check by hand:
- search → download → upload → processing finishes → library shows the track
- Browse import, button upload and drag/drop upload
- rename / reorder / delete, with other tracks and order unchanged
- Settings: app password change, Tonies credential change, lock and unlock

**Delete all:** the feature has a confirmation step and passes the offline tests, but it has not been run live against a populated Tonie. Test it only on a spare or empty Tonie.
