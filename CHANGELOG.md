# Changelog

All notable changes to Tonies-YT. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Volume levelling: every track added (YouTube or imported) is brought to the same loudness, so one song isn't quiet and the next one loud.
- Audio editor: an Edit button on YouTube results and Local files opens a waveform. Play it, drag the start and end handles (or type times, or use the arrow keys), zoom in on the clip and listen to it, then add it or save it as a new file. The clip is cut without re-encoding; the original file stays.
- Playlists: paste a playlist link to list its first 50 videos.
- Select several YouTube results and add them in one go; they land in the order shown.
- Delete several Local files at once: **Select**, tick them (or **Select all**), then **Delete**, with the same short Undo as a single delete.
- A ready-made image for PCs and Raspberry Pis (`ghcr.io/c1rca/tonies-yt`), published with every release; Compose pulls it, so installing no longer needs a build. New "Get started" section in the README.

### Changed
- YouTube audio is encoded once by the app instead of twice.
- At most three YouTube downloads run at once, and a plain download failure is retried once.
- Local files rows are tidier: a round Play button and Add stay one tap away, and Edit and clip, Add to top / bottom, Download and Delete moved into the row's **⋯** menu (also on right-click or long-press). Rows show the track's title and a readable size, and the file that's playing is highlighted. Ticking several files now starts with **Select**, like Tracks.
- Editing a YouTube result no longer waits behind tracks that are still downloading to a Tonie (the editor has its own download lane), and each YouTube download skips a needless extra pass (about 1–2 s faster).
- Internal restructure into small, named files; no visible changes.

### Fixed
- Coming back to the workspace reopens the Add audio tab you were on (YouTube or Local files) and, on phones, the same view (Tracks, Add audio or Activity).
- Coming back to the workspace from Settings, Logs or another page no longer flashes: it appears complete (Tonies, tracks, the Undo bar) instead of drawing empty panes first, the page cross-fades, Tonie pictures no longer blink when the status changes, and the page's files are cached between visits.
- Adding several videos at once: a track that failed quickly could let a later one land on the Tonie before an earlier one still downloading.

## [3.0.0] - 2026-10-17

A new interface: the audio workspace, with Undo, a queue that saves library changes in batches, local playback in the browser, and a locked-down, non-root Docker image.

