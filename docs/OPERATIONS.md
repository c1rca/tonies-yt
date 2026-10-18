# Operations

Use a consistent explicit project name. Examples use `tonies-local`; substitute the project that actually owns your installation. Never run an unscoped `down`, `--remove-orphans`, volume deletion or cleanup on a host with other projects.

## Lifecycle and verification

```bash
docker compose -p tonies-local up -d --build tonies-yt
docker compose -p tonies-local ps
docker compose -p tonies-local logs --tail 100 tonies-yt
curl --fail http://localhost:8090/api/health
# Stop without deleting state:
docker compose -p tonies-local stop tonies-yt
# After .env changes:
docker compose -p tonies-local up -d --force-recreate tonies-yt
```

Health proves web availability, not an unlocked vault, media playback or Tonies sign-in. Verify app login, a local audio preview and the intended cloud read/write separately. Do not perform destructive Tonies tests on a populated library.

For a second copy, choose a new `-p` project and `TONIES_PORT`. The named volume is project-scoped and there is no shared `container_name`. Confirm `docker compose ... config` before starting. Never share a data volume between running copies. The container always listens on 8080; `APP_PORT` does not change its command.

## Runtime security and data layout

The image and Compose run as **10001:10001** from the first process: no root bootstrap, recursive startup chown or privilege fallback. App code/packages/tools are root-owned. Compose enforces read-only root, all capabilities dropped, `no-new-privileges`, a private writable data volume and bounded disposable `/tmp`. The 1 GiB memory, 2 CPU and 256 PID limits may need deliberate tuning for large media, but do not remove identity/filesystem restrictions to fix a resource failure.

- `/app/data`: downloads, imports, job history, settings, logs and vault files; persists in `<project>_tonies-data` by default.
- `/tmp/home`, `/tmp/cache`, `/tmp/cache/deno`: disposable home and downloader/runtime caches. Cleared on container recreation/restart; app code and Python packages are not installed here (upstream downloader components may cache JavaScript).
- File logs: `/app/data/logs/toni-auto.log`; Docker JSON logs rotate at 10 MiB, three files. Persistent app logs/media are not quota-limited: monitor disk usage.
- Native: use exported `DATA_DIR=./data` and host-relative `LOG_FILE`; no automatic `.env` loading or Docker sandbox.
- `TONIES_EMAIL`/`TONIES_PASSWORD`: optional plaintext environment overrides for the cloud account, not the app login.

Inspect effective controls and actual process identity:
```bash
docker compose -p tonies-local config
docker compose -p tonies-local exec -T tonies-yt id
docker compose -p tonies-local exec -T tonies-yt python -c \
  'from pathlib import Path; print(Path("/proc/1/status").read_text())'
```

The default listener is only `127.0.0.1:8090`. Do not mount host root, the Docker socket or arbitrary directories; do not use `privileged`, host networking, root user or `chmod 777`. See [Security](SECURITY.md).

## Existing root-owned bind mounts

**The new named-volume default does not import old `./data`.** A new setup screen after an upgrade can mean you selected empty state; stop and fix the mount instead of creating a replacement login. Do not delete the original data or run `down -v`.

For an existing Linux host bind, one explicit migration option is a **separate copy** with app ownership. This leaves original files untouched. Use maintenance downtime, inspect every absolute path and keep rollback image/config. Commands below are examples, not an automatic startup step; an administrator must adapt/approve them. Do not run them against a symlink, mount root or a directory shared with another application.

1. Identify the old container/project and the exact source mounted at `/app/data` (`docker inspect <old-container>`). Stop only that project's app service. Do not guess from the current checkout/project name.
2. Back up the whole old directory (including `secrets/`) and `.env` to a private location. On Linux:
   ```bash
   umask 077
   mkdir -p "$HOME/tonies-backups"
   # Replace this literal path with the inspected old bind source.
   sudo tar --numeric-owner -C /absolute/old/data -czf - . > "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   tar -tzf "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   sha256sum "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   ```
   Verify a restore into an isolated location before relying on the backup. This archive is sensitive, not encrypted.
