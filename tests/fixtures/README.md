# Fixtures

Raw responses from SSSB, fetched logged out on 2026-11-26 (~17:17 UTC). Request log: `docs/reports/evidence/M0-requests.log`.
The only edit is `CSRFtoken`, replaced with zeros (a per-response session token, not listing data).

All three are JSONP: `cb({...});`. Strip `cb(` and `);`, then parse as JSON.

| File | Request | What it shows |
|---|---|---|
| `2026-11-26-list.jsonp` | `GET /widgets/?callback=cb&widgets[]=objektsummering@lagenheter&widgets[]=objektlistabilder@lagenheter` | All 24 listings in `data["objektlistabilder@lagenheter"]`; total in `html["objektsummering-TOTAL@lagenheter"]` (`"24"`). Has corridor rooms, studios, `Bottenvåning`, flags, rents up to 13 649 kr. |
| `2026-11-26-list-empty.jsonp` | same + `hyraMax=1` | A real empty result: `data["objektlistabilder@lagenheter"] == []`. The TOTAL field ignores filters and still says `"24"`. |
| `2026-11-26-detail-5102-3203-028.jsonp` | `GET /widgets/?refid=<from detaljUrl>&callback=cb&widgets[]=objektinformation@lagenheter&widgets[]=objektintresse&widgets[]=objektintressestatus` | Deadline in `html["objektintresse"]`: "Kan bokas till 2026-11-30 klockan 16:00". Status in `html["objektintressestatus"]`: "bokats av 5 personer, varav den med flest ködagar just nu har 132 ködagar". |

## Fields that matter (list item)
`objektNr` (id), `omrade` / `omradeKod`, `adress`, `typ`, `yta` (int, m²), `vaning` ("1" or "Bottenvåning"), `hyra` ("3 841", with a space as the thousands separator), `inflyttningDatum` (ISO), `egenskaper[].beskrivning` (flags), `antalIntresse` ("132 (5st)" = top queue days, applicants), `publiceratDatum`, `detaljUrl` (holds `refid`).

## Notes
- `pagination` / `paginationantal` are ignored: every page returns all listings, so there is no page-2 fixture.
- Unknown `omr=` values are ignored (the full list is returned).
- The list has no deadline (`publiceratSistaDatum` is null), so the deadline needs the detail request.
- Customer fields (`kundnrIntern`, `hg1Namn`, `avtalsNummer`, …) are all null when logged out.
