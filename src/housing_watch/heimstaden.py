"""Heimstaden (heimstaden.com/se) list parser and fetch guard (M6).

Digest only: Heimstaden shows no competition figure (Simon, 2026-11-26). One logged-out page
carries every Swedish listing, both as HTML cards and as the embedded JSON
`window.hose_objects_data = {"total_nr_of_objects": N, "objects": [...]}`; we read the JSON.
Strict like `sssb.py`: anything unexpected raises `ParseError`, the object count must equal
the total, and every listing (in scope or not) must parse. Only listings whose
`district_name` is the configured district (Stor-Stockholm, Simon 2026-11-27) are returned.
"""

import gzip
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from housing_watch.sbs import _MONTHS
from housing_watch.sssb import TIMEOUT_S, USER_AGENT, ParseError

LIST_URL = (
    "https://heimstaden.com/se/sok-lagenhet/?text=&number_of_rooms_min=1&number_of_rooms_max=10"
    "&rent_min=0&rent_max=26000&size_min=0&size_max=300&object_type=apartments"
)
BY_AGREEMENT = "Nu / enligt överenskommelse"  # shown once the date has passed
_MARK = "window.hose_objects_data = "
_RENT = re.compile(r"[1-9]\d{0,2}(?:\xa0\d{3})*")
_DATE = re.compile(r"(\d{1,2}) ([a-zåäö]+), (\d{4})")
MAX_BYTES = 30_000_000  # the page is ~3.8 MB


@dataclass(frozen=True)
class HsListing:
    id: str  # rental_object_id, e.g. 6937101-1210
    location: str  # "Ort - område" as the site shows it, e.g. "Stockholm - Bandhagen"
    address: str
    rooms: str  # e.g. "1 rum och kök", "1 rum och kokvrå"
    rent: int  # kr/month
    size_m2: int
    move_in: date | None  # None = "Nu / enligt överenskommelse"
    restricted: bool  # restriction ≠ "0" (all are "0" so far; shown as a tag)
    labels: tuple[str, ...]  # each once, site order
    seen_at: datetime


def _text(o: dict, key: str, nr: str) -> str:
    v = o.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ParseError(key, v, nr)
    return " ".join(v.split())


def _date(raw: str, nr: str) -> date:
    m = _DATE.fullmatch(raw)
    if not m or m.group(2) not in _MONTHS:
        raise ParseError("available_from_date", raw, nr)
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        raise ParseError("available_from_date", raw, nr) from None


def _listing(o: Any, seen_at: datetime) -> tuple[str, HsListing]:
    if not isinstance(o, dict):
        raise ParseError("object", type(o).__name__)
    nr = o.get("rental_object_id")
    if not isinstance(nr, str) or not re.fullmatch(r"\d+-\d+", nr):
        raise ParseError("rental_object_id", nr)
    if o.get("publish_status") != "published":
        raise ParseError("publish_status", o.get("publish_status"), nr)
    district = _text(o, "district_name", nr)
    rent_raw = o.get("rental_cost")
    if not isinstance(rent_raw, str) or not _RENT.fullmatch(rent_raw):
        raise ParseError("rental_cost", rent_raw, nr)
    size = o.get("size_main")
    if type(size) is not int or size <= 0:
        raise ParseError("size_main", size, nr)
    move_in = _date(_text(o, "available_from_date", nr), nr)
    shown = _text(o, "available_from_date_text", nr)
    if shown == BY_AGREEMENT:
        move_in = None
    elif shown != o["available_from_date"].strip():
        raise ParseError("available_from_date_text", shown, nr, why="≠ available_from_date")
    restriction = o.get("restriction")
    if not isinstance(restriction, str) or not restriction.isdigit():
        raise ParseError("restriction", restriction, nr)
    labels = o.get("labels")
    if not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
        raise ParseError("labels", labels, nr)
    return district, HsListing(
        id=nr,
        location=_text(o, "location", nr),
        address=_text(o, "street_address", nr),
        rooms=_text(o, "rooms", nr),
        rent=int(rent_raw.replace("\xa0", "")),
        size_m2=size,
        move_in=move_in,
        restricted=restriction != "0",
        labels=tuple(dict.fromkeys(" ".join(x.split()) for x in labels)),
        seen_at=seen_at,
    )


def parse_hs_page(page: str, seen_at: datetime, district: str) -> list[HsListing]:
    """The listings in `district`, after checking every listing on the page parses."""
    if seen_at.tzinfo is None:
        raise ParseError("seen_at", seen_at, why="must be tz-aware")
    if not isinstance(page, str) or page.count(_MARK) != 1:
        raise ParseError("hose_objects_data", page.count(_MARK) if isinstance(page, str) else 0)
    try:
        d, _ = json.JSONDecoder().raw_decode(page, page.index(_MARK) + len(_MARK))
    except json.JSONDecodeError as e:
        raise ParseError("hose_objects_data", str(e), why="not JSON") from None
    if not isinstance(d, dict) or not isinstance(d.get("objects"), list):
        raise ParseError("hose_objects_data", type(d).__name__)
    total, objs = d.get("total_nr_of_objects"), d["objects"]
    if type(total) is not int or total <= 0 or len(objs) != total:
        raise ParseError("total_nr_of_objects", (total, len(objs)), why="count ≠ total")
    out = [_listing(o, seen_at) for o in objs]
    if len({x.id for _, x in out}) != len(out):
        raise ParseError("rental_object_id", len(out), why="duplicate ids")
    return [x for dist, x in out if dist == district]


def allowed(url: str) -> bool:
    return url == LIST_URL


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Any redirect is an error: we only ever fetch the one list URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, f"Heimstaden redirected ({msg})", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirects())


def hs_get(url: str, *, opener=None) -> str:
    """A plain GET of Heimstaden's list page (never anything else), gzip accepted."""
    if not allowed(url):
        raise ValueError("hs_get only fetches Heimstaden's list page")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}, method="GET"
    )
    with (opener or _OPENER.open)(req, timeout=TIMEOUT_S) as resp:
        body = resp.read(MAX_BYTES + 1)
        enc = (resp.headers.get("Content-Encoding") or "").strip().lower()
        charset = resp.headers.get_content_charset() or "utf-8"
    if enc == "gzip":
        body = gzip.decompress(body)
    elif enc not in ("", "identity"):
        raise ValueError(f"Heimstaden sent an unexpected encoding: {enc}")
    if len(body) > MAX_BYTES:
        raise ValueError("Heimstaden page larger than expected")
    return body.decode(charset)
