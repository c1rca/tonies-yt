# Operations

Short runbook for running and troubleshooting Tonies-YT.

## Start
Foreground:
```bash
docker compose up --build
```

Background:
```bash
docker compose up -d --build
```

## Stop
```bash
docker compose down
```

## Restart cleanly
```bash
docker compose down
docker compose up -d --build --force-recreate
```

## Health checks
- App: `http://localhost:8090`
- API docs: `http://localhost:8090/docs`
- Health: `http://localhost:8090/api/health`

CLI:
```bash
curl -s http://localhost:8090/api/health
```

## Logs
UI:
- `http://localhost:8090/logs`

Container:
```bash
docker compose logs -f toni-auto
```

File:
- `./data/logs/tonies-yt.log`

## Config
Common `.env` settings:
- `LOG_LEVEL`
- `LOG_FILE=/app/data/logs/tonies-yt.log`

Advanced Tonies overrides:
- `TONIES_LOGIN_URL`
- `TONIES_APP_URL`

After config changes:
```bash
docker compose up -d --build
```

## Setup / auth routes
- `/setup` — first-time setup
- `/login` — app login
- `/settings` — app password, Tonies credentials, search settings, tool versions
- `/account` — legacy redirect to `/settings`

## Reset Tonies session
Linux/macOS:
```bash
rm -f data/tonies-storage-state.json
docker compose up -d --build
```

Windows PowerShell:
```powershell
Remove-Item data/tonies-storage-state.json -ErrorAction SilentlyContinue
docker compose up -d --build
```

## Port change
If `8090` is busy, change `docker-compose.yml`:
```yaml
ports:
  - "8091:8080"
```

## Backup
Back up:
- `.env`
- `data/`

Restore both, then run:
```bash
docker compose up -d --build
```

## Release checklist
- `.env.example` updated
- Main flow tested (search → select → upload → sync)
- Local library Browse import tested (file → normalize → appears in library)
- Local library upload-to-Tonies tested (button + drag/drop)
- Rename / reorder / delete tested
- Settings tested
- Logs page tested
- Confirm only one Tonies mutation runs at a time while search still works as expected
