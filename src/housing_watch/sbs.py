"""Studentbostäder i Norden (sbsstudent.se, Stockholm) list parser and fetch guard (M6).

Digest only: SBS shows no competition figure. Every card on the logged-out list pages has
what the digest needs (address, move-in, rent, m², rooms, labels), so no detail pages are
fetched. Page 1 is `LIST_URL`, later pages `/lediga-bostader/page/N/?…` (the site's own
`?paged=N` link answers 301 there). Strict like `sssb.py`: anything unexpected raises
`ParseError`, and the cards read must match the page's "Visar a – b av N".
"""

import html as htmllib
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from housing_watch.sssb import TIMEOUT_S, USER_AGENT, ParseError

BASE = "https://sbsstudent.se"
_QUERY = "?qt_mll_search_tags=Stockholm"
LIST_URL = BASE + "/lediga-bostader/" + _QUERY
_PAGE = re.compile(re.escape(BASE) + r"/lediga-bostader/page/([2-9]|[1-9]\d)/" + re.escape(_QUERY))
_CARD_OPEN = '<a class="card card--listing"'
_CARD = re.compile(
    re.escape(_CARD_OPEN) + r' href="([^"]*)">(.*?)</a>',
    re.S,
)
_HREF = re.compile(re.escape(BASE) + r"/lediga-bostader/([a-z0-9]+(?:-[a-z0-9]+)*)/")
_RANGE = re.compile(r"Visar (\d+) – (\d+) av (\d+) resultat")
_RENT = re.compile(r"(\d{1,3}(?:[ \xa0]\d{3})*) kr/mån")
_SIZE = re.compile(r"(\d{1,3}) m²")
_DATE = re.compile(r"(\d{1,2}) ([a-zåäö]+\.?) (\d{4})")
BY_AGREEMENT = "Enligt överenskommelse"
CITY = "Stockholm"
_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("jan.", "jan", "januari"),
            ("feb.", "feb", "februari"),
            ("mars", "mar.", "mars."),
            ("apr.", "apr", "april"),
            ("maj",),
            ("juni", "jun.", "jun"),
            ("juli", "jul.", "jul"),
            ("aug.", "aug", "augusti"),
            ("sep.", "sep", "sept.", "september"),
            ("okt.", "okt", "oktober"),
            ("nov.", "nov", "november"),
            ("dec.", "dec", "december"),
        ),
        start=1,
    )
    for m in names
}


@dataclass(frozen=True)
class SbsListing:
    id: str  # the listing's URL slug, e.g. nekvagen-23-lgh-1403
    tag: str  # e.g. "Studentbostad", "Student & Ungdom", "Korttidkontrakt – Student"
    city: str
    address: str
    move_in: date | None  # None = "Enligt överenskommelse" (by agreement)
    rent: int  # kr/month
    size_m2: int | None
    rooms: str  # "1 rum", "Korridor", "2 rum", …
    labels: tuple[str, ...]  # e.g. ("Möblerad", "Kampanj"), each once, page order
    seen_at: datetime


@dataclass(frozen=True)
class SbsPage:
    listings: list[SbsListing]
    first: int  # "Visar <first> – <last> av <total> resultat"
    last: int
    total: int


def page_url(n: int) -> str:
    if n < 1:
        raise ValueError(f"no SBS page {n}")
    return LIST_URL if n == 1 else f"{BASE}/lediga-bostader/page/{n}/{_QUERY}"


def _spans(card: str, cls: str, nr: str) -> list[str]:
    return [
        " ".join(htmllib.unescape(v).split())
        for v in re.findall(r'<span class="' + cls + r'[^"]*"[^>]*>([^<]*)</span>', card)
    ]


def _one(values: list[str], field: str, nr: str) -> str:
    if len(values) != 1 or not values[0]:
        raise ParseError(field, values, nr)
    return values[0]


def _move_in(raw: str, nr: str) -> date | None:
    if not raw.startswith("Inflyttning:"):
        raise ParseError("Inflyttning", raw, nr)
    v = raw.removeprefix("Inflyttning:").strip()
    if v == BY_AGREEMENT:
        return None
    m = _DATE.fullmatch(v)
    if not m or m.group(2) not in _MONTHS:
        raise ParseError("Inflyttning", v, nr)
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        raise ParseError("Inflyttning", v, nr) from None


