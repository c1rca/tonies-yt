# Security and deployment boundaries

## Safe defaults

The image runs as **10001:10001** from startup, with no root fallback or automatic ownership repair. The supplied Compose file adds:

- Loopback-only publication at **127.0.0.1:8090**.
- Read-only code, all capabilities dropped and `no-new-privileges`.
- One private, project-scoped data volume; disposable `/tmp` with `noexec,nosuid,nodev`.
- Memory, CPU, process and open-file limits, plus rotating Docker logs.

Keep these controls in custom deployments; `docker run` does not apply Compose settings automatically. Old-data permission errors require [migration](OPERATIONS.md#existing-root-owned-bind-mounts), not root or `chmod 777`.

Containers do not protect against a compromised host or Docker administrator, or make hostile media safe. Never mount host root/the Docker socket or enable privileged mode/host networking. Outbound access is required. Persistent audio/logs have no disk quota: monitor free space.

## First run and credentials

1. Complete setup over localhost or an SSH tunnel **before** remote exposure. The first allowed visitor creates the login; other users/processes on the host can reach loopback too.
2. Use a unique app passphrase of at least 12 characters. It protects the vault key that encrypts Tonies credentials, not your whole installation.
3. Protect `.env`, vault files, cookies, logs and backups. Never share populated configuration or debug exports publicly.

**App login and Tonies sign-in are separate.** Optional environment credentials override the cloud account, not app authentication. Environment values are visible to administrators. Audio, logs, history, settings and backup archives are not encrypted by the vault; stolen vaults permit offline password guessing.

Each browser receives its own HttpOnly, SameSite=Strict session cookie. **Lock** or restart signs all browsers out; changing the app password signs other browsers out. Login throttling, same-origin writes and the Host allowlist supplement TLS, not replace it. [Password reset](OPERATIONS.md#reset-a-forgotten-app-password) cannot decrypt old Tonies credentials without the old password.

## LAN and HTTPS proxies

Use **HTTPS outside localhost**, including shared home Wi-Fi. Plain HTTP exposes credentials, cookies and audio to network observers.

- Complete setup locally, then configure a firewall and exact `APP_ALLOWED_HOSTS`; never use a wildcard.
- Keep a host proxy's backend on loopback. If a LAN listener is needed, deliberately set `TONIES_BIND_ADDRESS` to the specific host IP, not an unrestricted address.
- Set `APP_PUBLIC_URL` to the canonical HTTPS origin and preserve `Host`.
- Forwarded headers are ignored by default. Enable them only for your proxy's verified source IP(s), never `*`, and overwrite client forwarding headers at the proxy.

Follow the [complete proxy procedure](OPERATIONS.md#running-behind-a-reverse-proxy), then verify Secure cookies, writes and SSE through HTTPS. Do not expose port 8090 directly to the internet. Public hosting requires operator-managed access policy, TLS, patching and monitoring.

## Uploads and external tools

Import only trusted media you have permission to use. Align proxy/request/file limits with temporary storage and concurrency. Container resource limits are a last resort, not media validation.

The image pins/checksums Deno and installs yt-dlp's matching solver at build time. Downloader runtime component downloads, ambient configuration and external plugins are disabled. Do not re-enable remote solvers or writable packages as a workaround.

Pip is removed from the runtime image, and the app refuses runtime tool installation. Update by rebuilding/recreating, not by installing into a running container. See [tooling policy](OPERATIONS.md#audio-tooling-and-dependency-audits).

## Maintenance and reporting

Test dependency/base updates with offline tests and isolated image acceptance. Python advisory scans do not cover Debian, ffmpeg or Deno, or prove exploit resistance; use current OS/container scanners too.

Report vulnerabilities privately to the maintainer. If no private channel is listed, ask for one without posting exploit details. Include version/image ID, sanitized configuration, a synthetic reproduction and impact. Do not attach credentials, tokens, vault files, private audio or unredacted logs, or test other people's instances.
