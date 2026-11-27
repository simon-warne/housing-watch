# Status (overwritten at the end of every session)
Updated: 2026-11-27 11:50, FV points moved to one secret `FV_POINTS_AT` (pushed, secret set); M6 Heimstaden done and pushed (tag `m6-hs`). Earlier the same day: BF hotfix + keep-alive change (pushed; manual run 09:05 UTC gave `ok: 23 · bf ok: 566 · fv ok: 11 · sbs ok: 16`). Reports: `docs/reports/M0.md` to `M6.md` (M6 parts A–D).

## Where things stand
- Pushed: everything incl. M6 Heimstaden (5123e71, tag `m6-hs`); CI green. Manual run 09:35 UTC: `ok: 23 · bf ok: 566 · fv ok: 13 · sbs skipped: recent fetch · hs ok: 19`. Claude can't push.
- Five sources in one scheduled run, each with its own guard, heartbeat and failure isolation: SSSB and BF (alerts), FV (alerts, "FV ·"), SBS and Heimstaden (digest only, every 6 h).
- M6 Heimstaden: parses the page's `hose_objects_data` JSON (count = total), keeps `Stor-Stockholm` (20 today), watches "1 rum och kök/kokvrå" ≤ 6,000. Digest section "Heimstaden:" between SBS and BF. History schema v5. 792 tests pass. Report: `docs/reports/M6.md` part D.
- Scheduling: two scheduled runs so far (02:23 UTC, failed on the BF ParseError the hotfix fixed; 11:22 UTC ok), nothing between. Workflow state `active`. Too early to judge (deadline 28 Nov).
- With a Nov 2026 BF queue start, expect few BF alerts for now: top dates seen are 2020–2021.

## Next, in order
1. **Check the next watch run** (FV_POINTS_AT added 11:42 UTC and pushed; the 4 watch runs with masked zeros deleted): the log shows `Version: 2026…` (no `***`) and `fv ok: N`, and no "FV: setup problem" note on the phone.
2. **Watch scheduling:** `gh run list -w watch`. Scheduled runs should come about every 30 min. If they're still hours apart by 28 Nov, the keep-alive guess was wrong: try an external trigger (workflow_dispatch from a cron service) or ask GitHub Support.
3. Sunday 29 Nov digest: check the "SBS:" and "Heimstaden:" lines and the `sbs ok` / `hs ok` log parts.
4. Check Mina sidor's number against the computed one, and when the daily +1 lands.
5. After a week: `scripts/gaps.sh 7`, look for false alerts (SSSB and BF), and tick the M4 and M5 acceptance in the reports.
6. On the first real BF last call: check whether BF really closes at 23:59 (`bf.CLOSES_AT`). After you apply for an FV flat: check that "Högst köpoäng" equals your points.
7. **Next session: M6 Wallenstam**, after its relaunch on 1 Dec (re-check the site first, plan first). Then HomeQ (read its terms, then one POST search).

## Open for Simon
- Log in to Heimstaden every 180 days (account deleted otherwise) and to Förvaltaren once a year.
- Optional: ask GitHub Support to purge cached views of the pre-redaction commits.
- Detail cap of 10 (per source): raise it? BF listings closing tonight take most of the 10 slots until they close.
- BF lines in the digest get cut off when about 100 BF rows are in scope (backlog: only listings with a chance, or a separate BF digest).

## Gotchas learned
- Heimstaden: one page (~3.8 MB) holds all of Sweden. Read the embedded `window.hose_objects_data` JSON, not the cards. "Ort" in `location` is unreliable (Åkersberga shows as "Stockholm"), so scope uses `district_name`. A past `available_from_date` is shown as "Nu / enligt överenskommelse". Only district listings are parsed strictly; `labels`/`restriction` are always `[]`/"0" so far.
- BF detail with no applicants: the "Längsta anmälda kötider" heading stays, with an empty `<ul>`. Only whitespace inside reads as none; anything else odd raises.
- SBS: the site's `?paged=N` link 301s to `/lediga-bostader/page/N/?qt_mll_search_tags=Stockholm`; request that directly. Cards have everything (no detail pages); the id is the URL slug; move-in can be "Enligt överenskommelse". `urllib` follows redirects by default: `sbs_get` uses its own handler (list pages only, one hop).
- SBS empty result is `<p>Inga lägenheter matchar dina filterinställningar.</p>` in `div.listings`; only that shape reads as 0 listings. SBS's 6 h wait counts from the last *successful* fetch (`sbs_last_fetch_ok`).
- A source guard longer than 30 min needs slack (`GUARD_SLACK`), or drifting runs push it a whole run later.
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
- GitHub masks every log string equal to a secret: a short secret ("0") hides every match in public logs and shows that a secret has that value. Keep secrets long (`FV_POINTS_AT` = `<points>@<date>`). `BOOKED_IDS`'s pattern also accepts a date.
- **Public signals leak too:** which listings get requests, the step's run time, and file sizes. Anything that varies with my days or bookings must not reach public history, logs or timing (`test_public_traces_do_not_depend_on_queue_days_or_bookings`).
- A size test needs enough alerts to grow SQLite pages, or it passes without padding.
- PyYAML reads a bare `on:` key as `True`.
- BF: the detail page has no listing id (it's matched by address and Lägenhetsnummer). The deadline comes from the list (`AnnonseradTill`). Bostad snabbt is a flag on ordinary `Hyresrätt` listings.
- BF alerts need a detail page fetched in this run (`fresh`): a stored page can be days old.
- The shrink guard compares each observation table separately: one source's growth can hide the other's loss.
