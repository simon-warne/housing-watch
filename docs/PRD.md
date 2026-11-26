---
author: claude
status: "Approved by Simon, 2026-11-26."
drafted: 2026-11-26 (Growth chat on moving out)
owner: Simon. Claude Code must not change scope without his approval.
---

# housing-watch: product requirements (v1 draft)

## 1. Purpose
Never miss an SSSB room I can win, and build my own data on how many queue days actually win, by area and month, before I start booking (about May 2027, move-in June to August 2027).

**Success (by 1 Aug 2027):**
- Every listing that matches my criteria and that I could win triggered an alert in time to book it (no misses I find out about later).
- Six months of listing history (Dec 2026 to May 2027) answer "is summer harder than winter?" with my own numbers.
- Zero false "you're winning" alerts in the last 48 h before a deadline.

## 2. What SSSB shows (checked 26 Nov 2026)
- Listings: https://minasidor.sssb.se/lediga-bostader/ (paged, `?pagination=N&paginationantal=10`). Each has type, address, area, size, rent, move-in date, floor, flags ("10 mån hyra", "Elström ingår", "Max 4 år"), and **"Ködagar: X (Nst)"** = the current top applicant's queue days and the number of applicants.
- Detail page: booking deadline, e.g. "Kan bokas till 2026-11-30 klockan 16:00". Offer goes to Mina sidor about 45 min after close.
- robots.txt allows everything except `/wp-admin/` (and allows `/wp-admin/admin-ajax.php`).
- Unknown: whether the queue-day figures are visible when logged out (M0 finds out).

**Rules that shape the alerts:** most queue days wins; up to 5 bookings per period; queue days are deleted on signing; **the second declined offer deletes all queue days**; corridor rooms charge 10 months a year (no June/July rent).

## 3. My criteria (config file, not code)
- **My queue days:** kept in GitHub secrets (`QUEUE_DAYS`, `QUEUE_DAYS_UNTIL`), never in the repo; +1 per day while my union membership is valid. Override by updating the secret if SSSB's number differs.
- **Areas: every area is watched; the ranking only orders alerts, it never excludes.** Ranked: central (Idun, Domus, Mjölner, Tanto, Nyponet, Kurland and anything inner-city, e.g. Gamla Stan if it ever appears) and Forum › Roslagstull › Jerum, Fyrtalet › Kungshamra, Strix, Pax › Lappkärrsberget › the rest. Central areas rarely fall within reach (800–1,600 days in 2026), but a winnable one is alerted like any other. As far as known, SSSB has nothing in Gamla Stan; it would come through Bostadsförmedlingen (M5). (Source of truth for criteria: `~/Documents/Personal/Housing/_claude/status.md`.)
- **Floor:** ground floor ranked lower, not excluded.
- **Types:** corridor room ("Rum i korridor"); studio if within rent. Exclude "room in a double" (shared bathroom) and collective housing.
- **Rent:** up to 6,000 kr/month alerts normally; lower ranks higher. **Stretch:** 6,001–7,000 kr alerts only when it is winnable *and* central or Forum/Roslagstull, labelled "over budget". Above 7,000: weekly digest only. Both limits in config. (Simon, 26 Nov: no more than 6,000 unless it is really good.)
- **Move-in window:** configurable; default 2027-06-01 to 2027-10-31 (autumn possible). Outside the window: logged, not alerted.

