# Status (overwritten at the end of every session)
Updated: 2026-11-26, after repo setup (code-setup, new mode).

## Where things stand
- PRD v1 approved. Size S. Repo scaffolded: CLAUDE.md, docs (DECISIONS, STATUS, BACKLOG), pyproject + uv.lock, hooks, CI (ruff, pytest, gitleaks). No product code yet.
- Pushed to https://github.com/simon-warne/housing-watch (public); CI green on first push. ntfy app installed, test push received.

## Next, in order
1. M0 reconnaissance in a fresh session (plan mode): how SSSB loads listings, whether queue days show logged out, save fixtures (normal, empty, page 2+, detail page), SSSB terms, Actions schedule delay and the 60-day inactivity pause, where the SQLite database lives, ntfy test push.

## Open for Simon
- Keep the ntfy topic name private (M0 adds it as the GitHub secret `NTFY_TOPIC`).
- PRD open question 4: how to mark a listing as "booked". Since the repo is public it can't be committed config; default: a GitHub variable listing booked IDs.

## Gotchas learned
- No `gitleaks` locally (CI runs it); `brew install gitleaks` if wanted.
