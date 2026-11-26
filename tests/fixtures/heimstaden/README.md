# Heimstaden fixtures (M6 part A)
Fetched logged out on 2026-11-26 (~21:03–21:10 UTC), user-agent `housing-watch/0.1 (+github.com/simon-warne/housing-watch)`, ≥ 5 s apart. Log: `docs/reports/evidence/M6-requests.log`. The WordPress `ajax_nonce` was replaced with `REDACTED` in both files; everything else is unedited. The list page is 3.8 MB, so it is stored gzipped.

| File | Source | What it shows |
|---|---|---|
| `2026-11-26-sok-lagenhet.html.gz` | `GET https://heimstaden.com/se/sok-lagenhet/?text=&number_of_rooms_min=1&number_of_rooms_max=10&rent_min=0&rent_max=26000&size_min=0&size_max=300&object_type=apartments` | All 424 Swedish listings server-rendered as `object-card`s (`data-object-id`, "Ort - område", address, rent, rooms, m², availability); 18 in Stockholm county |
| `2026-11-26-detail-6937101-1210.html` | `GET /se/sok-lagenhet/Bandhagen-33m²-6937101-1210/` | Detail: available 1 Feb 2027, 6 114 kr, floor 2, 1 rum och kök; no deadline, no competition figure |