## 4. Alerts
| Alert | When | Priority |
|---|---|---|
| **Winnable now** | New or changed listing that matches, and my queue days > current top (a tie is alerted as "tied", since SSSB's tie rule is unknown) | high |
| **Last call** | About 2 h before a deadline: matches, and I'm still ahead | urgent (rings through Do Not Disturb) |
| **Lead lost** | A listing I marked as booked: someone with more days has applied | high |
| **Weekly digest** | Sunday: matching listings seen, and the winning-days range per area | low |
| **Heartbeat** | Scraper failed, or 0 listings parsed, for 3 runs in a row | high |

Each alert: area, address, rent, move-in, top days vs mine, applicants, deadline, link. No duplicates for the same listing and state.

## 5. How it works
- Python. Fetch the listing pages at most every 30 min (detail pages only for listings that match, for their deadline), with a clear user-agent and pauses between requests.
- Parse into `Listing` (id, area, address, type, size, rent, flags, move-in, deadline, top_days, applicants, seen_at).
- SQLite history: every observation is kept; changes and new listings are detected by comparing with the last run. Every alert sent is logged too (this is the record of what I was told and when).
- Rules engine reads `config.yaml`, computes my queue days, decides alerts.
- Notifications via **ntfy** (decided by Simon, 26 Nov; secret topic in a GitHub secret). The ntfy app keeps its own history; the database is the real record.
- Runs on **GitHub Actions** on a schedule. Public repo (Simon, 2026-11-26), so minutes are unlimited. Verify in M0: how late scheduled runs start, and the rule that pauses schedules after a repo goes quiet. Where the database lives is decided in M0: committing it on every run means ~1,400 commits a month, so a separate `data` branch or an Actions cache/artifact is likely better.

## 6. Milestones
Each ends with its acceptance tests green, a short report with evidence in `docs/reports/`, and a tag.

**M0: Reconnaissance (no product code)**
- How SSSB loads listings (HTML or an endpoint); whether the figures need login; save 3+ fixture pages (normal, empty, page 2+) and a detail page.
- GitHub Actions: minutes, delay, schedule-pausing rules; ntfy test push to my phone.
- **Stop and ask Simon** if login is needed.

**M1: Parser.** Tests on the fixtures first. Every field parsed, including Swedish number formats ("3 841 kr", "Bottenvåning"). A layout change gives an error, never silent zeros.

**M2: History and changes.** SQLite schema; new, changed and removed listings detected; re-running on the same page changes nothing.

**M3: Rules and alerts.** Config, my queue days, the five alerts, dedupe. Tests for each alert, including the last call and a lost lead.

**M4: Live.** Scheduled runs, ntfy, heartbeat. Acceptance: a week of runs without a missed schedule or a false alert; a real test alert on my phone.

**M5: Bostadsförmedlingen** (student and youth listings). Can start as soon as M4 works.

**Later (backlog):** a small history dashboard; other sources.

## 7. Principles (never broken)
- **Notify only. Never book, decline or log in on my behalf.** (reCAPTCHA on booking; a second decline wipes my queue days.)
- Be polite to SSSB: at most every 30 min, a clear user-agent, no parallel hammering.
- No secrets in the repo (ntfy topic, any tokens): GitHub secrets only.
- No personal data beyond my own queue days.
- Evidence over claims: reports show test output and real runs.

## 8. How we work
Size **S** under `~/Documents/_hub/skills/code-setup/SKILL.md` (Rotunda retrospective, 26 Nov). In short: `CLAUDE.md` ≤ 40 lines; `docs/DECISIONS.md` and `docs/STATUS.md`; one milestone per session in plan mode; tests first on saved SSSB pages; CI with pytest, ruff and gitleaks; no local services; one fresh-subagent review before M4 goes live; a one-screen report per milestone; tags. Not used: security harness, E2E, previews, design track. Alert wording gets one look from Simon in M3. M0 is the gatekeeper step (SSSB's logged-out data and terms, GitHub Actions limits, ntfy) before any product code.

## 9. Open questions
1. Logged-out access to queue-day figures (M0).
2. ~~Private or public repo~~ **Public (Simon, 26 Nov).** So: the ntfy topic, my queue days and the list of listings I booked live in GitHub secrets or variables, never in committed files or committed history; the stored listing data is public SSSB data anyway.
3. Where the database lives (committed vs. artifact).
4. How I mark a listing as "booked" for the lead-lost alert (a line in config is the simplest).
