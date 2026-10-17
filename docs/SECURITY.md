# Security and deployment boundaries

## Safe defaults

The image runs the application as fixed UID/GID **10001:10001**. There is no root entrypoint, `gosu`, ownership repair on startup, or root fallback. Code, Python packages and Deno remain root-owned. The supplied Compose configuration also enforces:

- publication only on `127.0.0.1:8090`;
- a read-only root filesystem, all Linux capabilities dropped and `no-new-privileges`;
- one project-scoped data volume at `/app/data`, initialized with app ownership for a new installation;
- disposable `/tmp` (256 MiB, `noexec,nosuid,nodev`), including home, Deno and yt-dlp caches;
- a 1 GiB memory limit, two CPUs, 256-process limit, bounded open files and rotated Docker logs.

These restrictions are **Compose settings**, not properties Docker automatically applies to every `docker run`. Keep them when creating custom deployments. UID and read-only errors on old data are migration errors, not reasons to run the app as root. See [Operations](OPERATIONS.md#existing-root-owned-bind-mounts).

Containers reduce the impact of a compromised process; they do not make hostile media safe, isolate the app from the Docker administrator, or secure an already-compromised host. Never mount the Docker socket or host root, grant privileged mode, or use host networking. Outbound network access is still necessary for Tonies, YouTube and downloader components. Persistent data has no disk quota in this configuration: monitor free space and delete unneeded downloads.

## First run and credentials

Complete setup from the Docker host at `http://localhost:8090` before enabling remote access. The first allowed visitor can create the login while the vault is uninitialized; loopback is not protection from other users/processes on the same machine. For a remote Docker host use an SSH local port forward rather than exposing an unconfigured app.

Use a unique, long app passphrase and protect the host account. The vault encrypts Tonies credentials with a vault key protected by a key derived from that passphrase. It does **not** encrypt audio, logs, job history, settings or whole backups. Offline theft of the vault allows password guessing; encryption cannot protect a running unlocked app from its administrator. Environment credentials are visible to Docker administrators and are not encrypted by `.env`.

A restart or **Lock** requires browsers to sign in again. Do not share cookies, populated `.env`, vault files or debug exports. Session cookies are HttpOnly/SameSite=Strict, and HTTPS deployments must produce Secure cookies. Authentication, same-origin write checks and an explicit Host allowlist complement—not replace—TLS and a host firewall.

## LAN and HTTPS proxies

Loopback is intentional. `TONIES_BIND_ADDRESS` is an explicit opt-in; prefer the host's specific LAN IP over `0.0.0.0`. Add the exact hostname/IP to `APP_ALLOWED_HOSTS` and restrict clients with a firewall. Do not use a wildcard allowlist. HTTP on a LAN exposes credentials, cookies and audio to network observers: use HTTPS outside localhost, including on shared home Wi-Fi.

For HTTPS set `APP_PUBLIC_URL` to the canonical browser origin and include its hostname in `APP_ALLOWED_HOSTS`. Preserve the original `Host`. The default Uvicorn command disables proxy-header trust. Only enable it for a known reverse proxy and supply its exact source IP(s); never trust `*` or client-supplied forwarding headers. See [proxy configuration](OPERATIONS.md#running-behind-a-reverse-proxy). Do not expose port 8090 directly to the internet. A public deployment needs operator-managed TLS, firewall/access policy, patching and monitoring; this repository is not a turnkey public hosting platform.

## Uploads and external tools

Only import media you trust and have permission to use. Enforce request and file limits in the app and proxy; the container memory/tmpfs/process limits are last-resort resource controls, not upload validation. A filled temporary filesystem or exhausted persistent volume can fail legitimate uploads. Keep the configured upload ceiling below scratch capacity and allow for multipart/concurrent-request overhead.

Deno 2.9.7 is downloaded from pinned upstream archives and checked against embedded SHA-256 hashes for amd64/arm64. Builds install current `yt-dlp[default]`, which resolves the matching `yt-dlp-ejs` solver wheel during installation. The downloader disables runtime component downloads, ambient configuration and external plugins; do not re-enable `ejs:github` to bypass a missing build dependency. This still trusts upstream packages at build time and is not a complete supply-chain lock. Do not enable Python user-site packages or writable code paths to make an in-app updater work. Pip is removed after the build; the app's runtime update endpoint refuses installation. Update the container by rebuilding/recreating it, not by installing code into the running image.

## Maintenance and reporting

Rebuild regularly with current base/dependency patches, then run the offline suite and isolated container acceptance. A successful `pip-audit` only means no advisories were reported for that resolved Python dependency set at that time; it does not scan Debian/ffmpeg/Deno, assess reachability, or prove the app secure. Keep OS/container scanners and their advisory databases current. See [Operations](OPERATIONS.md#audio-tooling-and-dependency-audits).

Report suspected vulnerabilities privately to the repository maintainer through an available private security advisory/contact channel. If none is offered, ask for a private reporting channel without posting the exploit or secrets publicly. Include affected version/image ID, configuration with secrets removed, minimal synthetic reproduction, expected/actual behavior and impact. Never attach real credentials, session tokens, vault data, private audio or unredacted logs. Do not test against other people's instances or open public exploit issues without coordination.
