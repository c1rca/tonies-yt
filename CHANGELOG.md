# Changelog

All notable changes to Tonies-YT. Versions follow [Semantic Versioning](https://semver.org/).

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

[2.0.0]: https://github.com/c1rca/tonies-yt/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/c1rca/tonies-yt/releases/tag/v1.0.0
