# Scripts

## Install

Use the [Docker quick start](../README.md#get-started). `setup-windows.ps1` is a legacy native-Windows bootstrap, not a supported installer; it can reset its checkout. On Windows use Docker Desktop's Linux containers or WSL2.

## Live acceptance (opt-in, uses your real Tonies account)

`live_queue_acceptance.py` makes **real cloud writes**: it uploads three disposable clips, edits/deletes those clips and checks the starting snapshot. Run it only deliberately against an appropriate test Tonie, never as an ordinary offline test.

## Maintainer/internal

`scripts/dev/` contains diagnostics and regression helpers, not a stable user API. For UI work without Tonies credentials:

```bash
.venv/bin/python scripts/dev/fake_cloud_app.py --port 8099
```

The helper requires development dependencies and ffmpeg, uses temporary data and a fake Tonies client, and writes a generated dev password/session into that data directory. Keep it on loopback. The fake client does **not** fake YouTube: do not search/download if you need fully offline work. Keep local experiments under `scripts/dev/`.
