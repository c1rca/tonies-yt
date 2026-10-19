# Contributing

## Keep changes focused

- Keep the interface simple and PRs small.
- Support Docker on Linux, macOS and Windows with Linux containers. Native Windows Python is unsupported.
- Never commit `.env`, credentials, tokens, private media or populated test data.

## Develop locally

Follow [Quick Start](docs/QUICKSTART.md#run-a-checkout) to run a checkout. For account-free UI work, see the [fake-cloud helper](scripts/README.md#maintainerinternal). Use isolated data, never a real installation's volume.

To run offline tests, install Python 3.12 and ffmpeg on PATH:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
TEST_DATA=$(mktemp -d)
DATA_DIR="$TEST_DATA" LOG_FILE="$TEST_DATA/test.log" .venv/bin/python -m pytest -q
```

The cloud is faked in `tests/tonies_fake.py`; CI runs tests on pushes and PRs. Fake-cloud tests do not prove real YouTube/cloud extraction or image security. See [isolated acceptance](docs/OPERATIONS.md#tests-and-isolated-acceptance).

Never point tests at another person's Tonies account or include real IDs, tokens or presigned URLs in fixtures.

## Open a PR

1. Branch from `main` and make one coherent change.
2. Add regression coverage, run tests and check the affected UI flow.
3. Update the relevant guide when behavior changes.
4. Include a short summary, test results and screenshots for UI changes.

Before submitting, check that no secrets or local artifacts appear in the diff. Use short, descriptive commits, such as `fix: preserve local playback during refresh`.

## Report a bug

Include your OS, Docker/app version, reproduction steps, expected/actual behavior and sanitized logs (`/logs` can export them). Remove private titles, URLs and account details. Report vulnerabilities [privately](docs/SECURITY.md#maintenance-and-reporting).
