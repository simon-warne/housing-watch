# Status (overwritten at the end of every session)
Updated: 2026-11-26, end of M3 (rules and alerts). Reports: `docs/reports/M0.md` to `M3.md`.

## Where things stand
- M0 to M2 are done and pushed (`m0`, `m1`, `m2`).
- M3 is done (`m3`, **not pushed**). `config.yaml` and `config.py` hold the criteria; queue days and BOOKED_IDS come from env. `rules.py` gives status, eligibility and ranking. `alerts.py` covers the five alerts with dedupe, through a private `state.db` (alert log and failure streak). The history DB is public and holds no alerts. 205 tests pass.

## Next, in order
1. Simon force-pushes the rewritten history (`main` and all tags, commands in the M3 chat). Then he asks GitHub Support to purge cached views of the old commits (optional, see below).
2. M4 goes live in a fresh session (plan mode):
   - The runner: fetch the list, then the detail pages of eligible listings for their deadline, then `record()`.
   - On success, `run_ok`, `listing_alerts` and `digest_alert`; on failure or 0 listings, `run_failed`; then `dispatch`.
   - ntfy `send` with priorities: urgent = 5 (rings through Do Not Disturb), high = 4, low = 2. Click = `url`.
   - The schedule at odd minutes, plus a keep-alive.
   - The `data` branch commits `history.sqlite` and an **encrypted** `state.db` (key from a secret).
   - No alert text or queue days in Action logs.
   - Secrets: `QUEUE_DAYS`, `QUEUE_DAYS_UNTIL`, the state key. Variable: `BOOKED_IDS`.
   - Within 48 h of a deadline, consider using the detail page's live top days (PRD §1: no false "winnable").

## Open for Simon
- **Queue-days redaction (2026-11-26):** history rewritten locally with `git filter-repo`, 0 hits left. GitHub still serves the old commits by SHA until Support purges them. A backup mirror of the old history is in the session scratchpad (temporary).
- Before going live: check Mina sidor's number against the computed one, and when the daily +1 lands.

## Gotchas learned
- `/widgets/` ignores `pagination` and unknown `omr=`. `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- ntfy's reply echoes the topic, so send its output to /dev/null in Actions.
- Claude can't push (blocked by the permission check); Simon pushes. There's no local `gitleaks` (CI runs it).
- `hyra` uses a non-breaking space (`\xa0`). `parse_detail` needs the listing's objektNr.
- SQLite UNIQUE treats NULLs as distinct, so dedupe uses a `COALESCE` index (now in `state.db`).
- `record()` needs every `Listing.seen_at` to equal the run's `seen_at`.
- SSSB's `detaljUrl` is `http://`; alerts upgrade it to https.
- Tests that span days must allow for queue days growing (Pax, top 182, turns winnable at +3 days with 180).
- Never write real queue days into docs or plans: use placeholders.
