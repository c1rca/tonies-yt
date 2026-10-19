# Operations

Examples use project `tonies-local`. Use the project and Compose files that actually own your installation. Never run unscoped cleanup, `--remove-orphans` or volume deletion on a shared host.

## Lifecycle and verification

```bash
# Start, or apply configuration changes:
docker compose -p tonies-local up -d tonies-yt
# Update published image — back up first:
docker compose -p tonies-local pull tonies-yt
docker compose -p tonies-local up -d tonies-yt
# Check:
docker compose -p tonies-local ps
docker compose -p tonies-local logs --tail 100 tonies-yt
curl --fail http://localhost:8090/api/health
# Stop without deleting data:
docker compose -p tonies-local stop tonies-yt
# After .env changes:
docker compose -p tonies-local up -d --force-recreate tonies-yt
```

For a checkout build, use `build --pull tonies-yt`, then `up -d --no-build tonies-yt` instead of pulling. Use `TONIES_IMAGE_TAG=local` to avoid replacing your build with a published tag.

Health checks web availability, not vault access, playback or cloud sign-in. Verify those separately; never test destructive cloud actions on a populated Tonie. Wait for active work to finish before restarting.

For another instance, use a unique project, unused `TONIES_PORT` and separate data volume. Inspect `docker compose ... config` first. Container port **8080** is fixed; `APP_PORT` does not override the image command.

## Runtime security and data layout

The image runs as **10001:10001**, with no root bootstrap or ownership-repair fallback. Compose adds read-only code, dropped capabilities, `no-new-privileges`, a 256 MiB temporary filesystem, 1 GiB memory, 2 CPUs and a 256-PID limit. Tune resource ceilings deliberately; do not remove identity/filesystem controls.

- `/app/data`: persistent audio, jobs, settings, logs and vault; normally `<project>_tonies-data`.
- `/tmp/home`, `/tmp/cache`, `/tmp/cache/deno`: disposable home/tool caches, not installed app code.
- `/app/data/logs/toni-auto.log`: persistent file log. Docker logs rotate at 10 MiB, three files; persistent files have no quota.
- Native runs: export host-relative `DATA_DIR`/`LOG_FILE`; `.env` is not loaded automatically.

Inspect effective controls and process identity:
```bash
docker compose -p tonies-local config
docker compose -p tonies-local exec -T tonies-yt id
docker compose -p tonies-local exec -T tonies-yt python -c \
  'from pathlib import Path; print(Path("/proc/1/status").read_text())'
```

Do not use root, `chmod 777`, privileged mode, host networking, host-root mounts or the Docker socket. See [Security](SECURITY.md).

## Existing root-owned bind mounts

**Old `./data` is not imported into the new named volume.** An unexpected setup screen after upgrading can mean empty state: stop and check the mount, rather than creating a replacement login. Never delete the original or use `down -v` to troubleshoot.

This Linux procedure migrates to a separate bind copy. Use maintenance downtime, keep the old image/config and inspect every absolute path. Do not use symlinks, mount roots or directories shared with other apps.

1. Inspect the old container (`docker inspect <old-container>`) to identify its project and exact `/app/data` source. Stop only that project's app.
2. Back up the **whole** old directory and `.env` privately. Replace the example path below with the inspected source:
   ```bash
   umask 077
   mkdir -p "$HOME/tonies-backups"
   sudo tar --numeric-owner -C /absolute/old/data -czf - . > "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   tar -tzf "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   sha256sum "$HOME/tonies-backups/data-before-nonroot.tar.gz"
   ```
   Verify an isolated restore before relying on the archive. It is sensitive and unencrypted.
3. Copy into a **new** directory, then change ownership of only that verified copy:
   ```bash
   sudo install -d -m 0700 /absolute/new/tonies-data
   sudo cp -a /absolute/old/data/. /absolute/new/tonies-data/
   sudo chown -R -P --no-dereference 10001:10001 /absolute/new/tonies-data
   sudo chmod -R go-rwx /absolute/new/tonies-data
   ```
   Inspect unexpected symlinks; never follow them outside the copy.
4. Create a private `compose.bind.yml`:
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
   Render configuration and confirm exactly one `/app/data` mount with the intended source before starting:
   ```bash
   docker compose -p tonies-local -f docker-compose.yml -f compose.bind.yml config
   docker compose -p tonies-local -f docker-compose.yml -f compose.bind.yml up -d --build tonies-yt
   ```
   Use both `-f` arguments for **every** later operation on this bind deployment.
