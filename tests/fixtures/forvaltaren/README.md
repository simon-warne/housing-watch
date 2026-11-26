# Förvaltaren fixtures (M6 part A)
Fetched logged out on 2026-11-26 (~21:03–21:10 UTC), user-agent `housing-watch/0.1 (+github.com/simon-warne/housing-watch)`, ≥ 5 s apart. Log: `docs/reports/evidence/M6-requests.log`. The WordPress nonces in the detail page (`restNonce`, `ajaxNonce`) were replaced with `REDACTED`; everything else is unedited.

| File | Source | What it shows |
|---|---|---|
| `2026-11-26-groups.json` | `GET https://www.forvaltaren.se/wp-json/fv/v1/groups` | Object groups with counts (Lägenhet id 1: 17) |
| `2026-11-26-objects-lagenhet.json` | `GET /wp-json/fv/v1/objects?group_id=1&group_slug=lagenhet` | All 17 flats in one JSON: id, area, rooms, size, floor, rent (`cost`), deadline (`showDateEnd`), type, `specialContract`, `usercount` (= "Antal sökande") |
| `2026-11-26-detail-73-32-1-0102.html` | `GET /objekt/73-32-1-0102` | Detail: Tillträde 2027-02-01, Anmäl senast 2026-11-26, Antal sökande 40, **Högst köpoäng 5 022** |