3. Create a new, explicitly chosen copy—not an empty directory hiding old state:
   ```bash
   sudo install -d -m 0700 /absolute/new/tonies-data
   sudo cp -a /absolute/old/data/. /absolute/new/tonies-data/
   # Administrative, one-time ownership conversion of ONLY this verified copy.
   sudo chown -R -P --no-dereference 10001:10001 /absolute/new/tonies-data
   sudo chmod -R go-rwx /absolute/new/tonies-data
   ```
   Inspect unexpected symlinks before proceeding; do not follow links outside the copy. Root-owned old data that is not converted must fail rather than trigger a root fallback.
4. Create `compose.bind.yml` (not committed):
   ```yaml
   services:
     tonies-yt:
       volumes:
         - type: bind
           source: /absolute/new/tonies-data
           target: /app/data
           bind:
             create_host_path: false
   ```
   Compose merges mounts by target; render and confirm there is exactly one `/app/data` mount and the intended source before starting:
   ```bash
   docker compose -p tonies-local -f docker-compose.yml -f compose.bind.yml config
   docker compose -p tonies-local -f docker-compose.yml -f compose.bind.yml up -d --build tonies-yt
   ```
   Include both `-f` arguments for every later operation on this bind deployment.
5. Sign in with the **existing** app login, verify files/jobs/settings, and `exec ... id` (UID 10001). Preserve the original and backup until acceptance; if permissions fail, stop and repair the copy offline. Never switch runtime to root.

Alternatively restore the verified archive into a **new project-scoped named volume** using the restore procedure below; this also avoids modifying original state. Docker Desktop/Windows operators should use named volumes and this archive/restore route rather than relying on Linux host `chown` semantics.

## Backup / restore

Back up `.env` and **all** data, including `secrets/`. Audio/history/settings/logs and the archive are not encrypted by the vault. Restrict backup access and use separately managed backup encryption if required. Never share backup archives in bug reports.

For a named-volume installation, stop the app for a consistent archive. `run` below overrides the app command with tar but retains the nonroot UID and only the configured data mount:
```bash
umask 077
mkdir -p "$HOME/tonies-backups"
docker compose -p tonies-local stop tonies-yt
docker compose -p tonies-local run --rm --no-deps -T tonies-yt \
  tar -czf - -C /app/data . > "$HOME/tonies-backups/tonies-data.tar.gz"
tar -tzf "$HOME/tonies-backups/tonies-data.tar.gz"
sha256sum "$HOME/tonies-backups/tonies-data.tar.gz"
docker compose -p tonies-local up -d --no-build tonies-yt
```

Restore into a **new empty** volume/project, never over a running or nonempty library. Use only a trusted archive you created/verified; inspect its entries before extraction. A new `-p` changes the volume name. Pick an unused port (`TONIES_PORT=8091`) to avoid the original instance, and restore as app UID (not the archived root UID):
```bash
docker compose -p tonies-restore build tonies-yt
docker compose -p tonies-restore run --rm --no-deps -T tonies-yt \
  tar --no-same-owner -xzf - -C /app/data < "$HOME/tonies-backups/tonies-data.tar.gz"
TONIES_PORT=8091 docker compose -p tonies-restore up -d --no-build tonies-yt
```

Keep the original stopped or use separate data/ports. Verify existing login, settings, library files and playback before switching traffic. A restored archive does not restore in-memory sessions/cloud tokens; sign in again. Do not use `down -v`: it deletes named-volume state. For a bind deployment, use its override throughout or perform an offline host backup of the inspected bind path.

## App sign-in and sessions

App sign-in is separate from Tonies OAuth. APIs require the browser's session cookie; HTML redirects to setup/login. Health and the minimal first-run/sign-in routes remain public. **Lock** signs all browsers out and locks the vault; restart ends memory-backed sessions. Repeated failed logins are throttled. Cross-origin writes are rejected. The explicit `APP_ALLOWED_HOSTS` list protects against arbitrary Host/DNS rebinding; do not replace it with a wildcard.

### Reset a forgotten app password

