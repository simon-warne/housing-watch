# Status (overwritten at the end of every session)
Updated: 2026-11-26, end of M0 (reconnaissance). Report: `docs/reports/M0.md`.

## Where things stand
- M0 done, tagged `m0` and pushed. SSSB listings come logged out from `GET /widgets/?…widgets[]=objektlistabilder@lagenheter` (JSONP): all listings in one request, queue days included (`antalIntresse`). Deadline comes from the detail widget `objektintresse`.
- Fixtures in `tests/fixtures/` (list, empty list, detail), documented in its README.
- Decided: DB on a `data` branch; booked IDs in repo variable `BOOKED_IDS`; PRD §2/§5/§9 updated.
- ntfy from Actions verified: `ntfy-test` (manual only) delivered to Simon's phone in ~3–5 s. 

## Next, in order
1. M1 parser in a fresh session (plan mode): strip JSONP → JSON, parse into `Listing`, parse "3 841", "Bottenvåning", "132 (5st)", the deadline sentence; a layout change raises an error, never silent zeros. Check list length against `TOTAL@lagenheter`.

## Open for Simon
- Nothing.

## Gotchas learned
- `/widgets/` ignores `pagination` and unknown `omr=`; `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- ntfy's reply echoes the topic: send its output to /dev/null in Actions.
- Pushing from Claude is blocked by the permission check; Simon pushes.
- No `gitleaks` locally (CI runs it).