5. Verify the **existing** login, files, jobs, settings and UID 10001. Keep the original and backup until accepted. If permissions fail, stop and repair the copy offline; never switch runtime to root.

Alternatively restore into a new named volume below. Docker Desktop/Windows users should use that archive/restore route rather than host `chown` semantics.

For 1.x upgrades, browser-session settings are obsolete; see [2.0 breaking changes](../CHANGELOG.md#breaking-changes). The unused `data/tonies-storage-state.json` contains an old browser session: delete it or keep it private.

## Backup / restore

Back up **all data, including `secrets/`, plus `.env`**. The vault does not encrypt audio, logs, history, settings or archives. Restrict access; use separate backup encryption if needed. Never attach backups to bug reports.

### Back up

Stop the app for a consistent archive. Use a fresh archive filename for each backup; the example below overwrites an existing file with that name. Copy `.env` into the private backup location too.

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

The maintenance command retains UID 10001. For a bind deployment, include its override or back up the inspected host directory offline.

### Restore and verify

Use a **new project and empty volume**, never a running/nonempty library. Inspect configuration and archive entries first. Extract only a trusted archive you created and verified, as app UID rather than the archived owner. In the configured Compose directory:

```bash
docker compose -p tonies-restore pull tonies-yt
docker compose -p tonies-restore run --rm --no-deps -T tonies-yt \
  tar --no-same-owner -xzf - -C /app/data < "$HOME/tonies-backups/tonies-data.tar.gz"
TONIES_PORT=8091 docker compose -p tonies-restore up -d --no-build tonies-yt
```

Use an unused port and separate data; keep the original stopped or isolated. For a checkout-only image, replace `pull` with `build`. Sign in with the existing login and verify settings, library files and playback **before switching traffic**. In-memory sessions/tokens are not restored.

Never use `down -v` unless you explicitly intend to delete that project's named-volume state.

## App sign-in and sessions

App sign-in is separate from Tonies OAuth. Each browser/API client needs a session cookie. **Lock** or restart signs all browsers out; changing the app password signs other browsers out. Login attempts are throttled and cross-origin writes rejected. Keep `APP_ALLOWED_HOSTS` explicit, never wildcarded.

### Reset a forgotten app password

There is no recovery backdoor. Resetting cannot decrypt the old Tonies credentials.

1. Back up the whole installation and stop the app.
2. Move **both** `secrets/vault-meta.json` and `secrets/vault-credentials.enc` outside the active data tree into a private recovery directory.
3. Start locally, create a new app login immediately and re-enter Tonies credentials. Local audio, jobs and settings remain.

For named volumes, use a deliberate one-off `docker compose ... run --rm --no-deps -T tonies-yt` maintenance command as UID 10001, or restore a backup copy without those two files. For binds, operate offline on the verified host copy. Do not run the app as root. Keep old vault files private in case you remember the password; never remove them just to fix cloud sign-in.

## Running behind a reverse proxy

1. Complete setup over localhost/SSH first. Tonies-YT serves HTTP; terminate **HTTPS** at a trusted proxy, enforce a firewall/access policy and keep port 8090 off the internet.
2. Set the browser hostname and canonical origin in `.env`:
   ```dotenv
   APP_ALLOWED_HOSTS=localhost,127.0.0.1,[::1],tonies.example.com
   APP_PUBLIC_URL=https://tonies.example.com
   ```
   A host proxy can keep `TONIES_BIND_ADDRESS=127.0.0.1`. A LAN listener requires deliberate `TONIES_BIND_ADDRESS=<host-LAN-IP>`, that IP/hostname in the allowlist, HTTPS and firewall rules. A container proxy needs a private Docker network, not host networking or an unrestricted published port.
3. Preserve `Host`; do not trust `X-Forwarded-Host`. `APP_PUBLIC_URL` enforces the canonical origin/cookie policy without proxy-header trust. Forwarded headers are disabled by default. If client address/scheme forwarding is needed, use a proxy-specific Compose override:
   ```yaml
   services:
     tonies-yt:
       command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "172.30.0.10"]
   ```
   **Replace `172.30.0.10` with the proxy's verified fixed source IP as seen inside Docker.** A host proxy may appear as the bridge gateway. Never trust `*`. Firewall the backend to the proxy and have it overwrite forwarding headers. Without forwarding, clients share the proxy address's login throttle.
4. Configure the proxy. Inside an existing nginx TLS server block:
   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8090;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_set_header X-Forwarded-For $remote_addr;
       proxy_buffering off; # /api/events uses SSE
       client_max_body_size 101m; # 100 MiB file plus multipart overhead
       client_body_timeout 30s;
   }
   ```
5. Recreate using your proxy-specific Compose files. Through the **real HTTPS URL**, verify Secure cookies, successful same-origin writes, rejected arbitrary Hosts/forged forwarding headers and SSE reconnection. Health alone is not proxy acceptance.

Use HTTPS outside localhost, including LANs. Align proxy/request/file ceilings (`APP_MAX_UPLOAD_BYTES=104857600` by default) with scratch capacity and concurrent load; do not just raise limits.

## Tonies tokens and queued writes

The cloud client obtains tokens from `login.tonies.com`, refreshes them in memory and discards them on restart, Lock or credential changes. Optional environment credentials override the vault's cloud account, never app login.

Changes queue per Tonie, target chapter IDs and verify writes by reading back. An uncertain write is **not retried automatically**: refresh first. Restart loses unsaved queued work. Uploads use presigned storage forms without sending the Tonies bearer token to storage.

## Undo and cancellation

- The latest valid submission per Tonie may reverse confirmed Rename, Move or a newly attached upload for up to **ten minutes**. Newer submissions, expiry and session/account resets revoke capabilities.
- Tonie removals can be canceled for **five seconds before commitment**. Committed cloud deletion and Clear all have no audio recovery. Local-file deletion offers only a separate short pre-delete cancellation toast.
- `POST /api/tonies-actions/{action_id}/undo` requires sign-in/same-origin and accepts no client snapshot. The server's `undo_available`, `undo_pending` and `undo_expires_at` fields control availability.
- Undo consumes its capability before one guarded write, even on failure. Stale/uncertain state returns `409` with `needs_refresh: true`; refresh, do not retry. Only a confirmed inverse returns canonical `content`; cancellation returns `status: canceled`.

There is no proven provider compare-and-swap contract: another device can still race the final read/write boundary. Undo is not a backup.

## Published images

The release workflow builds and smoke-tests before publishing `ghcr.io/c1rca/tonies-yt` for `linux/amd64` and `linux/arm64`. A `v*` tag publishes `X.Y.Z`, `X.Y`, `X` and `latest`; manual runs test without publishing. Check the package is Public after first publication. Compose pulls `TONIES_IMAGE_TAG` (`latest` by default), or builds the checkout if pulling fails.

## Audio processing

New YouTube downloads and imports are encoded once to **128 kbps stereo MP3**. The encoder measures EBU R128 loudness and applies static gain toward **-14 LUFS**, a **-1 dBFS** limiter and at most **+12 dB** boost. Silence gets no gain; failed measurement skips levelling, not conversion. Existing library files are not reprocessed.

Editor clips stream-copy the MP3 without re-encoding; originals remain. Waveform loudness bars are read-only visualization and do not alter the encoding policy.

## Audio tooling and dependency audits

The image contains ffmpeg, CA certificates, curl, checksum-verified **Deno 2.9.7** (amd64/arm64) and `yt-dlp[default]` with its matched `yt-dlp-ejs` solver. Runtime component downloads, ambient configuration and external plugins are disabled; no browser is installed.

Direct Python dependencies are pinned except yt-dlp; base/APT/transitive packages can move. Builds are **not fully reproducible**. Record image IDs and dependency inventories. Pip is removed after build; never enable runtime installs, user-site packages or writable code to update tools.

```bash
# Outside the production container:
uv tool run pip-audit -r requirements.txt
# Rebuild with a current base, then test before recreating:
docker compose -p tonies-local build --pull tonies-yt
```

Python scans do not cover Debian/ffmpeg/Deno or prove safety. Use current OS/container scans too and assess reported applicability.

## Version labels

`app.__version__` in `app/__init__.py` supplies every page footer and startup/status version. Pages use `no-store`; static assets revalidate with ETag/304 so browsers can reuse unchanged cached responses. Restart/rebuild and reload to see changes; already-running processes/tabs do not hot-update. Keep the Docker image label and changelog release entry aligned.

## Tests and isolated acceptance

Follow [Contributing](../CONTRIBUTING.md) for offline tests. They do not prove live cloud, YouTube extraction, TLS, Windows or ARM64 operation.

For release acceptance, build the real image with a **unique project, unused loopback port and fresh data**. Verify setup/login, local import/range playback, ffmpeg/Deno and caches; inspect effective UID, read-only code, data/tmp writes, limits and capabilities. Never mount production data. Clean up only the isolated project's artifacts. Keep exported logs private: titles/URLs may remain even with secret redaction.