There is no password recovery. Preserve a complete backup, stop the app, and move **both** vault files (`secrets/vault-meta.json`, `secrets/vault-credentials.enc`) into a private recovery directory outside the active data tree. For a named volume, use a deliberate one-off `docker compose ... run --rm --no-deps -T tonies-yt` maintenance command as UID 10001 (or export the backup and restore a copy without those two files). For a host bind, use the host administrator on the verified copy. Do not make the app root.

Start locally and immediately set up a new login/Tonies credentials. Downloads/jobs/settings remain, but the old encrypted Tonies credentials cannot be recovered without the original password. Keep old vault files private in case the original password is remembered. Never remove vault files to troubleshoot cloud sign-in.

## Running behind a reverse proxy

Tonies-YT itself serves HTTP. Keep port 8090 closed to the internet. Complete setup over localhost/SSH first, then terminate **HTTPS** at a trusted proxy and enforce your network access policy. Host-based proxy examples may keep publication on loopback; a containerized proxy needs a deliberately configured private network rather than host networking or an unrestricted host port.

`.env`:
```dotenv
APP_ALLOWED_HOSTS=localhost,127.0.0.1,[::1],tonies.example.com
APP_PUBLIC_URL=https://tonies.example.com
```

Preserve `Host`; do not rely on unvalidated `X-Forwarded-Host`. With `APP_PUBLIC_URL` configured the app can enforce the canonical HTTPS origin/cookie policy without trusting arbitrary proxy headers. The image defaults to `--no-proxy-headers`. If client address/scheme forwarding is needed, explicitly override the command in a proxy-specific Compose file:
```yaml
services:
  tonies-yt:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "172.30.0.10"]
```

**Replace `172.30.0.10` with the verified, fixed source IP of your proxy** as seen inside Docker. A host proxy can appear as the bridge gateway, not 127.0.0.1. Never use `*`; firewall the backend so only the trusted proxy can connect, and have the proxy overwrite forwarding headers. With forwarding disabled, login throttling sees the proxy address and clients share its limit.

nginx example inside an already-configured TLS server block:
```nginx
location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_buffering off; # /api/events is server-sent events
    client_max_body_size 101m; # 100 MiB file ceiling plus multipart overhead
    client_body_timeout 30s;
}
```

The backend default file limit is 100 MiB (`APP_MAX_UPLOAD_BYTES=104857600`); request streaming limits also apply. Align proxy/request/file ceilings with scratch capacity and concurrent load rather than increasing them blindly. Verify via the real HTTPS browser URL: sign-in sets Secure cookies, writes succeed only from the configured origin, arbitrary Hosts/forged forwarding headers are rejected and SSE reconnects. Do not claim a proxy deployment works based only on the container health check.

## Tonies tokens and queued writes

The cloud client obtains OAuth tokens from `login.tonies.com`, renews them in memory and discards them on restart/lock/credential changes. Tokens are not a replacement for the app login. Environment credentials override vault cloud credentials.

Library changes use a per-Tonie in-memory queue, exact chapter IDs and readback verification. A possibly-landed cloud write is not automatically retried; refresh before deciding what to do next. A restart loses unsaved queued work. Back up first and avoid restarts while work is active. For action semantics see the application docs and [changelog](../CHANGELOG.md).

## Undo and cancellation

`POST /api/tonies-actions/{action_id}/undo` requires a signed-in, same-origin request and takes no snapshot from the browser. Public action fields `undo_available`, `undo_pending` and `undo_expires_at` control the affordance; the server is authoritative. Capabilities are one-level per human submission per Tonie, expire after at most ten minutes, and are revoked by newer submissions/session resets. Rename/Move/confirmed new uploads have guarded inverses. Pending removals can be canceled for five seconds before commitment; committed deletion and Clear all cannot be recovered by this feature.

The inverse consumes its capability before making one guarded write, including failures. Stale state or an uncertain result returns `409` and `needs_refresh: true`; refresh rather than retry. Success includes confirmed `content` only when an inverse actually completed. A canceled pending removal returns actions with `status: canceled` and no invented canonical content. Upload jobs mirror canceled/undone outcomes separately from forward completion, so they do not leave ghost uploads or advertise removed attachments as current. Provider-side atomic compare-and-swap is not proven; another device can still race the final read/write boundary.

