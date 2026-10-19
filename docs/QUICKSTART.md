# Quick Start

For the simplest installation, use the [README quick start](../README.md#get-started). This guide covers a source checkout and native runs.

## Prerequisites

- Docker Engine with Compose **2.24+**, or Docker Desktop with Linux containers.
- A trusted host with room for your audio library.
- Tonies credentials for setup; local playback itself does not contact Tonies.

**Existing installation:** [back up and migrate old data](OPERATIONS.md#existing-root-owned-bind-mounts) first. The named volume does not import `./data` automatically.

## Run a checkout

1. For a fresh checkout, run these commands in a Linux/macOS shell or WSL2:
   ```bash
   git clone https://github.com/c1rca/tonies-yt.git
   cd tonies-yt
   cp .env.example .env
   chmod 600 .env
   ```

2. Start the app:
   ```bash
   docker compose -p tonies-local pull tonies-yt
   docker compose -p tonies-local up -d --no-build
   ```
   This pulls the published amd64/arm64 image without building locally. Set `TONIES_IMAGE_TAG=3.1.0` in `.env` to pin the release instead of using `latest`. To build this checkout yourself, skip the pull and use `up -d --build` instead. Set `TONIES_IMAGE_TAG=local` before building to keep your build separate from published tags; a later `pull` can replace a locally built image with the same tag.
3. Open **http://localhost:8090** and complete setup immediately. Create an app login with a unique passphrase of at least 12 characters, then enter Tonies credentials. Setup saves them but does not verify cloud sign-in.
4. Choose a Tonie and add audio, or use **Local files → Import file** to import and preview a file locally.

The first visitor creates the login. On a remote Docker host, use `ssh -L 8090:127.0.0.1:8090 user@docker-host`, then open localhost on your workstation. Do not expose an unconfigured app.

## Configuration

Edit [`.env.example`](../.env.example)'s settings in your own `.env`:

- `TONIES_PORT`: host port, **8090** by default. The container always listens on **8080**; `APP_PORT` does not change its command.
- `TONIES_IMAGE_TAG`: published version, or `latest` by default.
- `LOG_LEVEL`, `LOG_FILE`: logging. Compose fixes the file at `/app/data/logs/toni-auto.log`.
- `APP_MAX_UPLOAD_BYTES`: per-file ceiling, **104857600** (100 MiB) by default.
- `TONIES_EMAIL` and `TONIES_PASSWORD`: optional plaintext overrides for the vault's Tonies account. Both must be set; the app login is still required.
- `TONIES_CREATIVE_UPLOAD_URL` or `TONIES_CHARACTER_NAME`: optional default target for jobs that do not specify a Tonie.

The app runs as **10001:10001** with no root fallback. Data persists in the project's named volume at `/app/data`; tool caches are disposable. [Back up the whole volume and `.env`](OPERATIONS.md#backup--restore). `down -v` deletes named-volume data.

For phone/LAN access, complete local setup first, then follow [HTTPS proxy configuration](OPERATIONS.md#running-behind-a-reverse-proxy). It covers `TONIES_BIND_ADDRESS`, exact `APP_ALLOWED_HOSTS` and `APP_PUBLIC_URL`. A different bind address alone is not safe remote access.

## Stop and update

Use the same `-p tonies-local` throughout. See [lifecycle commands](OPERATIONS.md#lifecycle-and-verification) for updates, logs and health checks. API docs are at `http://localhost:8090/docs` after sign-in. A restart or **Lock** signs every browser out.

## Native setup (Linux/macOS, without Docker)

Install **Python 3.12**, **ffmpeg/ffprobe** and **Deno** on PATH, then run from the checkout:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export DATA_DIR=./data
export LOG_FILE=./data/logs/toni-auto.log
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --no-proxy-headers
```

Native runs do not load `.env` automatically or receive Docker's sandbox. Use host-relative data/log paths, not `/app/data`.

**Native Windows Python is unsupported:** the runtime requires POSIX file locks and process groups. Use Docker Desktop's Linux containers or WSL2.

## Troubleshooting

- **Permission denied:** stop and check [data ownership/migration](OPERATIONS.md#existing-root-owned-bind-mounts). Do not run as root or use `chmod 777`.
- **LAN unreachable / invalid Host:** configure bind, allowed hosts, HTTPS and firewall together.
- **Tonies sign-in fails:** check Settings and environment overrides. Do not delete the vault to fix cloud authentication.
- **Forgot the app passphrase:** follow [reset instructions](OPERATIONS.md#reset-a-forgotten-app-password). The old encrypted credentials cannot be recovered without it.
