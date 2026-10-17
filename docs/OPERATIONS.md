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
- `TONIES_EMAIL` / `TONIES_PASSWORD`, when both are set, choose the Tonies account instead of the vault's. The app login still comes from the vault.

## App sign-in and sessions

Each browser signs in at `/login` with the app username and password and receives a session cookie (`tonies_yt_session`, HttpOnly, SameSite=Strict, Secure when the request arrived over HTTPS). Sessions are kept in memory for up to 30 days.

- Without a session, API calls get `401` and pages redirect to `/login` (or `/setup` before first run). Only `/api/health`, `/api/setup/status`, `/api/setup/login`, `/api/setup/init`, `/login`, `/setup`, `/static/*` and `/favicon.ico` are open, and `/api/setup/status` then reports only whether setup or sign-in comes next.
- **Lock** signs every browser out and locks the vault. Changing the app password signs every other browser out. A restart ends all sessions.
- After 5 failed sign-ins from one address within 5 minutes, further attempts get `429` with a `Retry-After` header until the window passes.
- State-changing requests whose `Origin` doesn't match the host (or that the browser marks `Sec-Fetch-Site: cross-site`) get `403`.
- `/api/setup/init` refuses once a vault exists, so setup can't be re-run over an existing login.

Scripts must sign in first and keep the cookie:
```bash
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username":"me","app_password":"..."}' http://localhost:8090/api/setup/login
curl -b jar http://localhost:8090/api/jobs
```

To queue library changes from a script, send them in one request; they are saved in one batch:
```bash
curl -b jar -H 'Content-Type: application/json' http://localhost:8090/api/tonies-actions -d '{
  "target_url": "https://my.tonies.com/creative-tonies/<household>/<tonie>/edit",
  "actions": [{"type": "delete", "chapter_id": "..."},
              {"type": "move", "chapter_id": "...", "position": {"edge": "start"}},
              {"type": "rename", "chapter_id": "...", "title": "New name"}]}'
```

## Running behind a reverse proxy

Tonies-YT serves plain HTTP. To reach it from outside your home network, put it behind an HTTPS reverse proxy and keep port 8090 closed to the internet. The proxy must:
- pass the browser's host on, either unchanged in `Host` or in `X-Forwarded-Host` (otherwise writes fail the cross-site check with `403`);
- send `X-Forwarded-Proto: https`, so the session cookie is marked Secure;
- not buffer `/api/events` (server-sent events).

nginx example:
```nginx
location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    client_max_body_size 200m;
}
```

Sign-in throttling counts the address the app sees, which behind a proxy is the proxy itself, so failed attempts from anywhere share one limit.

## Tonies sign-in and tokens

The client signs in with OAuth (password grant against `login.tonies.com`) and renews its access token in memory with the refresh token. Tokens are never written to disk. A restart discards them, and the next Tonies action signs in again with the credentials from the unlocked vault or the environment. Locking the app, signing in again or changing Tonies credentials in Settings also discards the current tokens.

Tonies can revoke or expire a refresh token at any time; the client then signs in again once. If that fails, check the credentials in Settings.

## Action queue and write safety

Every library change (delete, rename, move, delete all, upload) goes into a per-Tonie queue (`app/actions.py`). Each Tonie has one worker that waits 0.15 s for more changes to arrive, then saves everything that's ready in one batch:

1. read the Tonie;
2. apply the queued changes in order, each by chapter ID (`app/tonies_batch.py`); a change whose track is gone, or that would exceed the Tonie's capacity or chapter limit, fails on its own and the rest still apply;
3. write the whole chapter list once;
4. read it back until it matches.

A batch of any size costs three requests. Positions are anchors ("before this chapter", "at the start/end") with an index fallback, so a move or upload queued behind other edits still lands where it was dropped.

Uploads first send their audio to Tonies storage, up to three at a time, while edits and reads carry on. They are attached in the order they were queued, at their requested position, so there is no follow-up reorder.

A write that may or may not have landed (timeout, connection drop) is never retried automatically; every change in that batch is reported as failed with a "refresh first" message. Progress and the resulting library are published as `tonies_actions` events on `/api/events`; `GET /api/tonies-actions` lists recent changes and their status.

Queued changes live in memory. A restart drops anything not yet saved; the page notices and shows the library as it is on Tonies.

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

Back up `.env` and the whole `data/` directory, including `secrets/`. Stop the app first if you need a consistent copy. Restore to the same `./data` mount (or native `DATA_DIR`) and start the app; sign in with the app password. No tokens or sessions need restoring.

## Upgrading from 1.x

See [`CHANGELOG.md`](../CHANGELOG.md) for the full list.

- Keep `data/`; vault, job history and library carry over unchanged, and the existing app login keeps working.
- If you ran with `TONIES_EMAIL` / `TONIES_PASSWORD` and no vault, the first visit asks you to create an app login; the Tonies fields can stay empty.
- Scripts and integrations that call the API must now sign in and send the session cookie (see above).
- Remove `TONIES_STORAGE_STATE_FILE`, `TONIES_APP_URL`, `TONIES_LOGIN_URL` and the `TONIES_*_SELECTOR` lines from `.env`; they are ignored.
- `data/tonies-storage-state.json` (old browser session cookies) is no longer read. Delete it or keep it private.
- `data/staging/` and `data/debug/` were browser-era working folders and can be removed once the upgrade is confirmed.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
DATA_DIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

GitHub Actions runs the same suite, plus a Docker build and health check, on every push to `main` and every pull request.

The suite is offline: the Tonies cloud is a fake (`tests/tonies_fake.py`) that follows the observed OAuth, GraphQL, REST and S3 contracts. It covers sign-in and sessions (every API route is checked for `401` without one), the action queue and batch planner, token renewal, exact-ID writes, stale and ambiguous writes, no-retry on unknown outcomes, upload placement, processing readback, job-state saves, log privacy and the container dependency contract. It does not build an image or talk to Tonies.

To try UI changes without a Tonies account, run the app against the same fake cloud with simulated network delay:
```bash
.venv/bin/python scripts/dev/fake_cloud_app.py --port 8099 --latency 0.25
```
It prints its throwaway data directory; set the token from `session.txt` there as the `tonies_yt_session` cookie.

Before a release, check by hand:
- search → download → upload → processing finishes → library shows the track
- Browse import, button upload and drag/drop upload
- rename / reorder / delete, with other tracks and order unchanged
- Settings: app password change, Tonies credential change, lock and sign in again
- a second browser or phone needs its own sign-in

**Delete all:** the feature has a confirmation step and passes the offline tests, but it has not been run live against a populated Tonie. Test it only on a spare or empty Tonie.