### Before you upgrade
- **Data folder ownership.** The container now runs as UID/GID 10001 instead of root, and Compose uses a named volume. Back up `data/`, then copy it once with app ownership, as in [existing root-owned bind mounts](docs/OPERATIONS.md#existing-root-owned-bind-mounts). Your app login and Tonies vault keep working. The app never falls back to root.
- **Allowed hostnames.** Only names in `APP_ALLOWED_HOSTS` are accepted (default `localhost,127.0.0.1,::1`); other hosts get `400`. Add your LAN IP and any reverse-proxy hostname.
- **Reverse proxies.** Forwarded headers are ignored unless you trust your proxy's exact IP, or set `APP_PUBLIC_URL` to the HTTPS address. See [running behind a reverse proxy](docs/OPERATIONS.md#running-behind-a-reverse-proxy).
- **LAN access.** Compose listens on `127.0.0.1:8090` by default. Set `TONIES_BIND_ADDRESS` to reach it from other devices.
- **yt-dlp updates** happen by rebuilding the image; Settings no longer updates it in place.

### Added
- **Audio workspace.** Pick a Tonie, then see its tracks and Add audio side by side on desktop; on phones, Tracks / Add audio / Activity tabs with a sticky header showing the target Tonie and free time.
- **Undo** for the last rename, move or new upload on each Tonie, plus five seconds to cancel a removal before it is sent. Undo refuses rather than guesses if the Tonie was changed elsewhere; deleted tracks and Clear all can't be undone.
- **Action queue.** Deletes, moves, renames, Clear all and uploads are queued per Tonie and saved in batches (one read, one write, one confirming read). The list shows each change straight away.
- Up to three uploads send their audio at once and land in the order and position they were queued; a placeholder marks where each will appear.
- **Local playback.** Play and pause your MP3s in the page, with seeking and volume; playback carries on while you browse.
- Per-track menus, inline rename (Enter to save, Escape to cancel), multi-select removal and a separate Clear all.
- **Activity** lists every job and change with its outcome, keeps errors until you dismiss them, and shows when Tonies accepted a track but is still processing it.
- Light, dark or system appearance, the same on every page (Settings, Logs, Sign-in, Setup); reduced-motion and keyboard support.
- The app version in every page footer.
- `POST/GET /api/tonies-actions`, and `tonies_actions` events on `/api/events`.
- `scripts/dev/fake_cloud_app.py`: run the app against a fake Tonies cloud, for UI work without an account.

### Changed
- Docker: read-only code, no Linux capabilities, no privilege escalation, bounded scratch space, resource and log limits.
- New app passwords need at least 12 characters; existing logins still work. The vault is written privately and atomically, and media and session handling have explicit limits.
- Deno is pinned and checksum-verified, with matching yt-dlp solvers built into the image; runtime downloads and plugins are off. Build-only pip is removed from the runtime image.
- Changes target tracks by ID and position anchors, so a queued change still hits the right track after the list moves, and one failed change no longer blocks the rest of its batch.
- Native Windows Python is no longer supported; on Windows use Docker Desktop (Linux containers) or WSL2.

### Removed
- The in-app yt-dlp updater and the "online" label in the Settings menu.

### Fixed
- Undo keeps working after Tonies finishes processing a new upload.

## [2.0.0] - 2026-10-17

Tonies-YT now talks to the Tonies cloud directly over HTTPS instead of driving my.tonies.com in a headless browser, and the app login now actually protects the app.

### Breaking changes
- **Sign-in is per browser.** Signing in sets a session cookie, and every page and API call needs it. Before, unlocking from one device opened the whole API to anyone on the network. Scripts that call the API must sign in first (`POST /api/setup/login`) and keep the cookie.
- **`TONIES_EMAIL` / `TONIES_PASSWORD` no longer skip the app login.** They still choose the Tonies account, but you create an app login on first run like everyone else (the Tonies fields on the setup page become optional).
- **Removed settings:** `TONIES_STORAGE_STATE_FILE`, `TONIES_APP_URL`, `TONIES_LOGIN_URL` and all `TONIES_*_SELECTOR` variables are ignored. `data/tonies-storage-state.json` is no longer read.
- **Default log level is `INFO`** (was `DEBUG`).

### Added
- Direct Tonies API client (`app/tonies_api.py`): OAuth sign-in with in-memory token renewal, GraphQL reads, presigned S3 uploads and exact chapter-ID writes with read-back.
- Session sign-in with HttpOnly, SameSite=Strict cookies (Secure behind HTTPS); "Lock" signs every browser out, and changing the app password signs other browsers out.
- Sign-in throttling: 5 failed attempts per client in 5 minutes triggers a wait.
- Cross-site write protection (`Origin` / `Sec-Fetch-Site` checks).
- App version shown in Settings; Docker `HEALTHCHECK`; OCI image labels.
- GitHub Actions CI: offline test suite and Docker build/health check.
- Docs: how sign-in and write safety work, upgrading from 1.x, resetting a forgotten app password, running behind a reverse proxy.

### Changed
- Uploads, rename, reorder and delete re-read the Tonie, write by chapter ID and confirm by reading back. If the Tonie changed in between, the action is refused instead of guessed; a write with an unknown outcome is never retried.
- Local uploads are no longer re-encoded a second time before upload.
- The library shows a new track as soon as Tonies accepts it and updates when processing finishes.
- Docker image is about 1.1 GB (was about 3.9 GB) and no longer installs Playwright or Chromium.
- `pytest` moved to `requirements-dev.txt`; the unused `keyring` and `python-dotenv` packages were removed.

### Fixed
- Live job events now reach the page immediately instead of silently falling back to polling.
- First-run setup could be re-run over an existing vault by anyone who could reach the app.
- Job history saves could fail or go back in time when two jobs updated at once.
- `/favicon.ico` returned 404.

### Removed
- Playwright browser automation, browser-session files, resync heuristics and upload retry/fallback paths.

## [1.0.0] - 2026-03-29

First public release: YouTube search and download, local MP3 library, upload to Creative Tonies through browser automation, track management, encrypted credential vault.

[Unreleased]: https://github.com/c1rca/tonies-yt/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/c1rca/tonies-yt/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/c1rca/tonies-yt/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/c1rca/tonies-yt/releases/tag/v1.0.0
