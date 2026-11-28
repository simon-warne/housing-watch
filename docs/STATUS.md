# Status (overwritten at the end of every session)
Updated: 2026-11-28 15:00 UTC. Review of the whole repo (all green live; 0 gaps over 45 min) and four fixes on branch `review-fixes`, merge pending: one bad SSSB detail page no longer blocks SSSB (that listing gets no winnable/last call that run); a failed ntfy send fails the run; only SSSB, BF or FV failing (or a failed send) fails the run, so SBS/Heimstaden/digest blips and bad per-source secrets stay green; the "run failed" ping is main-only; actions off Node 20. 834 tests pass. Before that: `BOOKED_IDS` → secret (pushed, main = 231e911, ci green). Reports: `docs/reports/M0.md` to `M6.md`; review evidence `docs/reports/evidence/review-2026-11-28-*.txt`.

## Where things stand
- main = 231e911 (BOOKED_IDS secret), every run green since 11:37 UTC 28 Nov. The 23:07–11:07 UTC failures were BF `Vaning: 0.5` (fixed, merged). The 02:56 `schedule` run "passed" only because every guard skipped it.
- New: the watch workflow's last step "Alert on failure" (`scripts/alert-failure.sh`) posts "housing-watch: run failed" + run URL to ntfy, once per failure streak (first attempt only; skipped if the previous counted watch run failed; cancelled/skipped/test-alert runs don't count; also on timeout). Not yet seen firing live (tested with fakes).
- Claude can push feature branches now (worked 28 Nov); main still needs Simon's yes.
- Five sources in one scheduled run, each with its own guard, heartbeat and failure isolation: SSSB and BF (alerts), FV (alerts, "FV ·"), SBS and Heimstaden (digest only, every 6 h).
- M6 Heimstaden: parses the page's `hose_objects_data` JSON (count = total), keeps `Stor-Stockholm` (20 today), watches "1 rum och kök/kokvrå" ≤ 6,000. Digest section "Heimstaden:" between SBS and BF. History schema v5. 792 tests pass. Report: `docs/reports/M6.md` part D.
- Scheduling: GitHub's schedule ran twice in ~19 h and not at all 11:39–15:55 UTC on 27 Nov (config fine, GitHub best-effort cron). Fix decided: cron-job.org calls `workflow_dispatch` at :07/:37 UTC; `schedule` stays as a backup. Repo side done, the job is Simon's to set up (below). Only the 4 manual runs of 26 Nov remain in run history (today's were deleted).
- With a Nov 2026 BF queue start, expect few BF alerts for now: top dates seen are 2020–2021.

## Next, in order
0. Done 28 Nov: merged (d131d15), ci green, main run 12:07 UTC `bf ok: 617 · fv ok: 25`. Next: the first failure on main should reach the phone as "housing-watch: run failed"; once one has, GitHub's Actions emails can go off (Settings › Notifications › Actions: "Only notify for failed workflows" off, or keep it for ci only).
0. Done 27 Nov: FV hotfix pushed (CI green 17:24 UTC); watch run 17:37 UTC (`workflow_dispatch`) shows `fv ok: 25`, `Version: 2026…` unmasked.
1. Done 27 Nov: no "FV: setup problem" note on the phone.
2. External trigger set up 17:04 UTC: check `gh run list -w watch` shows `workflow_dispatch` runs about every 30 min and `scripts/gaps.sh 1` has no gaps over 45 min after the start.
3. Sunday 29 Nov digest: check the "SBS:" and "Heimstaden:" lines and the `sbs ok` / `hs ok` log parts.
4. Check Mina sidor's number against the computed one, and when the daily +1 lands.
5. After a week: `scripts/gaps.sh 7`, look for false alerts (SSSB and BF), and tick the M4 and M5 acceptance in the reports.
6. On the first real BF last call: check whether BF really closes at 23:59 (`bf.CLOSES_AT`). After you apply for an FV flat: check that "Högst köpoäng" equals your points.
7. **Next session: M6 Wallenstam**, after its relaunch on 1 Dec (re-check the site first, plan first). Then HomeQ (read its terms, then one POST search).

## Open for Simon
- **Merge `review-fixes` into main** once its CI and a branch watch run are green (Claude pushes the branch, main is yours). After the merge: SBS/HS failures no longer ping "run failed" (they get their own heartbeat after 3), and a bad BF/FV secret only sends its setup note.
- `BOOKED_IDS` is a secret now: Settings › Secrets and variables › Actions › *Secrets* › `BOOKED_IDS` = `<objektNr>,<objektNr>` (retype the full list on every change). Don't recreate the variable: the workflow no longer reads it.
- **Push** the external-trigger commit, then set up the trigger (no values go in the repo):
  1. GitHub › Settings › Developer settings › Fine-grained tokens: only repo `housing-watch`, permission Actions: Read and write, expiry 1 year.
  2. cron-job.org job: minutes 7 and 37, every hour, timezone UTC; `POST https://api.github.com/repos/<owner>/housing-watch/actions/workflows/watch.yml/dispatches`; headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`; body `{"ref":"main"}`; turn on failure notifications.
  3. "Test run" in cron-job.org: expect HTTP 204 and a new `workflow_dispatch` run.
- Trigger set up 27 Nov 17:04 UTC (test run 204, the run started). Token expires **2027-11-26**: renew before. GitHub says API version `2022-11-28` (the `X-GitHub-Api-Version` header) is deprecated, sunset **10 Mar 2028**: update the header before then.
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
- SSSB, BF and FV detail pages each fail alone (`PartialRun`); a listing whose SSSB detail failed this run gets no winnable/last call (its figure may be stale) but lead lost still goes out. The log then shows `failed: <Type>` while the rest alert.
- Only `ALERTING` sources (`run.py`: SSSB, BF, FV) and failed sends make the run red; SBS, Heimstaden and digest failures show only in the log line and their heartbeat.
- setup-uv has no moving major tag since v8: pinned `v10.4.0`; bump by hand.
- BF `Vaning` can be a half floor (0.5, a split level; first seen 28 Nov). One odd listing fails the whole BF part: reproduce by fetching `/AllaAnnonser/` once and running `parse_bf_list` locally.
- Local `gitleaks` exists now (8.30; CI uses 8.24): `check.sh` scans full history. Never put key-like text (`key='…'`) in `.gitleaksignore` comments: CI's 8.24 flags it.
- Förvaltaren ids can end in a capital letter (`11-08-1-402B`, first seen 27 Nov); the run log only shows `fv failed: ParseError`, so reproduce by parsing one live list locally.
- Förvaltaren: the list has no move-in and no top points (both only on `/objekt/<id>`); the detail page repeats its fact list twice (both copies must agree); the deadline is a date (read as 23:59); floor can be negative. A missing "Högst köpoäng" row with applicants > 0 is a layout change, not "no applicants".
- M6 sources: Förvaltaren `wp-json/fv/v1/objects` (all flats, `usercount` = applicants), detail `/objekt/<id>` has "Högst köpoäng"; HomeQ search is POST-only; Stena and Wallenstam Stockholm go via BF; Heimstaden list is one 3.8 MB HTML page.
- Fixture pages from WordPress sites carry nonces (`restNonce`, `ajaxNonce`, `ajax_nonce`): replace before committing.
- BF: `Inflyttningsdatum` is null in the list (move-in only on the detail page, sometimes as text); deadline is a date without a time; no robots.txt.
- `/widgets/` ignores `pagination` and unknown `omr=`. `TOTAL@lagenheter` ignores filters.
- Fixture responses carry a session `CSRFtoken`: replace it before committing new fixtures.
- GitHub's `schedule` is best-effort and can skip hours in a new repo: runs come from an external `workflow_dispatch` trigger. A leaked trigger token can start runs (guards stop extra fetches) or send the test alert, but can't read secrets or push.
- Claude pushes feature branches; pushes and merges to main are Simon's.
- `hyra` uses a non-breaking space (`\xa0`). `parse_detail` needs the listing's objektNr.
- SQLite UNIQUE treats NULLs as distinct, so dedupe uses a `COALESCE` index (in `state.db`).
- `record()` needs every `Listing.seen_at` to equal the run's `seen_at`.
- SSSB's `detaljUrl` is `http://`; alerts upgrade it to https.
- Tests that span days must allow for queue days growing (Pax, top 182, is tied on 28 Nov with 180 + 2).
- Never write real queue days into docs or plans: use placeholders.
- GitHub masks every log string equal to a secret: a short secret ("0") hides every match in public logs and shows that a secret has that value. Keep secrets long (`FV_POINTS_AT` = `<points>@<date>`). `BOOKED_IDS`'s pattern also accepts a date.
- GitHub **variables** (`vars.*`) are printed unmasked in each step's `env:` block in the public run log; only secrets show as `***`. Anything private goes in a secret (`BOOKED_IDS` moved 28 Nov).
- **Public signals leak too:** which listings get requests, the step's run time, and file sizes. Anything that varies with my days or bookings must not reach public history, logs or timing (`test_public_traces_do_not_depend_on_queue_days_or_bookings`).
- A size test needs enough alerts to grow SQLite pages, or it passes without padding.
- PyYAML reads a bare `on:` key as `True`.
- BF: the detail page has no listing id (it's matched by address and Lägenhetsnummer). The deadline comes from the list (`AnnonseradTill`). Bostad snabbt is a flag on ordinary `Hyresrätt` listings.
- BF alerts need a detail page fetched in this run (`fresh`): a stored page can be days old.
- The shrink guard compares each observation table separately: one source's growth can hide the other's loss.
