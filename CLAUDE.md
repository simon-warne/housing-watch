# housing-watch

Watches SSSB's free rooms and pings Simon's phone (ntfy) when one he can win with his queue days appears. Size: S (`docs/DECISIONS.md`).
Read `docs/STATUS.md` first, then `docs/PRD.md` (what, principles §7) when the task needs it. New ideas go to `docs/BACKLOG.md`.

## How to work
Follow `~/Documents/_hub/skills/code-setup/SKILL.md` §5–§10: one milestone per session, plan mode first, tests first, every yes from Simon logged in `docs/DECISIONS.md` in the same turn, `docs/STATUS.md` updated before the session ends.

## Commands
- `uv sync` (Python 3.14, deps in `pyproject.toml`)
- `uv run ruff check .` · `uv run pytest -q` · `./scripts/check.sh` (full check, same as CI plus local gitleaks if installed)

## Rules
- Only GET requests to SSSB, at most every 30 min, with a clear user-agent and a pause between pages. Never log in, book or decline.
- The repo is **public**: the ntfy topic, tokens, my queue days and booked listings live only in GitHub secrets/variables (never committed, never printed in Action logs). Tests use made-up values.
- Tests run against saved SSSB pages in `tests/fixtures/`, never the live site.

## Gotchas
- Hooks in `.claude/settings.json`: ruff after Python edits, pytest on Stop (skipped until `tests/test_*.py` exist).
