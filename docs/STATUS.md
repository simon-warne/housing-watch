# Status (overwritten at the end of every session)
Updated: 2026-11-26, M5 part A (reconnaissance) done, stopped for Simon. Reports: `docs/reports/M0.md` to `M5.md`.

## Where things stand
- M0 to M3 are done and pushed. The rewritten history (queue days redacted) is force-pushed: `main` and the tags match GitHub.
- Move-in window is now 2027-04-01 to 2027-10-31; earlier move-ins are listed in the weekly digest (commit cc2c960). 272 tests pass. **Not pushed.**
- M5 part A done: BF serves all listings in one logged-out JSON (`/AllaAnnonser/`); detail pages show move-in, deadline and the top-3 applicants' queue start dates. Fixtures in `tests/fixtures/bf/`. Simon answered the three questions (logged); part B is next, using the BF area list in `docs/reports/M5.md` (Stockholm city, Solna, Sundbyberg only).
- M4 code is done and tagged `m4`: `run.py`, `ntfy.py`, `crypto.py`, `watch.yml`, `scripts/{fetch,push}-data.sh`, `gaps.sh`. 271 tests pass. The review's two privacy leaks are fixed (requests use public facts only; state is padded).

## Next, in order
1. Simon: add the secrets `STATE_KEY` (command in `docs/reports/M4.md`), `QUEUE_DAYS` and `QUEUE_DAYS_UNTIL`, plus the variable `BOOKED_IDS`. Push `main` and `m4`. Then run **watch** with `test_alert` (phone check), then one normal run (it creates the `data` branch).
2. Check Mina sidor's number against the computed one, and when the daily +1 lands.
3. After a week: `scripts/gaps.sh 7`, look for false alerts, and tick M4's acceptance in the report.
4. M5 part B in a fresh session: plan first (plan: `docs/plans/M5.md`; commute list and answers in `docs/reports/M5.md`). Simon adds `BF_QUEUE_SINCE`.

## Open for Simon
- Optional: ask GitHub Support to purge cached views of the pre-redaction commits.
- Detail cap of 10: with more than 10 listings closing within 48 h, each gets a live figure every other run. Raise the cap?

## Gotchas learned
- BF: `Inflyttningsdatum` is null in the list (move-in only on the detail page, sometimes as text); deadline is a date without a time; no robots.txt.
- `/widgets/` ignores `pagination` and unknown `omr=`. `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- Claude can't push (blocked by the permission check); Simon pushes. There's no local `gitleaks` (CI runs it).
- `hyra` uses a non-breaking space (`\xa0`). `parse_detail` needs the listing's objektNr.
- SQLite UNIQUE treats NULLs as distinct, so dedupe uses a `COALESCE` index (in `state.db`).
- `record()` needs every `Listing.seen_at` to equal the run's `seen_at`.
- SSSB's `detaljUrl` is `http://`; alerts upgrade it to https.
- Tests that span days must allow for queue days growing (Pax, top 182, is tied on 28 Nov with 180 + 2).
- Never write real queue days into docs or plans: use placeholders.
- **Public signals leak too:** which listings get requests, the step's run time, and file sizes. Anything that varies with my days or bookings must not reach public history, logs or timing (`test_public_traces_do_not_depend_on_queue_days_or_bookings`).
- A size test needs enough alerts to grow SQLite pages, or it passes without padding.
- PyYAML reads a bare `on:` key as `True`.
