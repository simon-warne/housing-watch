# Backlog
Ideas for later. Nothing here is in scope until Simon says "now" (then it moves to the PRD and DECISIONS.md).

- Small history dashboard (winning queue days per area and month).
- Other sources beyond SSSB and Bostadsförmedlingen (see ~/Documents/Personal/Housing/links.md).
- History: keep every listing/removal period (now `first_seen` stays the first-ever date and only the last `removed_at` is kept). Only if the digest needs re-listings (M2 review).
- History: treat flags as a set if SSSB's flag order turns out to vary (now an order change counts as "changed") (M2 review).
- Alerts: a repeat of the same state on one listing (tied → winnable → tied again, or lost → regained → lost on a booked listing) is deduped away, since keys are `status@published` (M3 review). A "lead regained" alert would fit here too.
- Digest: a listing that closes and is re-listed in the same week drops out of that week's winning-days range (same root cause as the history item above) (M3 review).
