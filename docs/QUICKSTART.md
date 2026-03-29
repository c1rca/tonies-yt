# Quick Start

Get Tonies-YT running locally in a few minutes.

## Prerequisites
- Docker Desktop (Windows) or Docker Engine + Compose plugin (Linux)
- Git
- Tonies account credentials

## 1) Clone
```bash
git clone <YOUR_REPO_URL>
cd tonies-yt
```

## 2) Create `.env`
Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

Linux/macOS:
```bash
cp .env.example .env
```

No Tonies credentials need to be added to `.env` for normal setup.

On first launch, the app will prompt you to finish setup/login in the browser.

`./data` is runtime state (downloads/logs/session storage). It is created/used at runtime and should not be committed to git.

## 3) Start (recommended: Docker Compose)
```bash
docker compose up -d --build
```

Open:
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`

## Windows alternative (without Docker Compose)
Use the Windows setup script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup-windows.ps1
```

The script can install missing prerequisites, set up Python 3.12 + venv, install dependencies, and print the exact command to run the web server.

## 4) First use
1. Open the app
2. Complete setup/login if prompted
3. Select a Creative Tonies
4. Search YouTube, drag a file from Local MP3 Library onto a Tonie, or use **Browse** to import local audio into the library first

## Stop / restart
```bash
docker compose down
docker compose up -d --build
```

## Logs
- UI: `http://localhost:8090/logs`
- File: `./data/logs/tonies-yt.log`

## Fast fixes
### Port 8090 already in use
Change `docker-compose.yml`:
```yaml
ports:
  - "8091:8080"
```

### Session/login issues
Linux/macOS:
```bash
rm -f data/tonies-storage-state.json
```

Windows PowerShell:
```powershell
Remove-Item data/tonies-storage-state.json -ErrorAction SilentlyContinue
```

Then restart:
```bash
docker compose up -d --build
```