def _card(href: str, card: str, seen_at: datetime) -> SbsListing:
    m = _HREF.fullmatch(href)
    if not m:
        raise ParseError("href", href, why="not an SBS listing link")
    nr = m.group(1)
    city = _one(_spans(card, "listing__city", nr), "city", nr)
    if city != CITY:
        raise ParseError("city", city, nr, why=f"expected {CITY}")
    titles = re.findall(r'<h2 class="card__title">([^<]*)</h2>', card)
    address = _one([" ".join(htmllib.unescape(t).split()) for t in titles], "title", nr)
    dates = _spans(card, "listing__date listing__date-vacancy", nr)
    move_in = _move_in(_one(dates, "Inflyttning", nr), nr)

    rent: list[int] = []
    size: list[int] = []
    rooms: list[str] = []
    for li in re.findall(r"<li>([^<]*)</li>", card):
        v = " ".join(htmllib.unescape(li).replace("\xa0", " ").split())
        if r := _RENT.fullmatch(v):
            rent.append(int(r.group(1).replace(" ", "")))
        elif s := _SIZE.fullmatch(v):
            size.append(int(s.group(1)))
        elif re.fullmatch(r"\d+ rum|Korridor", v):
            rooms.append(v)
        else:
            raise ParseError("details", v, nr, why="unknown item")
    if len(rent) != 1 or rent[0] <= 0:
        raise ParseError("rent", rent, nr)
    if len(size) > 1 or len(rooms) != 1:
        raise ParseError("details", (size, rooms), nr)
    labels = tuple(dict.fromkeys(_spans(card, "listing__additional-feature", nr)))
    if "" in labels:
        raise ParseError("labels", labels, nr)
    return SbsListing(
        id=nr,
        tag=_one(_spans(card, "listing__tag", nr), "tag", nr),
        city=city,
        address=address,
        move_in=move_in,
        rent=rent[0],
        size_m2=size[0] if size else None,
        rooms=rooms[0],
        labels=labels,
        seen_at=seen_at,
    )


def parse_sbs_page(page: str, seen_at: datetime) -> SbsPage:
    """The cards on one list page and its "Visar a – b av N" range (checked to match)."""
    if seen_at.tzinfo is None:
        raise ParseError("seen_at", seen_at, why="must be tz-aware")
    if not isinstance(page, str):
        raise ParseError("page", type(page).__name__)
    ranges = _RANGE.findall(" ".join(htmllib.unescape(page).split()))
    if len(ranges) != 1:
        raise ParseError("Visar", ranges, why="expected one result range")
    first, last, total = (int(n) for n in ranges[0])
    cards = _CARD.findall(page)
    if len(cards) != page.count(_CARD_OPEN) or page.count("card--listing") != len(cards):
        raise ParseError("cards", len(cards), why="a card the parser can't read")
    if not 1 <= first <= last <= total or len(cards) != last - first + 1:
        raise ParseError("cards", (len(cards), first, last, total), why="count ≠ range")
    out = [_card(href, body, seen_at) for href, body in cards]
    if len({x.id for x in out}) != len(out):
        raise ParseError("id", len(out), why="duplicate ids")
    return SbsPage(listings=out, first=first, last=last, total=total)


def fetch_sbs(
    get: Callable[[str], str],
    sleep: Callable[[float], object],
    seen_at: datetime,
    *,
    max_pages: int,
    pause: float,
) -> list[SbsListing]:
    """Every Stockholm listing: page 1, then /page/N/ until the range reaches the total, with
    `pause` seconds between pages and never more than `max_pages` requests."""
    out: list[SbsListing] = []
    total: int | None = None
    n = 1
    while True:
        p = parse_sbs_page(get(page_url(n)), seen_at)
        if p.first != len(out) + 1 or (total is not None and p.total != total):
            raise ParseError("Visar", (n, p.first, p.total), why="pages don't line up")
        total = p.total
        out += p.listings
        if p.last == p.total:
            break
        if n >= max_pages:
            raise ParseError("pages", p.total, why=f"more than {max_pages} pages")
        n += 1
        sleep(pause)
    if len({x.id for x in out}) != len(out):
        raise ParseError("id", len(out), why="a listing on two pages")
    return out


def allowed(url: str) -> bool:
    return url == LIST_URL or bool(_PAGE.fullmatch(url))


class _ListOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another list page; anything else is an error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError("SBS redirected outside the list pages")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ListOnlyRedirects())


def sbs_get(url: str, *, opener=None) -> str:
    """A plain GET to an SBS Stockholm list page (never anything else)."""
    if not allowed(url):
        raise ValueError("sbs_get only fetches SBS's Stockholm list pages")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with (opener or _OPENER.open)(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8")
