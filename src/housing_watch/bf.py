"""Bostadsförmedlingen (bostad.stockholm.se) parser and fetch guard (M5 part B).

The list is one logged-out JSON array (`/AllaAnnonser/`); a detail page (`/bostad/<id>/`, HTML)
adds the move-in date and the queue start dates of the top 3 applicants. Strict like
`sssb.py`: anything unexpected raises `ParseError`, never silent zeros.
"""

import html as htmllib
import json
import math
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from housing_watch.sssb import STOCKHOLM, TIMEOUT_S, USER_AGENT, ParseError

BASE = "https://bostad.stockholm.se"
LIST_URL = BASE + "/AllaAnnonser/"
_ALLOWED = re.compile(r"https://bostad\.stockholm\.se/(?:AllaAnnonser/|bostad/[1-9]\d{0,11}/)")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOSES_AT = time(23, 59)  # "Anmälan senast" is a date; read as the end of that day (not verified)

MONTHS = (
    "januari februari mars april maj juni juli augusti september oktober november december"
).split()
_MONTH = re.compile(r"(" + "|".join(MONTHS) + r") (\d{4})")
_ASAP = re.compile(r"så snart som möjligt")
_FACT = re.compile(
    r'<span class="apartment-facts__text">\s*(?:<svg.*?</svg>\s*)?(?:<span>)?\s*([^<]+?)\s*'
    r'(?:</span>\s*)?</span>\s*<span class="apartment-facts__heading">\s*([^<]*?)\s*</span>',
    re.DOTALL,
)
_H1 = re.compile(r"<h1[^>]*>\s*([^<]+?)\s*</h1>")
_APT_NO = re.compile(
    r"Lägenhetsnummer</strong>\s*</span>\s*<span class=\"list-text\">\s*([^<]+?)\s*<"
)
_QUEUE = re.compile(
    r"Längsta anmälda kötider på denna bostad:</strong>\s*<ul[^>]*>(.*?)</ul>", re.DOTALL
)
_LI = re.compile(r"<li>\s*([^<]*?)\s*</li>")


@dataclass(frozen=True)
class BfListing:
    id: str  # LagenhetId
    apartment_no: str | None  # Lagenhetsnummer; empty on some project ads
    district: str  # Stadsdel
    municipality: str  # Kommun
    address: str
    type: str  # Lagenhetstyp
    rooms: float
    size_m2: float | None  # None only on project ads with several units
    rent: int | None  # kr/month; None only on project ads
    floor: int | None  # 0 = BV; negative = below ground
    units: int  # Antal
    bostad_snabbt: bool
    advertised_from: date
    deadline: date  # Anmälan senast
    url: str  # /bostad/<id>/
    seen_at: datetime


@dataclass(frozen=True)
class BfDetail:
    move_in: date
    top_dates: tuple[date, ...]  # queue start dates of the top 3; () = no block shown


def bf_deadline(d: date) -> datetime:
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


def _int(item: dict, key: str, nr: str, *, lo: int) -> int:
    v = _get(item, key, nr)
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise ParseError(key, v, nr)
    return v


