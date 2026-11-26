# Status (overwritten at the end of every session)
Updated: 2026-11-26, end of M1 (parser). Reports: `docs/reports/M0.md`, `docs/reports/M1.md`.

## Where things stand
- M0 done, tagged `m0` and pushed. SSSB listings come logged out from `GET /widgets/?…widgets[]=objektlistabilder@lagenheter` (JSONP): all listings in one request, queue days included (`antalIntresse`). Deadline comes from the detail widget `objektintresse`.
- Fixtures in `tests/fixtures/` (list, empty list, detail), documented in its README.
- Decided: DB on a `data` branch; booked IDs in repo variable `BOOKED_IDS`; PRD §2/§5/§9 updated.
- ntfy from Actions verified: `ntfy-test` (manual only) delivered to Simon's phone in ~3–5 s.
- M1 done, tagged `m1` (not pushed yet): `src/housing_watch/sssb.py` parses list and detail into `Listing`/`Detail`, strict (`ParseError`, never silent zeros); 92 tests on fixtures; fresh-subagent review's 6 findings fixed.

## Next, in order
1. Simon pushes `main` and the `m1` tag.
2. M2 history in a fresh session (plan mode): SQLite schema (observations + alerts), new/changed/removed detection, re-running the same page changes nothing; join `Detail` (deadline) by id; DB on the `data` branch.

## Open for Simon
- Nothing.

## Gotchas learned
- `/widgets/` ignores `pagination` and unknown `omr=`; `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- ntfy's reply echoes the topic: send its output to /dev/null in Actions.
- Pushing from Claude is blocked by the permission check; Simon pushes.
- No `gitleaks` locally (CI runs it).
- `hyra` uses a non-breaking space (`\xa0`). `parse_detail` needs the listing's objektNr: refids are opaque and the login link inside carries a different refid.
- Unbooked listings: shape guessed (empty status). Unknown text raises on purpose; fix when a real one appears.
