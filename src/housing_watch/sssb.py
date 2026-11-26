"""SSSB widget parser (M1).

Turns the JSONP responses of minasidor.sssb.se/widgets/ into `Listing` and `Detail`.
Strict by design: anything that doesn't have the expected shape raises `ParseError`,
so a layout change on SSSB's side is loud, never silent zeros.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

LISTINGS_KEY = "objektlistabilder@lagenheter"
TOTAL_KEY = "objektsummering-TOTAL@lagenheter"
STOCKHOLM = ZoneInfo("Europe/Stockholm")

_JSONP = re.compile(r"\s*[A-Za-z_$][\w$]*\((.*)\)\s*;?\s*", re.DOTALL)
_RENT = re.compile(r"[1-9]\d{0,2}(?:[ \xa0]\d{3})*")
_FLOOR = re.compile(r"\d{1,3}")
_INTEREST = re.compile(r"(\d+) \((\d+)st\)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DEADLINE = re.compile(r"Kan bokas till (\d{4}-\d{2}-\d{2}) klockan (\d{2}:\d{2})")
_STATUS = re.compile(
    r"bokats av (\d+) person(?:er)?, varav den med flest ködagar just nu har (\d+) ködagar"
)


class ParseError(Exception):
    """SSSB returned something the parser doesn't recognise."""

    def __init__(self, field: str, raw: Any, objekt_nr: str | None = None, why: str = ""):
        self.field, self.raw, self.objekt_nr = field, raw, objekt_nr
        where = f" in {objekt_nr}" if objekt_nr else ""
        super().__init__(f"{field}{where}: unexpected {raw!r}" + (f" ({why})" if why else ""))


@dataclass(frozen=True)
class Listing:
    id: str  # objektNr
    area: str
    area_code: str
    address: str
    type: str
    size_m2: float
    rent: int  # kr/month
    floor: int  # 0 = Bottenvåning
    flags: tuple[str, ...]
    move_in: date
    published: date
    top_days: int | None  # None = nobody has booked yet
    applicants: int
    detail_url: str
    seen_at: datetime


@dataclass(frozen=True)
class Detail:
    deadline: datetime  # Europe/Stockholm, tz-aware
    applicants: int
    top_days: int | None


def load_jsonp(text: str) -> dict:
    """Strip the `cb(...);` wrapper and parse the JSON inside."""
    m = _JSONP.fullmatch(text or "")
    if not m:
        raise ParseError("jsonp", (text or "")[:80], why="not a JSONP response")
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ParseError("jsonp", m.group(1)[:80], why=str(e)) from None
    if not isinstance(payload, dict):
        raise ParseError("jsonp", type(payload).__name__, why="expected an object")
    return payload


def parse_rent(raw: Any) -> int:
    """'3 841' (normal or non-breaking space) → 3841."""
    if not isinstance(raw, str) or not _RENT.fullmatch(raw):
        raise ParseError("hyra", raw)
    return int(raw.replace(" ", "").replace("\xa0", ""))


def parse_floor(raw: Any) -> int:
    """'Bottenvåning' → 0, '6' → 6."""
    if raw == "Bottenvåning":
        return 0
    if not isinstance(raw, str) or not _FLOOR.fullmatch(raw):
        raise ParseError("vaning", raw)
    return int(raw)


def parse_interest(raw: Any) -> tuple[int | None, int]:
    """'132 (5st)' → (132, 5): top queue days, applicants. Nobody booked → (None, 0)."""
    if raw is None or raw == "" or raw == "0 (0st)":
        return None, 0
    m = _INTEREST.fullmatch(raw) if isinstance(raw, str) else None
    if not m or int(m.group(1)) == 0 or int(m.group(2)) == 0:
        raise ParseError("antalIntresse", raw)
    return int(m.group(1)), int(m.group(2))


def parse_deadline(html: Any) -> datetime:
    """'Kan bokas till 2026-11-30 klockan 16:00' → tz-aware Stockholm datetime."""
    m = _DEADLINE.search(html) if isinstance(html, str) else None
    if not m:
        raise ParseError("objektintresse", html)
    try:
        naive = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    except ValueError:
        raise ParseError("objektintresse", m.group(0), why="invalid date") from None
    return naive.replace(tzinfo=STOCKHOLM)


