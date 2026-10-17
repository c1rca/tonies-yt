# Quick Start

## Prerequisites

- Docker Engine + Compose **2.24 or later**, or Docker Desktop using Linux containers.
- A Tonies account email and password for the setup form. Local playback itself does not contact Tonies.
- A trusted machine with enough free disk for your library. Existing users: **read [migration](OPERATIONS.md#existing-root-owned-bind-mounts) before starting**; the new named volume does not automatically import old `./data`.

## 1. Create configuration

Linux/macOS:
```bash
cp .env.example .env
chmod 600 .env
```

Windows PowerShell:
```powershell
Copy-Item .env.example .env
```
Restrict `.env` to your Windows account using its file permissions. Normally do not put Tonies credentials in it; setup stores them in the app's encrypted vault. Optional `TONIES_EMAIL`/`TONIES_PASSWORD` override that vault account, but an app login is still required.

## 2. Start locally

```bash
docker compose -p tonies-local up -d --build
docker compose -p tonies-local ps
```

Open **http://localhost:8090** on the Docker host. The default is **loopback only**, not accessible from your phone/LAN. For a remote Docker host, use `ssh -L 8090:127.0.0.1:8090 user@docker-host`, then open localhost on your workstation. Do not expose an unconfigured instance.

The image runs as **10001:10001**, never root. Compose makes code read-only, drops all capabilities and persists data in the project's `tonies-data` named volume. `/tmp`, home and tool caches are disposable. No host directories are mounted by default. Container port 8080 maps to loopback port 8090; set `TONIES_PORT=8091` in `.env` if needed. `APP_PORT` alone does not change the image command.

## 3. Complete setup immediately

1. Create a unique app username and a long, unique passphrase. The first visitor to an uninitialized instance creates the login. Keep the passphrase safe; it cannot be recovered.
2. Enter Tonies credentials (or use the optional environment overrides). Setup requires both cloud credential fields unless environment credentials are configured; creating the vault does not verify them against Tonies.
3. Import audio through **Browse**, then preview it in the library. To write to Tonies, select a Creative Tonie and add audio.

Every browser needs its own sign-in. A restart or **Lock** signs browsers out. See [Security](SECURITY.md) for vault and host-administrator boundaries. The volume includes unencrypted audio/logs/history/settings and the encrypted vault: protect and [back it up](OPERATIONS.md#backup--restore). Never use `docker compose down -v` unless you intend to delete that installation's data.

## Remote/LAN access is opt-in

Complete setup locally first. For remote use configure HTTPS, an explicit allowed host and a firewall. Example `.env` additions for a proxy with canonical origin:

```dotenv
APP_ALLOWED_HOSTS=localhost,127.0.0.1,[::1],tonies.example.com
APP_PUBLIC_URL=https://tonies.example.com
```

A host-based proxy can leave `TONIES_BIND_ADDRESS=127.0.0.1`. A LAN listener instead needs `TONIES_BIND_ADDRESS=<host-LAN-IP>` and that IP/hostname in `APP_ALLOWED_HOSTS`; do not use plaintext HTTP outside localhost. Follow the complete [proxy instructions](OPERATIONS.md#running-behind-a-reverse-proxy), including exact trusted proxy IPs. A bind address alone is not a safe remote deployment.

## Stop, restart and update

Always use the same project name; another `-p` value creates separate state.

```bash
docker compose -p tonies-local stop tonies-yt
docker compose -p tonies-local up -d
# After source/dependency/base updates, back up first, then:
docker compose -p tonies-local build --pull tonies-yt
docker compose -p tonies-local up -d --no-build tonies-yt
```

After `.env` changes use `up -d --force-recreate tonies-yt`. Logs: `docker compose -p tonies-local logs --tail 100 tonies-yt`. Health: `http://localhost:8090/api/health` (web availability only, not Tonies authentication). API docs, once signed in: `http://localhost:8090/docs`.

## Native setup (Linux/macOS, without Docker)

The hardened native runtime requires POSIX file locking and process-group controls. **Native Windows Python is not supported by this branch**; its former native command cannot import the locking module. On Windows use Docker Desktop with Linux containers (recommended), or a Linux environment such as WSL2 with the Linux instructions. Do not remove vault locks or process bounds to work around this.

Use Python 3.12, ffmpeg and Deno on PATH. Native runs do not receive the container sandbox or automatically load `.env`.

Linux/macOS:
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export DATA_DIR=./data
export LOG_FILE=./data/logs/toni-auto.log
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --no-proxy-headers
```

Do not reuse `/app/data` on the native host. The legacy `scripts/setup-windows.ps1` targets native Windows and resets its checkout; it is not an installer for this hardened branch. Use Docker Desktop's Linux-container flow above instead.

## Troubleshooting

- **Permission denied on data:** do not run as root or `chmod 777`. See the explicit [backed-up migration](OPERATIONS.md#existing-root-owned-bind-mounts).
- **LAN unreachable / invalid Host:** intentional defaults. Configure bind, allowlist, HTTPS and firewall together.
- **Tonies sign-in fails:** check Settings and any environment overrides; restarting discards cloud tokens. Do not delete the vault/library to fix cloud sign-in.
- **Forgot app passphrase:** see [reset instructions](OPERATIONS.md#reset-a-forgotten-app-password); there is no recovery backdoor.
