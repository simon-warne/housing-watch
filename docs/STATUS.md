# Status (overwritten at the end of every session)
Updated: 2026-11-26, M5 done (part B code, review, report), tagged `m5`. Reports: `docs/reports/M0.md` to `M5.md`.

## Where things stand
- M0 to M3 are done and pushed. M4 is done and tagged `m4`, M5 is done and tagged `m5`. **Everything since cc2c960 is not pushed** (Claude can't push).
- M5: Bostadsförmedlingen runs in the same scheduled run after SSSB, with its own guard and heartbeat. BF alerts are tagged "BF ·" and the weekly digest has a BF section. 408 tests pass.
- With a Nov 2026 BF queue start, expect few BF alerts for now: top dates seen are 2020–2021.

## Next, in order
1. Simon: add the secrets `STATE_KEY` (command in `docs/reports/M4.md`), `QUEUE_DAYS`, `QUEUE_DAYS_UNTIL` and `BF_QUEUE_SINCE` (`YYYY-MM-DD`), plus the variable `BOOKED_IDS`. Push `main` with `--tags`. Then run **watch** with `test_alert` (phone check), then one normal run (it creates the `data` branch).
2. Check Mina sidor's number against the computed one, and when the daily +1 lands.
3. After a week: `scripts/gaps.sh 7`, look for false alerts (SSSB and BF), and tick the M4 and M5 acceptance in the reports.
4. On the first real BF last call: check whether BF really closes at 23:59 (`bf.CLOSES_AT`).
5. M6 (PRD §6): reconnaissance of the other queues in a fresh session (plan first), in order SBS › HomeQ › Förvaltaren › Wallenstam › Heimstaden/Stena direct; stop for Simon before product code.

## Open for Simon
- Optional: ask GitHub Support to purge cached views of the pre-redaction commits.
- Detail cap of 10 (per source): raise it? BF listings closing tonight take most of the 10 slots until they close.
- BF lines in the digest get cut off when about 100 BF rows are in scope (backlog: only listings with a chance, or a separate BF digest).

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
- BF: the detail page has no listing id (it's matched by address and Lägenhetsnummer). The deadline comes from the list (`AnnonseradTill`). Bostad snabbt is a flag on ordinary `Hyresrätt` listings.
- BF alerts need a detail page fetched in this run (`fresh`): a stored page can be days old.
- The shrink guard compares each observation table separately: one source's growth can hide the other's loss.