def _str(item: dict, key: str, nr: str | None) -> str:
    v = item.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ParseError(key, v, nr)
    return v.strip()


def _date(item: dict, key: str, nr: str) -> date:
    v = item.get(key)
    if not isinstance(v, str) or not _ISO_DATE.fullmatch(v):
        raise ParseError(key, v, nr)
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ParseError(key, v, nr) from None


def _flags(raw: Any, nr: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ParseError("egenskaper", raw, nr)
    out = []
    for e in raw:
        d = e.get("beskrivning") if isinstance(e, dict) else None
        if not isinstance(d, str) or not d.strip():
            raise ParseError("egenskaper", e, nr)
        out.append(d.strip())
    return tuple(out)


def _listing(item: Any, seen_at: datetime) -> Listing:
    if not isinstance(item, dict):
        raise ParseError(LISTINGS_KEY, type(item).__name__, why="listing is not an object")
    nr = _str(item, "objektNr", None)
    size = item.get("yta")
    if isinstance(size, bool) or not isinstance(size, int | float) or size <= 0:
        raise ParseError("yta", size, nr)
    url = _str(item, "detaljUrl", nr)
    if "refid=" not in url:
        raise ParseError("detaljUrl", url, nr, why="no refid")
    top_days, applicants = _wrap(parse_interest, item, "antalIntresse", nr)
    return Listing(
        id=nr,
        area=_str(item, "omrade", nr),
        area_code=_str(item, "omradeKod", nr),
        address=_str(item, "adress", nr),
        type=_str(item, "typ", nr),
        size_m2=float(size),
        rent=_wrap(parse_rent, item, "hyra", nr),
        floor=_wrap(parse_floor, item, "vaning", nr),
        flags=_flags(item.get("egenskaper"), nr),
        move_in=_date(item, "inflyttningDatum", nr),
        published=_date(item, "publiceratDatum", nr),
        top_days=top_days,
        applicants=applicants,
        detail_url=url,
        seen_at=seen_at,
    )


def _wrap(fn, item: dict, field: str, nr: str):
    """Run a helper on item[field] and attach the objektNr to its error."""
    raw = item.get(field)
    try:
        return fn(raw)
    except ParseError as e:
        raise ParseError(field, raw, nr) from e


def parse_list(payload: dict, seen_at: datetime, *, check_total: bool = True) -> list[Listing]:
    """All listings from the `objektlistabilder@lagenheter` widget response.

    `check_total` compares the count with SSSB's TOTAL (which ignores filters, so keep it on
    for the unfiltered fetch we always make).
    """
    if seen_at.tzinfo is None:
        raise ParseError("seen_at", seen_at, why="must be tz-aware")
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get(LISTINGS_KEY) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ParseError(LISTINGS_KEY, type(items).__name__, why="listings key missing")
    listings = [_listing(item, seen_at) for item in items]
    if len({x.id for x in listings}) != len(listings):
        raise ParseError("objektNr", [x.id for x in listings], why="duplicate ids")
    if check_total:
        html = payload.get("html")
        total = html.get(TOTAL_KEY) if isinstance(html, dict) else None
        if not isinstance(total, str) or not total.isdigit():
            raise ParseError(TOTAL_KEY, total)
        if int(total) != len(listings):
            raise ParseError(TOTAL_KEY, total, why=f"parsed {len(listings)} listings")
    return listings


def parse_detail(payload: dict) -> Detail:
    """Deadline and live booking status from the detail widgets."""
    html = payload.get("html") if isinstance(payload, dict) else None
    if not isinstance(html, dict):
        raise ParseError("html", type(html).__name__, why="detail html missing")
    deadline = parse_deadline(html.get("objektintresse"))
    status = html.get("objektintressestatus")
    m = _STATUS.search(status) if isinstance(status, str) else None
    if not m:
        raise ParseError("objektintressestatus", status)
    return Detail(deadline=deadline, applicants=int(m.group(1)), top_days=int(m.group(2)))