def _num(v: Any, key: str, nr: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0:
        raise ParseError(key, v, nr)
    return float(v)


def _iso(v: Any, key: str, nr: str | None) -> date:
    if not isinstance(v, str) or not _ISO.fullmatch(v):
        raise ParseError(key, v, nr)
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ParseError(key, v, nr) from None


def _listing(item: Any, seen_at: datetime) -> BfListing:
    if not isinstance(item, dict):
        raise ParseError("AllaAnnonser", type(item).__name__, why="listing is not an object")
    lid = _get(item, "LagenhetId", None)
    if isinstance(lid, bool) or not isinstance(lid, int) or lid <= 0:
        raise ParseError("LagenhetId", lid)
    nr = str(lid)
    units = _int(item, "Antal", nr, lo=1)
    project = units > 1 or _int(item, "ProjektId", nr, lo=0) != 0

    def optional(key: str):
        v = _get(item, key, nr)
        if v is None and not project:
            raise ParseError(key, v, nr, why="null outside a project ad")
        return v

    rent = optional("Hyra")
    if rent is not None and (isinstance(rent, bool) or not isinstance(rent, int) or rent <= 0):
        raise ParseError("Hyra", rent, nr)
    size = optional("Yta")
    floor = optional("Vaning")
    if floor is not None and (isinstance(floor, bool) or not isinstance(floor, int)):
        raise ParseError("Vaning", floor, nr)
    snabbt = _get(item, "BostadSnabbt", nr)
    if not isinstance(snabbt, bool):
        raise ParseError("BostadSnabbt", snabbt, nr)
    url = _get(item, "Url", nr)
    if url != f"/bostad/{nr}/":
        raise ParseError("Url", url, nr)
    apt = _get(item, "Lagenhetsnummer", nr)
    if not isinstance(apt, str):
        raise ParseError("Lagenhetsnummer", apt, nr)
    return BfListing(
        id=nr,
        apartment_no=apt.strip() or None,
        district=_text(item, "Stadsdel", nr),
        municipality=_text(item, "Kommun", nr),
        address=_text(item, "Gatuadress", nr),
        type=_text(item, "Lagenhetstyp", nr),
        rooms=_num(_get(item, "AntalRum", nr), "AntalRum", nr),
        size_m2=None if size is None else _num(size, "Yta", nr),
        rent=rent,
        floor=floor,
        units=units,
        bostad_snabbt=snabbt,
        advertised_from=_iso(_get(item, "AnnonseradFran", nr), "AnnonseradFran", nr),
        deadline=_iso(_get(item, "AnnonseradTill", nr), "AnnonseradTill", nr),
        url=url,
        seen_at=seen_at,
    )


def parse_bf_list(body: str, seen_at: datetime) -> list[BfListing]:
    """Every listing in the `/AllaAnnonser/` response. `[]` parses to [] (the run treats it
    as a failed run)."""
    if seen_at.tzinfo is None:
        raise ParseError("seen_at", seen_at, why="must be tz-aware")
    try:
        items = json.loads(body, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise ParseError("AllaAnnonser", (body or "")[:80], why=str(e)) from None
    if not isinstance(items, list):
        raise ParseError("AllaAnnonser", type(items).__name__, why="expected an array")
    out = [_listing(i, seen_at) for i in items]
    if len({x.id for x in out}) != len(out):
        raise ParseError("LagenhetId", len(out), why="duplicate ids")
    return out


def parse_move_in(raw: str, deadline: date) -> date:
    """'2027-04-15'; 'januari 2027' → the 1st; 'så snart som möjligt' → the deadline date
    (Claude, 2026-11-26). Anything else raises."""
    s = " ".join((raw or "").split()).casefold()
    if _ISO.fullmatch(s):
        return _iso(s, "Inflytt", None)
    if m := _MONTH.fullmatch(s):
        return date(int(m.group(2)), MONTHS.index(m.group(1)) + 1, 1)
    if _ASAP.fullmatch(s):
        return deadline
    raise ParseError("Inflytt", raw)


def parse_bf_detail(page: str, listing: BfListing) -> BfDetail:
    """Move-in and top-3 queue dates from a detail page, checked to be about `listing`
    (the page carries no id: address and Lägenhetsnummer must match the list)."""
    if not isinstance(page, str):
        raise ParseError("detail", type(page).__name__, listing.id)
    h1 = _H1.search(page)
    address = " ".join(htmllib.unescape(h1.group(1)).split()) if h1 else None
    m = _APT_NO.search(page)
    apt = htmllib.unescape(m.group(1)).strip() if m else None
    if address != listing.address or (listing.apartment_no and apt != listing.apartment_no):
        raise ParseError("detail", (address, apt), listing.id, why="wrong listing")
    facts = {htmllib.unescape(k).strip(): htmllib.unescape(v) for k, v in _FACT.findall(page)}
    if "Inflytt" not in facts:
        raise ParseError("Inflytt", sorted(facts), listing.id, why="no move-in fact")
    closes = facts.get("Anmälan senast")
    if closes != listing.deadline.isoformat():
        raise ParseError("Anmälan senast", closes, listing.id, why="differs from the list")
    try:
        move_in = parse_move_in(facts["Inflytt"], listing.deadline)
    except ParseError as e:
        raise ParseError("Inflytt", e.raw, listing.id) from e
    q = _QUEUE.search(page)
    if q is None:
        return BfDetail(move_in=move_in, top_dates=())
    if not q.group(1).strip():  # nobody has applied yet: BF's live shape (Simon, 2026-11-27)
        return BfDetail(move_in=move_in, top_dates=())
    raw = _LI.findall(q.group(1))
    if not 1 <= len(raw) <= 3 or not all(_ISO.fullmatch(r) for r in raw):
        raise ParseError("kötider", raw, listing.id)
    try:
        dates = tuple(date.fromisoformat(r) for r in raw)
    except ValueError:
        raise ParseError("kötider", raw, listing.id) from None
    return BfDetail(move_in=move_in, top_dates=dates)


def detail_url(listing: BfListing) -> str:
    url = BASE + listing.url
    if not _ALLOWED.fullmatch(url):
        raise ValueError(f"unexpected BF url for {listing.id}")
    return url


def bf_get(url: str, *, opener=urllib.request.urlopen) -> str:
    """A plain GET to BF's list or a detail page (never anything else), decoded as text."""
    if not _ALLOWED.fullmatch(url):
        raise ValueError("bf_get only fetches BF's listing pages")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with opener(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8")
