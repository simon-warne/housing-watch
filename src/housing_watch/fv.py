"""Förvaltaren (forvaltaren.se, Sundbyberg) parser and fetch guard (M6).

The list is one logged-out WordPress JSON with every flat; a detail page (`/objekt/<id>`, HTML)
adds the move-in date (Tillträde) and the top queue points ("Högst köpoäng"). Strict like
`sssb.py`: anything unexpected raises `ParseError`, never silent zeros.
"""

import html as htmllib
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from housing_watch.sssb import STOCKHOLM, TIMEOUT_S, USER_AGENT, ParseError

BASE = "https://www.forvaltaren.se"
LIST_URL = BASE + "/wp-json/fv/v1/objects?group_id=1&group_slug=lagenhet"
_ID = re.compile(r"\d{1,4}(?:-\d{1,5}){3}")
_DETAIL = re.compile(re.escape(BASE) + r"/objekt/(" + _ID.pattern + ")")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DEADLINE = re.compile(r"(\d{4}-\d{2}-\d{2})T00:00:00")
_FACT = re.compile(r'<li>\s*<span class="text-xxs uppercase">([^<]*)</span>\s*([^<]*?)\s*</li>')
_POINTS = re.compile(r"\d{1,3}(?:[ \xa0]\d{3})*")
CLOSES_AT = time(23, 59)  # "Anmäl senast" is a date; read as the end of that day (not verified)


@dataclass(frozen=True)
class FvListing:
    id: str  # Objektnummer, e.g. 73-32-1-0102
    area: str  # areaname
    address: str
    municipality: str  # address3
    type: str  # objectType.name, e.g. "1 rum o kokvrå"
    rooms: int
    size_m2: int
    rent: int  # kr/month (cost)
    floor: int  # 0 = ground floor; negative = below
    applicants: int  # usercount
    deadline: date  # Anmäl senast
    special: str  # specialContract ("" = none)
    seen_at: datetime


@dataclass(frozen=True)
class FvDetail:
    move_in: date  # Tillträde
    top_points: int | None  # Högst köpoäng; None = row not shown
    applicants: int | None  # Antal sökande; None = row not shown


def fv_deadline(d: date) -> datetime:
    """The moment applications close (Europe/Stockholm, tz-aware)."""
    return datetime.combine(d, CLOSES_AT, STOCKHOLM)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not a valid number")


def _get(item: dict, key: str, nr: str | None) -> Any:
    if key not in item:
        raise ParseError(key, "<missing>", nr)
    return item[key]


def _text(item: dict, key: str, nr: str | None) -> str:
    v = _get(item, key, nr)
    if not isinstance(v, str) or not v.strip():
        raise ParseError(key, v, nr)
    return " ".join(v.split())


def _int(item: dict, key: str, nr: str, *, lo: int | None) -> int:
    v = _get(item, key, nr)
    if isinstance(v, bool) or not isinstance(v, int) or (lo is not None and v < lo):
        raise ParseError(key, v, nr)
    return v


def _iso(raw: Any, key: str, nr: str | None) -> date:
    if not isinstance(raw, str) or not _ISO.fullmatch(raw):
        raise ParseError(key, raw, nr)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ParseError(key, raw, nr) from None


def _listing(item: Any, seen_at: datetime) -> FvListing:
    if not isinstance(item, dict):
        raise ParseError("rentalObjects", type(item).__name__, why="listing is not an object")
    nr = _get(item, "id", None)
    if not isinstance(nr, str) or not _ID.fullmatch(nr):
        raise ParseError("id", nr)
    otype = _get(item, "objectType", nr)
    if not isinstance(otype, dict):
        raise ParseError("objectType", otype, nr)
    end = _get(item, "showDateEnd", nr)
    m = _DEADLINE.fullmatch(end) if isinstance(end, str) else None
    if not m:
        raise ParseError("showDateEnd", end, nr)
    special = _get(item, "specialContract", nr)
    if not isinstance(special, str):
        raise ParseError("specialContract", special, nr)
    return FvListing(
        id=nr,
        area=_text(item, "areaname", nr),
        address=_text(item, "address1", nr),
        municipality=_text(item, "address3", nr),
        type=_text(otype, "name", nr),
        rooms=_int(item, "rooms", nr, lo=1),
        size_m2=_int(item, "size", nr, lo=1),
        rent=_int(item, "cost", nr, lo=1),
        floor=_int(item, "floor", nr, lo=None),
        applicants=_int(item, "usercount", nr, lo=0),
        deadline=_iso(m.group(1), "showDateEnd", nr),
        special=" ".join(special.split()),
        seen_at=seen_at,
    )


def parse_fv_list(body: str, seen_at: datetime) -> list[FvListing]:
    """Every flat in the objects response. An empty list parses to [] (the run treats it as a
    failed run)."""
    if seen_at.tzinfo is None:
        raise ParseError("seen_at", seen_at, why="must be tz-aware")
    try:
        data = json.loads(body, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise ParseError("objects", (body or "")[:80], why=str(e)) from None
    if not isinstance(data, dict) or not isinstance(data.get("rentalObjects"), list):
        raise ParseError("objects", type(data).__name__, why="expected {rentalObjects: [...]}")
    out = [_listing(i, seen_at) for i in data["rentalObjects"]]
    if len({x.id for x in out}) != len(out):
        raise ParseError("id", len(out), why="duplicate ids")
    return out


def _facts(page: str, nr: str) -> dict[str, str]:
    """Label → value of the fact list. The page repeats it (mobile and desktop); every copy
    must agree."""
    facts: dict[str, str] = {}
    for k, v in _FACT.findall(page):
        k, v = htmllib.unescape(k).strip(), htmllib.unescape(v).strip()
        if facts.setdefault(k, v) != v:
            raise ParseError(k, (facts[k], v), nr, why="the fact lists disagree")
    return facts


def _count(facts: dict[str, str], key: str, nr: str) -> int | None:
    raw = facts.get(key)
    if raw is None:
        return None
    if not _POINTS.fullmatch(raw):
        raise ParseError(key, raw, nr)
    return int(re.sub(r"[ \xa0]", "", raw))


def parse_fv_detail(page: str, listing: FvListing) -> FvDetail:
    """Move-in, top points and applicants from a detail page, checked to be about `listing`."""
    nr = listing.id
    if not isinstance(page, str):
        raise ParseError("detail", type(page).__name__, nr)
    facts = _facts(page, nr)
    if facts.get("Objektnummer") != nr:
        raise ParseError("Objektnummer", facts.get("Objektnummer"), nr, why="wrong listing")
    closes = facts.get("Anmäl senast")
    if closes != listing.deadline.isoformat():
        raise ParseError("Anmäl senast", closes, nr, why="differs from the list")
    if "Tillträde" not in facts:
        raise ParseError("Tillträde", sorted(facts), nr, why="no move-in fact")
    return FvDetail(
        move_in=_iso(facts["Tillträde"], "Tillträde", nr),
        top_points=_count(facts, "Högst köpoäng", nr),
        applicants=_count(facts, "Antal sökande", nr),
    )


def detail_url(listing: FvListing) -> str:
    url = f"{BASE}/objekt/{listing.id}"
    if not _DETAIL.fullmatch(url):
        raise ValueError(f"unexpected FV url for {listing.id}")
    return url


def fv_get(url: str, *, opener=urllib.request.urlopen) -> str:
    """A plain GET to Förvaltaren's flat list or a detail page (never anything else)."""
    if url != LIST_URL and not _DETAIL.fullmatch(url):
        raise ValueError("fv_get only fetches Förvaltaren's flat list and detail pages")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with opener(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8")
