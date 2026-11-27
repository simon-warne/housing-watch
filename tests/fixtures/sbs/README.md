# Studentbostäder i Norden fixtures (M6 part A)
Fetched logged out on 2026-11-26 (~21:03–21:10 UTC), user-agent `housing-watch/0.1 (+github.com/simon-warne/housing-watch)`, ≥ 5 s apart. Log: `docs/reports/evidence/M6-requests.log`. Raw responses, unedited (no tokens found).

| File | Source | What it shows |
|---|---|---|
| `2026-11-26-lediga-stockholm.html` | `GET https://sbsstudent.se/lediga-bostader/?qt_mll_search_tags=Stockholm` | 15 Stockholm cards (type tag, city, address, move-in, rent, m², rooms/"Korridor"); no deadline, no competition figure; "Visar 1 – 15 av 16 resultat" |
| `2026-11-26-lediga-stockholm-page-2.html` | `GET /lediga-bostader/page/2/?qt_mll_search_tags=Stockholm` (22:25 UTC) | The 16th card ("Visar 16 – 16 av 16"). The page's own `?paged=2` link 301s to this URL |
| `2026-11-26-detail-nekvagen-23-lgh-1403.html` | `GET /lediga-bostader/nekvagen-23-lgh-1403/` | Detail: area (Campus Stella, Spånga), floor, rent 6 777, what's included; "Logga in för att söka" link to the Hogia portal |
| `2026-11-27-lediga-stockholm-empty.html` | `GET /lediga-bostader/?qt_mll_search_tags=Stockholm&qt_mll_monthly_rent_amount_max=1` (2026-11-27 08:13 UTC) | The empty result: no cards, no "Visar a – b av N", only `<p>Inga lägenheter matchar dina filterinställningar.</p>` in `div.listings`. Made with a filter nothing matches (an empty Stockholm list without filters not seen) |
