# Changelog

All notable changes to Tonies-YT. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Settings, Logs, Sign-in and Setup now share the workspace's look, header and light/dark/system appearance (shared `theme.css`/`theme.js`, `pages.css`).
- One-level per-Tonie server-owned Undo for guarded Rename/Move/new-upload inverses, with expiry, account reset invalidation and a five-second pre-commit cancellation window for removals. No pretend recovery after committed deletion/Clear all; uncertain inverses are never replayed.
- Selected-Tonie-first Audio Workspace: desktop Tonie rail with adjacent Tracks and Add audio panes; mobile Tracks / Add audio / Activity navigation and a sticky target/capacity header.
- YouTube / Local source tabs preserve in-session searches and filters. Local multi-selection adds to the captured destination; native local preview remains mounted across all workspace views.
- Visible per-track action menus, exact-ID inline rename with Enter/Escape and refresh-safe drafts, confirmed bulk removal, and a separate Clear all danger area.
- Unified in-session Activity with per-target jobs/actions, retained dismissible errors, partial failures and distinct accepted/processing/refresh-needed states. Follow-up read failures do not re-enable duplicate accepted local uploads.
- System-default light/dark appearance with a remembered manual choice, reduced-motion support and keyboard-accessible confirmation focus.
- Local MP3 playback: separate Play / Pause row actions and one persistent browser-native player with seeking, volume and Close. Library refreshes and filtering keep playback intact; removing the current file or locking the app stops it. Media requests retain session protection and HTTP range support; downloads keep their attachment behavior.
- **Action queue.** Deletes, moves, renames, delete-all and uploads queue per Tonie and are saved in batches: one read, one write and one confirming read for any number of changes. The list shows each change immediately; nothing waits for the previous change to finish.
- Uploads send their audio up to three at a time, alongside edits and library reads, and are attached in the order they were queued at their chosen position (no follow-up reorder). A placeholder row marks where each upload will land.
- `POST/GET /api/tonies-actions`, and `tonies_actions` events on `/api/events` with the resulting library.
- `scripts/dev/fake_cloud_app.py`: run the app against a fake Tonies cloud with simulated latency.

### Changed
- Docker now defaults to fixed nonroot UID/GID 10001, project-scoped named-volume state and loopback-only publication. Compose restricts privileges, makes code read-only, uses bounded disposable scratch/caches and enforces resource/log limits. Existing host binds require an explicit backed-up ownership migration; startup never falls back to root.
- New app passwords require at least 12 characters; existing vaults remain usable. Vault files use private atomic storage, and session/request/media boundaries have explicit security policy and bounded resource handling.
- Settings shows installed tool versions but no longer installs yt-dlp into the running app. Update by rebuilding/recreating the container (or update the native environment offline).
- Every app page has a server-rendered footer from the central running app version; future version bumps do not require per-page edits.
- Deno archives are version-pinned and SHA-256 checked; matching yt-dlp EJS solvers are installed during the build while runtime component downloads/plugins/config are disabled. Python web/parser dependencies are patched and build-only pip is removed from the runtime image.
- Native Windows Python is not supported by the POSIX-hardened runtime; Windows installation instructions now use Docker Desktop Linux containers instead of a known-broken native command.
- Changes refer to tracks by chapter ID with anchored positions, so a queued change still hits the right track after the list moved; one failing change no longer blocks the others in its batch.
- Upload jobs no longer re-check free space with an extra read before queuing; the queue checks capacity for the whole batch.

### Fixed
- Live updates: the event stream named every event `job`, which the page never received, so job progress only arrived through the 3–20 s fallback poll.

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
- First-run setup could be re-run over an existing vault by anyone who could reach the app.
- Job history saves could fail or go back in time when two jobs updated at once.
- `/favicon.ico` returned 404.

### Removed
- Playwright browser automation, browser-session files, resync heuristics and upload retry/fallback paths.

## [1.0.0] - 2026-03-29

First public release: YouTube search and download, local MP3 library, upload to Creative Tonies through browser automation, track management, encrypted credential vault.

[2.0.0]: https://github.com/c1rca/tonies-yt/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/c1rca/tonies-yt/releases/tag/v1.0.0
