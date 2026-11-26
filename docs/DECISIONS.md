# Decisions
One line each, newest last: `YYYY-MM-DD · decision · why · (Simon | Claude, reversible)`. A reversal is a new line.

- 2026-11-26 · Size S under code-setup · script for my own use, only public SSSB data and my own queue days · (Simon)
- 2026-11-26 · PRD v1 approved · scope, alerts, milestones M0–M5 as in docs/PRD.md · (Simon)
- 2026-11-26 · Notifications via ntfy, topic in a GitHub secret · free, phone app, no account · (Simon)
- 2026-11-26 · Rent cap 6,000 kr; 6,001–7,000 only if winnable and central/Forum/Roslagstull · "no more than 6,000 unless it is really good" · (Simon)
- 2026-11-26 · Public GitHub repo · unlimited Actions minutes (private ≈1,440 of 2,000 free min/month at 48 runs/day); SSSB data is public; ntfy topic, my queue days and booked listings live in GitHub secrets/variables, never in committed files or history (PRD §9.2) · (Simon)
- 2026-11-26 · Hooks: ruff format + check after Python edits; pytest on Stop · must-always checks are deterministic, not reminders · (Simon)
- 2026-11-26 · Tooling: Python 3.14 with uv, pytest, ruff; CI = ruff, pytest, gitleaks · S-size defaults from code-setup §2 · (Claude, reversible)
