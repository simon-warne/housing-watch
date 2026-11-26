# Status (overwritten at the end of every session)
Updated: 2026-11-26, M6 Förvaltaren done and tagged `m6-fv`, waiting for Simon's push. Reports: `docs/reports/M0.md` to `M6.md`.

## Where things stand
- M0 to M3 are done and pushed. M4 is done and tagged `m4`, M5 is done and tagged `m5`. **Everything since cc2c960 is not pushed** (Claude can't push).
- M5: Bostadsförmedlingen runs in the same scheduled run after SSSB, with its own guard and heartbeat. BF alerts are tagged "BF ·" and the weekly digest has a BF section. 408 tests pass.
- M6 Förvaltaren: third source in the same run (own guard, heartbeat, digest section), "FV ·" alerts from `FV_POINTS`/`FV_POINTS_ON`. 549 tests pass. Report: `docs/reports/M6.md` part B.
- With a Nov 2026 BF queue start, expect few BF alerts for now: top dates seen are 2020–2021.

## Next, in order
1. ~~Secrets, push, **watch** with `test_alert` + one normal run~~: done 2026-11-26 (Simon: worked).
2. Check Mina sidor's number against the computed one, and when the daily +1 lands.
3. After a week: `scripts/gaps.sh 7`, look for false alerts (SSSB and BF), and tick the M4 and M5 acceptance in the reports.
4. On the first real BF last call: check whether BF really closes at 23:59 (`bf.CLOSES_AT`).
5. **Simon: push** (main + tag `m6-fv`). The first run after that should log `… · fv ok: 17` (or the count then). After you apply for an FV flat: check that "Högst köpoäng" equals your points (the off-by-one risk).
6. **Next session: M6 SBS** (digest only; plan first, tests first), then Heimstaden (digest only), Wallenstam after 1 Dec, HomeQ (check terms, one POST search).

## Open for Simon
- FV in the digest: every flat is listed (~15 lines, mostly over budget), before BF. Keep, or show over-budget FV flats only as a count?
- A bad/half-set FV secret stops the whole run (same as BF_QUEUE_SINCE). Keep, or make it FV-only?
- Optional: ask GitHub Support to purge cached views of the pre-redaction commits.
- Detail cap of 10 (per source): raise it? BF listings closing tonight take most of the 10 slots until they close.
- BF lines in the digest get cut off when about 100 BF rows are in scope (backlog: only listings with a chance, or a separate BF digest).

## Gotchas learned
- Förvaltaren: the list has no move-in and no top points (both only on `/objekt/<id>`); the detail page repeats its fact list twice (both copies must agree); the deadline is a date (read as 23:59); floor can be negative. A missing "Högst köpoäng" row with applicants > 0 is a layout change, not "no applicants".
- M6 sources: Förvaltaren `wp-json/fv/v1/objects` (all flats, `usercount` = applicants), detail `/objekt/<id>` has "Högst köpoäng"; HomeQ search is POST-only; Stena and Wallenstam Stockholm go via BF; Heimstaden list is one 3.8 MB HTML page.
- Fixture pages from WordPress sites carry nonces (`restNonce`, `ajaxNonce`, `ajax_nonce`): replace before committing.
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
