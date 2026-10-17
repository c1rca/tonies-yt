# Quick Start

## Prerequisites
- Docker Desktop using Linux containers (Windows), or Docker Engine + Compose plugin (Linux)
- Your Tonies account email and password

## 1) Create `.env`

Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

Linux:
```bash
cp .env.example .env
```

No Tonies credentials go in `.env` for normal setup; the app keeps them in its encrypted vault. Setting `TONIES_EMAIL` and `TONIES_PASSWORD` instead is supported: they replace the vault's Tonies account, but you still create an app login.

## 2) Start with Docker Compose (recommended)

```bash
docker compose up -d --build
```

Open:
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`

Compose maps host port 8090 to container port 8080 and mounts `./data` at `/app/data`. The image includes ffmpeg, Deno and yt-dlp.

## 3) First use
1. Open the app.
2. First run: choose an app username and password, then enter your Tonies email and password. Do this right away: until setup is done, whoever opens the app first creates the login. Save the app password somewhere safe; it cannot be recovered.
3. Select a Creative Tonie.
4. Search YouTube, drag a Local MP3 Library file onto a Tonie, or use **Browse** to import local audio first.

Every browser or phone signs in with the app username and password. After a restart, or after **Lock**, everyone signs in again. Tonies sign-in then happens automatically on the next action.

`./data` contains downloads, logs, job history, the encrypted vault and app settings. Keep it private, back it up and do not commit it.

## Native setup (without Docker)

Use Python 3.12, with **ffmpeg and Deno on PATH**.

Linux:
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --upgrade yt-dlp
export DATA_DIR=./data
export LOG_FILE=./data/logs/toni-auto.log
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Windows PowerShell:
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
$env:DATA_DIR = "./data"
$env:LOG_FILE = "./data/logs/toni-auto.log"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Native runs read exported environment variables; Compose loads `.env` through `env_file`. Do not reuse the container's `/app/data` paths on a native host.

On Windows, `scripts/setup-windows.ps1` can do the install for you. It checks out `main` with `git reset --hard`, so only run it in a clean checkout.

## Stop / restart
```bash
docker compose stop tonies-yt
docker compose up -d
```

After changing source or build inputs, use `docker compose up -d --build`.

## Logs
- UI: `http://localhost:8090/logs`
- File with the supplied `.env.example`: `./data/logs/toni-auto.log`
- Container: `docker compose logs --tail 100 tonies-yt`

## Fast fixes

### Port 8090 already in use
Change only the host side of the mapping in `docker-compose.yml`, for example `"8091:8080"`. The container always listens on 8080; changing `APP_PORT` alone does not move it.

### Tonies sign-in fails
Check the Tonies email and password in **Settings**. If `TONIES_EMAIL`/`TONIES_PASSWORD` are set in `.env`, those win over Settings. Restarting the app clears its in-memory tokens:
```bash
docker compose restart tonies-yt
```
Do not delete `data/` or the vault to fix Tonies sign-in.

### Forgot the app password
See [Operations → Reset a forgotten app password](OPERATIONS.md#reset-a-forgotten-app-password).
