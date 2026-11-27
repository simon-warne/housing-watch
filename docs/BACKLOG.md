# Backlog
Ideas for later. Nothing here is in scope until Simon says "now" (then it moves to the PRD and DECISIONS.md).

- Small history dashboard (winning queue days per area and month).
- History: keep every listing/removal period (now `first_seen` stays the first-ever date and only the last `removed_at` is kept). Only if the digest needs re-listings (M2 review).
- History: treat flags as a set if SSSB's flag order turns out to vary (now an order change counts as "changed") (M2 review).
- Alerts: a repeat of the same state on one listing (tied → winnable → tied again, or lost → regained → lost on a booked listing) is deduped away, since keys are `status@published` (M3 review). A "lead regained" alert would fit here too.
- Digest: a listing that closes and is re-listed in the same week drops out of that week's winning-days range (same root cause as the history item above) (M3 review).
- BF digest lines: with ~100 BF rows in scope the weekly digest hits ntfy's ~3,900-byte limit ("+N more"); consider listing only BF rows with a chance, or tier 1, or a separate BF digest (M5 dry run, 2026-11-26).
- BF booked listings / lead lost: a `BF_BOOKED_IDS` variable and "lead lost" when an earlier queue date appears in the top 3 (not in M5).
- BF landlord conditions (youth age 18–25, income) are shown on the page, not checked or shown in alerts.
- Rental market dataset: record every Stockholm listing from all watched sources (not only matching ones) for trend analysis (rent per m² by area over time, supply, queue-time trends). Caveats: first-hand rents are negotiated, so they move slowly; for investment signals, sale prices (Valueguard, Svensk Mäklarstatistik) and second-hand rents (Qasa, Blocket) say more; official sources exist (SCB rents, BF queue statistics). Simon's idea, 2026-11-26.
- Outside "no watch run in 2 h" check (e.g. healthchecks.io pinged by the run), so a dead trigger (expired token, cron-job.org down) is noticed; the runner's heartbeats can't fire if nothing runs (2026-11-27).
