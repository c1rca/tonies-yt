# Contributing

Thanks for your interest in improving Tonies-YT.

---

## Ground Rules

- Keep UX clean, minimal, and professional.
- Prefer small, focused PRs.
- Preserve cross-platform Docker usability (Windows + Linux).
- Never commit secrets (`.env`, credentials, tokens).

---

## Local Setup

```bash
git clone <repo>
cd toni-auto
cp .env.example .env
# fill required env values

docker compose up --build
```

Run tests:

```bash
pip install -r requirements.txt
pytest -q
```

---

## Branch + PR Process

1. Create a branch from `main`
2. Make focused changes
3. Run tests and validate UI flow
4. Update docs when behavior changes
5. Open PR with clear summary and screenshots for UI changes

---

## Commit Style

Use short, descriptive commits:

- `feat: add tonies rename endpoint`
- `fix: prevent tonies library auto-scroll`
- `docs: add windows quick start`

---

## PR Checklist

- [ ] Change is scoped and coherent
- [ ] Tests pass (or rationale provided)
- [ ] Docs updated (`README` / `docs/*` as needed)
- [ ] No secrets or local artifacts committed
- [ ] UI changes include before/after notes or screenshots

---

## Reporting Bugs

Please include:

- environment (Windows/Linux, Docker version)
- steps to reproduce
- expected vs actual behavior
- relevant logs (`/logs` export preferred)

