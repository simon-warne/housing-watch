# Status (overwritten at the end of every session)
Updated: 2026-11-26, end of M2 (history). Reports: `docs/reports/M0.md`, `M1.md`, `M2.md`.

## Where things stand
- M0 done (`m0`, pushed). SSSB listings come logged out from `GET /widgets/?…widgets[]=objektlistabilder@lagenheter` (JSONP), all in one request, queue days included. The deadline comes from the detail widget `objektintresse`.
- M1 done (`m1`, pushed). `src/housing_watch/sssb.py` parses into `Listing`/`Detail`, strictly.
- M2 done (`m2`, **not pushed**). `src/housing_watch/history.py` writes SQLite with change-only observations, so an unchanged run leaves the file byte-identical. It detects new, changed and removed listings. The deadline joins by id and carries forward. The empty-list guard means 0 listings removes nothing. The `alerts` table already has its dedupe index. 114 tests pass.

## Next, in order
1. Simon pushes `main` and the `m2` tag.
2. M3 rules and alerts in a fresh session (plan mode): `config.yaml`, my queue days (from a secret/variable, never committed), the five alerts in PRD §4, dedupe through `alerts`, and a look from Simon at the alert wording.
   - Use `Changes` from `record()`, plus `latest()` and `deadline()`.
   - A deadline-only change shows up as an `(old, new)` pair that is equal apart from `seen_at`: read `deadline()`.

## Open for Simon
- Nothing.

## Gotchas learned
- `/widgets/` ignores `pagination` and unknown `omr=`; `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- ntfy's reply echoes the topic: send its output to /dev/null in Actions.
- Pushing from Claude is blocked by the permission check; Simon pushes.
- No `gitleaks` locally (CI runs it).
- `hyra` uses a non-breaking space (`\xa0`). `parse_detail` needs the listing's objektNr.
- Unbooked listings: shape guessed (empty status). Unknown text raises on purpose.
- SQLite UNIQUE treats NULLs as distinct: dedupe uses a `COALESCE` index.
- `record()` needs every `Listing.seen_at` equal to the run's `seen_at` (parse with the same timestamp).