## Published images

Pushing a `v*` tag runs `.github/workflows/release-image.yml`: it builds the image, smoke-tests it as UID 10001 with a read-only root (health, `401` on the API, ffmpeg and Deno present), then publishes `ghcr.io/c1rca/tonies-yt` for `linux/amd64` and `linux/arm64` as `X.Y.Z`, `X.Y`, `X` and `latest`. A manual run of the workflow builds and tests without publishing. Compose pulls `${TONIES_IMAGE_TAG:-latest}` and falls back to building the checkout if the image can't be pulled. After the first publish, check the package is **Public** under the repository's Packages.

## Audio processing

Every added track goes through one encoder (`media_process.convert_to_mp3`): it measures integrated loudness (EBU R128), applies one static gain towards -14 LUFS with a -1 dBFS limiter (boost capped at +12 dB, no gain for silence) and writes 128 kbps stereo MP3. Clips made in the audio editor are cut from that MP3 without re-encoding. If loudness can't be measured the audio is converted unchanged. YouTube audio is downloaded in its original format and encoded once. Existing library files are not re-processed.

YouTube: at most three downloads run at once; a plain download failure is retried once (a download has no side effects), timeouts and size limits are not. A playlist link lists its first 50 videos. Several results added together attach in the order shown, even when a later one finishes downloading first.

## Audio tooling and dependency audits

The image contains ffmpeg, CA certificates, curl, Deno **2.9.7** and `yt-dlp[default]`; no browser runtime is installed. Deno amd64/arm64 archives are version-pinned and SHA-256 checked. The default yt-dlp dependency set installs its matching `yt-dlp-ejs` solver wheel at build time; production invocations disable remote component downloads, ambient configuration and external plugins. Do not rely on `ejs:github` fetches at runtime. Python direct dependencies are pinned except yt-dlp; base tags/APT/transitives also move, so builds are **not fully reproducible**. Record the built image ID and dependency inventory for each release.

Keep code immutable: rebuild/recreate to update tools. Pip is removed from the runtime image after build (the base image's pip also had advisories); the Settings updater refuses runtime installation. Do not run pip against the running container, enable user-site installations, make packages writable or relax the read-only filesystem.

```bash
# Python advisory scan, run outside the production container:
uv tool run pip-audit -r requirements.txt
# Build current base; review changes and run tests before recreating:
docker compose -p tonies-local build --pull tonies-yt
```

The hardening audit identified multipart and Starlette advisories in the old resolved Python set. Pins now use FastAPI 0.135.0, Starlette 1.3.1 and python-multipart 0.0.32; run the scanner again because advisories change. A clean Python scan is not a Debian/ffmpeg/Deno scan or a guarantee of exploit resistance. Use a current OS/container scanner in your release process and triage reported applicability rather than declaring unscanned packages safe.

## Version labels

All five application pages (workspace, Settings, Logs, Login and Setup) receive the same server-rendered footer from `app/web_pages.py`. Its version comes from `app.__version__` in `app/__init__.py`, also used by startup/status. Bump that central release version and restart/rebuild the app as appropriate; no per-page edits or browser-side version checks are needed. Pages are served with `no-store` so a reload reflects the running release. This does not hot-update an already-running Python process or an already-open tab.

## Tests and isolated acceptance

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The offline suite exercises fake cloud contracts and effective packaging defaults, but is not a live cloud or image smoke test. Release acceptance must build the real image, inspect `Config.User`, run under the actual Compose restrictions with a **unique project/unused loopback port/fresh data**, and exercise setup, login, authenticated local import/range playback, ffmpeg conversion, Deno and downloader caches. Confirm PID 1 and the app process are nonroot, read-only code writes fail, only data/tmp are writable and limits/capabilities are effective. Clean up only the isolated project's artifacts; never run bare `compose up/down` or mount production data for a smoke test.

Cloud upload, actual YouTube extraction, proxy TLS and Windows/ARM64 operation require separate acceptance; local synthetic media does not prove those paths. Logs can contain private titles/URLs, so keep exports private even when token/password redaction is enabled.
