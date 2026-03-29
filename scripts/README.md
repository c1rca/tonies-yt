# Scripts

`scripts/` is split by intended audience:

## Supported (user-facing)
- `setup-windows.ps1` — Windows non-Docker bootstrap/setup script.

For usage details, prerequisites, and the recommended Docker-first path, see:
- README: [Windows without Docker Compose](../README.md#windows-without-docker-compose)
- Quickstart: [`../docs/QUICKSTART.md`](../docs/QUICKSTART.md)

## Maintainer/internal
- `scripts/dev/*` — one-off checks, regression helpers, and local diagnostics used during development.

Notes:
- Dev scripts are not part of the stable user workflow.
- Environment-specific/local experiment scripts should stay under `scripts/dev/`.
