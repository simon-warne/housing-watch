"""M6: SBS (Studentbostäder i Norden) list parser, page walk and fetch guard.

Only saved pages in tests/fixtures/sbs/, never the live site."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.sbs import (
    LIST_URL,
    SbsListing,
    allowed,
    fetch_sbs,
    page_url,
    parse_sbs_page,
    sbs_get,
)
from housing_watch.sssb import USER_AGENT, ParseError
from tests.test_bf import FakeResponse

FIX = Path(__file__).parent / "fixtures" / "sbs"
P1 = FIX / "2026-11-26-lediga-stockholm.html"
P2 = FIX / "2026-11-26-lediga-stockholm-page-2.html"
DETAIL = FIX / "2026-11-26-detail-nekvagen-23-lgh-1403.html"
SEEN = datetime(2026, 11, 26, 22, 25, tzinfo=UTC)


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def edited(p: Path, old: str, new: str, count: int = 1) -> str:
    s = text(p)
    assert s.count(old) >= count, f"fixture lacks {old!r}"
    return s.replace(old, new, count)


@pytest.fixture(scope="module")
def page1():
    return parse_sbs_page(text(P1), SEEN)


@pytest.fixture(scope="module")
def by_id(page1) -> dict[str, SbsListing]:
    return {x.id: x for x in page1.listings}


# --- parse_sbs_page on the fixtures ---


def test_page_one_has_15_of_16(page1):
    assert (page1.first, page1.last, page1.total) == (1, 15, 16)
    assert len(page1.listings) == 15
    assert len({x.id for x in page1.listings}) == 15


def test_page_two_has_the_16th():
    p = parse_sbs_page(text(P2), SEEN)
    assert (p.first, p.last, p.total) == (16, 16, 16)
    [x] = p.listings
    assert x == SbsListing(
        id="nekvagen-35-41-2",
        tag="Student & Ungdom",
        city="Stockholm",
        address="Nekvägen 35-41",
        move_in=date(2027, 2, 1),
        rent=7131,
        size_m2=16,
        rooms="1 rum",
        labels=(),
        seen_at=SEEN,
    )


def test_a_full_card(by_id):
    assert by_id["nekvagen-23-lgh-1403"] == SbsListing(
        id="nekvagen-23-lgh-1403",
        tag="Studentbostad",
        city="Stockholm",
        address="Nekvägen 23 lgh 1403",
        move_in=date(2027, 1, 1),
        rent=6777,
        size_m2=21,
        rooms="1 rum",
        labels=(),
        seen_at=SEEN,
    )


def test_move_in_by_agreement_is_none(by_id):
    x = by_id["nekvagen-13-lgh-1103"]
    assert x.move_in is None
    assert x.labels == ("Möblerad", "Kampanj")


def test_corridor_room_and_short_term_tag(by_id):
    assert by_id["nekvagen-1-lgh-1003-2"].rooms == "Korridor"
    assert by_id["nekvagen-3-lgh-1103"].tag == "Korttidkontrakt – Student"


def test_repeated_label_is_kept_once(by_id):
    assert by_id["nekvagen-41-lgh-1507"].labels == ("Kampanj",)


def test_dates_in_nov_and_dec(by_id):
    assert by_id["nekvagen-35-41"].move_in == date(2026, 11, 27)
    assert by_id["bromstensvagen-150-lgh-1110"].move_in == date(2026, 12, 7)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("1 mars 2027", date(2027, 3, 1)),
        ("1 maj 2027", date(2027, 5, 1)),
        ("15 juni 2027", date(2027, 6, 15)),
        ("1 aug. 2027", date(2027, 8, 1)),
        ("1 sep. 2027", date(2027, 9, 1)),
        ("31 okt. 2027", date(2027, 10, 31)),
        ("1 januari 2027", date(2027, 1, 1)),
    ],
)
def test_swedish_month_names(raw, want):
    page = edited(P2, "Inflyttning: 1 feb. 2027", f"Inflyttning: {raw}")
    assert parse_sbs_page(page, SEEN).listings[0].move_in == want


@pytest.mark.parametrize(
    "raw",
    ["1 foo. 2027", "snart", "32 jan. 2027", "1 feb 27", "2027-02-01", ""],
)
def test_unknown_move_in_raises(raw):
    page = edited(P2, "Inflyttning: 1 feb. 2027", f"Inflyttning: {raw}")
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_the_detail_page_is_not_a_list_page():
    with pytest.raises(ParseError):
        parse_sbs_page(text(DETAIL), SEEN)


def test_missing_rent_raises():
    with pytest.raises(ParseError):
        parse_sbs_page(edited(P2, "<li>7 131 kr/mån</li>", ""), SEEN)


def test_unknown_detail_item_raises():
    with pytest.raises(ParseError):
        parse_sbs_page(edited(P2, "<li>16 m²</li>", "<li>16 m²</li><li>Våning 3</li>"), SEEN)


def test_city_other_than_stockholm_raises():
    page = edited(
        P2,
        '<span class="listing__city is-style-tag">Stockholm</span>',
        ('<span class="listing__city is-style-tag">Uppsala</span>'),
    )
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_card_count_must_match_the_range():
    with pytest.raises(ParseError):
        parse_sbs_page(edited(P1, "Visar 1 &#8211; 15 av 16", "Visar 1 &#8211; 14 av 16"), SEEN)


def test_no_result_count_raises():
    with pytest.raises(ParseError):
        parse_sbs_page(edited(P1, "Visar 1 &#8211; 15 av 16 resultat", ""), SEEN)


def test_a_card_the_pattern_misses_raises():
    # A changed card markup must not silently drop a listing.
    page = edited(
        P2, '<a class="card card--listing" href=', '<a class="card card--listing x" href='
    )
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_card_link_outside_the_listings_raises():
    page = edited(
        P2,
        "https://sbsstudent.se/lediga-bostader/nekvagen-35-41-2/",
        ("https://example.com/nekvagen-35-41-2/"),
    )
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_naive_seen_at_raises():
    with pytest.raises(ParseError):
        parse_sbs_page(text(P2), datetime(2026, 11, 26, 22, 25))


# --- fetch_sbs: walks /page/N/ until the range reaches the total ---


def fake_get(pages: dict[str, str]):
    calls: list[str] = []

    def get(url: str) -> str:
        calls.append(url)
        return pages[url]

    return get, calls


def test_page_urls():
    assert (
        page_url(1)
        == LIST_URL
        == "https://sbsstudent.se/lediga-bostader/?qt_mll_search_tags=Stockholm"
    )
    assert (
        page_url(2) == "https://sbsstudent.se/lediga-bostader/page/2/?qt_mll_search_tags=Stockholm"
    )


def test_fetch_walks_both_pages_with_a_pause():
    get, calls = fake_get({page_url(1): text(P1), page_url(2): text(P2)})
    sleeps: list[float] = []
    xs = fetch_sbs(get, sleeps.append, SEEN, max_pages=5, pause=5)
    assert len(xs) == 16
    assert calls == [page_url(1), page_url(2)]
    assert sleeps == [5]


def test_one_page_when_everything_fits():
    page = edited(P2, "Visar 16 &#8211; 16 av 16", "Visar 1 &#8211; 1 av 1")
    get, calls = fake_get({page_url(1): page})
    sleeps: list[float] = []
    assert len(fetch_sbs(get, sleeps.append, SEEN, max_pages=5, pause=5)) == 1
    assert calls == [page_url(1)] and sleeps == []


def test_page_cap_raises_before_fetching_more():
    get, calls = fake_get({page_url(1): text(P1)})
    with pytest.raises(ParseError):
        fetch_sbs(get, lambda s: None, SEEN, max_pages=1, pause=5)
    assert calls == [page_url(1)]


def test_total_changing_between_pages_raises():
    p2 = edited(P2, "Visar 16 &#8211; 16 av 16", "Visar 16 &#8211; 16 av 17")
    get, _ = fake_get({page_url(1): text(P1), page_url(2): p2})
    with pytest.raises(ParseError):
        fetch_sbs(get, lambda s: None, SEEN, max_pages=5, pause=5)


def test_page_starting_at_the_wrong_place_raises():
    # Page 1 served again as page 2 (a listing shifted or the site ignored the page).
    get, _ = fake_get({page_url(1): text(P1), page_url(2): text(P1)})
    with pytest.raises(ParseError):
        fetch_sbs(get, lambda s: None, SEEN, max_pages=5, pause=5)


def test_same_listing_on_two_pages_raises():
    p2 = edited(P2, "nekvagen-35-41-2/", "nekvagen-23-lgh-1403/")
    get, _ = fake_get({page_url(1): text(P1), page_url(2): p2})
    with pytest.raises(ParseError):
        fetch_sbs(get, lambda s: None, SEEN, max_pages=5, pause=5)


# --- sbs_get: a plain GET to the list pages only ---


def test_sbs_get_is_a_plain_get_with_our_user_agent():
    seen = {}

    def opener(req, timeout):
        seen.update(method=req.get_method(), ua=req.get_header("User-agent"), timeout=timeout)
        return FakeResponse(b"<html></html>")

    assert sbs_get(page_url(2), opener=opener) == "<html></html>"
    assert seen == {"method": "GET", "ua": USER_AGENT, "timeout": 30}


@pytest.mark.parametrize(
    "url",
    [
        "https://sbsstudent.se/",
        "https://sbsstudent.se/lediga-bostader/",
        "https://sbsstudent.se/lediga-bostader/nekvagen-23-lgh-1403/",
        "https://sbsstudent.se/lediga-bostader/?paged=2&qt_mll_search_tags=Stockholm",
        "https://sbsstudent.se/lediga-bostader/page/1/?qt_mll_search_tags=Stockholm",
        "https://sbsstudent.se/lediga-bostader/page/0/?qt_mll_search_tags=Stockholm",
        "https://sbsstudent.se/lediga-bostader/page/2/?qt_mll_search_tags=Uppsala",
        "http://sbsstudent.se/lediga-bostader/?qt_mll_search_tags=Stockholm",
        "https://sbsstudent.se.evil.example/lediga-bostader/?qt_mll_search_tags=Stockholm",
        "https://studentbostad.sbsstudent.se/",
        LIST_URL + "&x=1",
    ],
)
def test_sbs_get_only_fetches_the_list_pages(url):
    assert not allowed(url)
    with pytest.raises(ValueError):
        sbs_get(url, opener=lambda *a, **k: pytest.fail("must not fetch"))


def test_a_redirect_is_followed_only_to_a_list_page():
    from urllib.request import Request

    from housing_watch.sbs import _ListOnlyRedirects

    h = _ListOnlyRedirects()
    req = Request(LIST_URL, headers={"User-Agent": USER_AGENT})
    ok = h.redirect_request(req, None, 301, "Moved", {}, page_url(2))
    assert ok is not None and ok.full_url == page_url(2)
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 301, "Moved", {}, "https://sbsstudent.se/logga-in/")


# --- review fixes: nothing on a card may be skipped quietly ---


def test_detail_item_with_attributes_raises():
    page = edited(P2, "<li>16 m²</li>", '<li>16 m²</li><li class="x">Våning 3</li>')
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_label_with_nested_markup_raises():
    page = edited(
        P2,
        '<span class="listing__tag is-style-tag"',
        '<span class="listing__additional-feature is-style-tag"><b>Ny</b></span>'
        '<span class="listing__tag is-style-tag"',
    )
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_a_redirect_is_followed_at_most_once():
    from housing_watch.sbs import _ListOnlyRedirects

    assert _ListOnlyRedirects.max_redirections == 1


# --- an empty list (Simon, 2026-11-27): only the site's exact "no results" shape ---
EMPTY = FIX / "2026-11-27-lediga-stockholm-empty.html"
NO_RESULTS = "<p>Inga lägenheter matchar dina filterinställningar.</p>"


def test_the_empty_result_page_is_zero_listings():
    p = parse_sbs_page(text(EMPTY), SEEN)
    assert (p.listings, p.first, p.last, p.total) == ([], 0, 0, 0)


@pytest.mark.parametrize(
    "change",
    [
        (NO_RESULTS, "<p>Något gick fel.</p>"),  # another message
        (NO_RESULTS, ""),  # no message at all
        (NO_RESULTS, NO_RESULTS + NO_RESULTS),  # twice
        ('<div class="listings grid grid-col-1">', '<div class="other">'),  # outside the list
    ],
)
def test_anything_else_without_cards_still_raises(change):
    with pytest.raises(ParseError):
        parse_sbs_page(edited(EMPTY, *change), SEEN)


def test_no_results_text_next_to_cards_raises():
    page = edited(
        P2,
        '<div class="listings-result-count">',
        NO_RESULTS + '<div class="listings-result-count">',
    )
    with pytest.raises(ParseError):
        parse_sbs_page(page, SEEN)


def test_fetch_of_an_empty_list_is_one_request_and_no_listings():
    get, calls = fake_get({page_url(1): text(EMPTY)})
    sleeps: list[float] = []
    assert fetch_sbs(get, sleeps.append, SEEN, max_pages=5, pause=5) == []
    assert calls == [page_url(1)] and sleeps == []


def test_an_empty_second_page_raises():
    get, _ = fake_get({page_url(1): text(P1), page_url(2): text(EMPTY)})
    with pytest.raises(ParseError):
        fetch_sbs(get, lambda s: None, SEEN, max_pages=5, pause=5)
