# Decisions
One line each, newest last: `YYYY-MM-DD · decision · why · (Simon | Claude, reversible)`. A reversal is a new line.

- 2026-11-26 · Size S under code-setup · script for my own use, only public SSSB data and my own queue days · (Simon)
- 2026-11-26 · PRD v1 approved · scope, alerts, milestones M0–M5 as in docs/PRD.md · (Simon)
- 2026-11-26 · Notifications via ntfy, topic in a GitHub secret · free, phone app, no account · (Simon)
- 2026-11-26 · Rent cap 6,000 kr; 6,001–7,000 only if winnable and central/Forum/Roslagstull · "no more than 6,000 unless it is really good" · (Simon)
- 2026-11-26 · Public GitHub repo · unlimited Actions minutes (private ≈1,440 of 2,000 free min/month at 48 runs/day); SSSB data is public; ntfy topic, my queue days and booked listings live in GitHub secrets/variables, never in committed files or history (PRD §9.2) · (Simon)
- 2026-11-26 · Hooks: ruff format + check after Python edits; pytest on Stop · must-always checks are deterministic, not reminders · (Simon)
- 2026-11-26 · Tooling: Python 3.14 with uv, pytest, ruff; CI = ruff, pytest, gitleaks · S-size defaults from code-setup §2 · (Claude, reversible)
- 2026-11-26 · M0 plan approved (docs/plans/M0.md) · reconnaissance before any product code · (Simon)
- 2026-11-26 · SQLite history lives on a separate `data` branch, committed only when something changed · artifacts expire after 90 days and the cache after 7 days unused; durable and inspectable · (Simon)
- 2026-11-26 · Booked listings marked in GitHub repo variable `BOOKED_IDS` (comma-separated objektNr) · public repo, so not a committed file; easy to edit · (Simon)
- 2026-11-26 · PRD §2/§5 facts updated from M0 (widgets JSON endpoint, one request for all listings) · facts, no scope change · (Simon)
- 2026-11-26 · ntfy test from GitHub Actions in M0 via a manual-only workflow · proves the secret → Actions → phone path before M4 · (Simon)
- 2026-11-26 · Schedule at odd minutes and add an explicit keep-alive in M4 · GitHub delays/drops top-of-hour runs; 60-day pause, and the docs don't say bot commits count as activity · (Claude, reversible)
- 2026-11-26 · M1 plan approved (docs/plans/M1.md), incl. assumption: null/""/"0 (0st)" antalIntresse → top_days=None, applicants=0 · parser strict, no silent zeros · (Simon)
