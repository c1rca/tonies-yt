# Tonies-YT

A web app for finding, clipping and adding audio to Creative Tonies.

- Search YouTube or choose videos from a playlist.
- Import, preview and clip local audio.
- Add tracks, rename them and change their order.
- Follow downloads and uploads in Activity.

![Tonies-YT app screenshot](docs/assets/app-screenshot.png)

## Get started

Install [Docker](https://docs.docker.com/get-docker/) with Compose **2.24+**. On Windows or macOS, use Docker Desktop with Linux containers.

**Upgrading an existing installation?** Old `./data` is **not imported automatically**. [Back up and migrate it](docs/OPERATIONS.md#existing-root-owned-bind-mounts) before starting.

```bash
mkdir tonies-yt && cd tonies-yt
curl -fsSLO https://raw.githubusercontent.com/c1rca/tonies-yt/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/c1rca/tonies-yt/main/.env.example -o .env
docker compose -p tonies-local up -d
```

Run these commands in a Linux/macOS shell or WSL2. The published image supports `linux/amd64` and `linux/arm64`; no local build is needed.

1. Open **http://localhost:8090** on the Docker host.
2. Create an app login immediately, then enter your Tonies credentials. The first visitor creates the login; use a unique passphrase of at least 12 characters.
3. Choose a Tonie, then search YouTube or use **Local files → Import file**.

Your app login is separate from your Tonies account. Tonies credentials are stored in an encrypted vault. **Lock** and restarting the app sign every browser out.

## Update

[Back up first](docs/OPERATIONS.md#backup--restore), then run from the same directory:

```bash
docker compose -p tonies-local pull tonies-yt
docker compose -p tonies-local up -d tonies-yt
```

Keep using the same project name (`-p tonies-local`). Set `TONIES_IMAGE_TAG` in `.env` to pin a published version instead of following `latest`.

## Keep your data safe

- Docker listens on **127.0.0.1:8090** by default and runs as **10001:10001**, with no root fallback. For phone or remote access, [configure HTTPS, allowed hosts and proxy trust](docs/OPERATIONS.md#running-behind-a-reverse-proxy) after local setup.
- Back up the **whole data volume and `.env`**. Never run `down -v` unless you intend to delete that installation's data. Audio and backups are not encrypted by the vault.
- Undo has limits: committed Tonies deletions and **Clear all** cannot be recovered by the app. Read [Undo and cancellation](docs/OPERATIONS.md#undo-and-cancellation).

**Unofficial project:** not affiliated with or endorsed by tonies GmbH. It uses a private Tonies API that may change without notice. “Tonies”, “Toniebox” and “Creative-Tonie” are tonies GmbH trademarks. Only use audio you have the right to use.

## Guides

- [Quick Start](docs/QUICKSTART.md) — checkout builds, native setup and troubleshooting
- [App overview](docs/APP_OVERVIEW.md) — workspace and architecture
- [Operations](docs/OPERATIONS.md) — backups, migration, password reset and proxies
- [Security](docs/SECURITY.md) — credentials and deployment boundaries
- [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)
