# Bostadsförmedlingen fixtures (M5)
Fetched logged out on 2026-11-26 (~19:34 UTC), user-agent `housing-watch/0.1 (+github.com/simon-warne/housing-watch)`, 5 s apart. Log: `docs/reports/evidence/M5-requests.log`. Raw responses, unedited (no tokens or session data found).

| File | Source | What it shows |
|---|---|---|
| `2026-11-26-alla-annonser.json` | `GET https://bostad.stockholm.se/AllaAnnonser/` | All 527 listings in one JSON array; same keys on every item; `Inflyttningsdatum` is null on all |
| `2026-11-26-detail-202619429-studentkorridor.html` | `GET /bostad/202619429/` | Student corridor: Inflytt `2027-01-01`, Anmälan senast `2026-11-29`, top-3 queue dates |
| `2026-11-26-detail-202618022-ungdom.html` | `GET /bostad/202618022/` | Youth flat: move-in "så snart som möjligt", age 18–25, income ≥ 285,000 kr, top-3 queue dates |
| `2026-11-26-detail-202601471-bostad-snabbt.html` | `GET /bostad/202601471/` | Bostad snabbt: move-in "januari 2027", no queue-date block |
| `2026-11-28-alla-annonser.json` | `GET https://bostad.stockholm.se/AllaAnnonser/` (2026-11-28 ~11:07 UTC, one GET, unedited) | All 617 listings; 202621041 (Idrottsgatan 5) has `Vaning: 0.5`, a half floor, which made every BF run fail from 23:07 UTC 27 Nov |
