# Scripts

`scripts/` is split by intended audience:

## Install
Use Docker (see the [README quick start](../README.md#quick-start-recommended) and [`../docs/QUICKSTART.md`](../docs/QUICKSTART.md)).
- `setup-windows.ps1` — legacy native-Windows bootstrap. Native Windows Python is no longer supported; Windows users should run the Docker image (Docker Desktop with Linux containers or WSL2).

## Live acceptance (opt-in, uses your real Tonies account)
- `live_queue_acceptance.py` — uploads three disposable clips, edits and deletes only those, then checks the Tonie matches its starting snapshot. Run it only deliberately; it makes real cloud writes.

## Maintainer/internal
- `scripts/dev/*` — one-off checks, regression helpers and local diagnostics, including `fake_cloud_app.py` (the app against a fake Tonies cloud, for UI work without an account).

Notes:
- Dev scripts are not part of the stable user workflow.
- Environment-specific/local experiment scripts should stay under `scripts/dev/`.
