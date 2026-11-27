"""M6: Heimstaden list parser (the page's embedded `hose_objects_data` JSON) and fetch guard.

Only the saved page in tests/fixtures/heimstaden/, never the live site."""

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.heimstaden import (
    BY_AGREEMENT,
    LIST_URL,
    HsListing,
    allowed,
    hs_get,
    parse_hs_page,
)
from housing_watch.sssb import USER_AGENT, ParseError

FIX = Path(__file__).parent / "fixtures" / "heimstaden"
LIST = FIX / "2026-11-26-sok-lagenhet.html.gz"
SEEN = datetime(2026, 11, 26, 21, 5, tzinfo=UTC)
DISTRICT = "Stor-Stockholm"
_MARK = "window.hose_objects_data = "


def page_text() -> str:
    return gzip.decompress(LIST.read_bytes()).decode("utf-8")


PAGE = page_text()


def data() -> dict:
    i = PAGE.index(_MARK) + len(_MARK)
    return json.JSONDecoder().raw_decode(PAGE[i:])[0]


def page_with(d: dict) -> str:
    """A minimal page carrying `d` the way the site does."""
    blob = json.dumps(d, ensure_ascii=False)
    return f"<html><script>\n    {_MARK}{blob};\n    window.hose_map_style = 1;</script></html>"


def edit(oid: str, **changes) -> str:
    d = data()
    (o,) = [o for o in d["objects"] if o["rental_object_id"] == oid]
    o.update(changes)
    return page_with(d)


@pytest.fixture(scope="module")
def listings() -> list[HsListing]:
    return parse_hs_page(PAGE, SEEN, DISTRICT)


@pytest.fixture(scope="module")
def by_id(listings) -> dict[str, HsListing]:
    return {x.id: x for x in listings}


# --- parse_hs_page on the real page ---


def test_only_the_district_is_kept(listings):
    assert len(listings) == 20
    assert len({x.id for x in listings}) == 20


def test_bandhagen(by_id):
    x = by_id["6937101-1210"]
    assert x == HsListing(
        id="6937101-1210",
        location="Stockholm - Bandhagen",
        address="Önskehemsgatan 43",
        rooms="1 rum och kök",
        rent=6114,
        size_m2=33,
        move_in=date(2027, 2, 1),
        restricted=False,
        labels=(),
        seen_at=SEEN,
    )


def test_taby_kyrkby_is_the_one_cheap_one_room(listings, by_id):
    x = by_id["6933111-1005"]
    assert (x.location, x.rooms, x.rent, x.move_in) == (
        "Täby - Täby Kyrkby",
        "1 rum och kokvrå",
        5080,
        date(2027, 2, 1),
    )
    cheap_one_room = [y for y in listings if y.rooms.startswith("1 rum") and y.rent <= 6000]
    assert [y.id for y in cheap_one_room] == ["6933111-1005"]


def test_mislabelled_area_kept_as_shown(by_id):
    # Österåker shows as "Stockholm - Åkersberga"; we keep the site's words.
    assert by_id["6854100-1501"].location == "Stockholm - Åkersberga"


def test_address_is_trimmed(by_id):
    assert by_id["1460130-1001"].address == "Sparres Väg 14"


def test_now_by_agreement_reads_as_none():
    page = edit(
        "6856130-1406",
        available_from_date="25 november, 2026",
        available_from_date_text=BY_AGREEMENT,
    )
    by = {x.id: x for x in parse_hs_page(page, SEEN, DISTRICT)}
    assert by["6856130-1406"].move_in is None


def test_other_districts_are_not_kept():
    assert {x.location.split(" - ")[0] for x in parse_hs_page(PAGE, SEEN, "Stor-Malmö")} >= {
        "Malmö",
        "Lund",
    }


def test_labels_and_restriction_are_kept():
    page = edit("6937101-1210", labels=["Nyproduktion", "Nyproduktion"], restriction="1")
    x = {x.id: x for x in parse_hs_page(page, SEEN, DISTRICT)}["6937101-1210"]
    assert x.labels == ("Nyproduktion",)
    assert x.restricted is True


def test_district_with_no_listings_is_empty():
    assert parse_hs_page(PAGE, SEEN, "Stor-Uppsala") == []


# --- strict: anything unexpected raises ---


def _broken(**changes):
    return edit("6937101-1210", **changes)


@pytest.mark.parametrize(
    "page",
    [
        pytest.param("<html></html>", id="no data"),
        pytest.param(PAGE.replace(_MARK, "window.other = "), id="renamed"),
        pytest.param(page_with(data()) + page_with(data()), id="twice"),
        pytest.param(page_with({**data(), "total_nr_of_objects": 424}), id="total mismatch"),
        pytest.param(page_with({**data(), "total_nr_of_objects": 0, "objects": []}), id="empty"),
        pytest.param(page_with({"objects": data()["objects"]}), id="no total"),
        pytest.param(PAGE[: PAGE.index(_MARK) + 5000], id="truncated"),
        pytest.param(_broken(rental_object_id="6933111-1005"), id="duplicate id"),
        pytest.param(_broken(publish_status="draft"), id="unpublished"),
        pytest.param(_broken(rental_cost=""), id="no rent"),
        pytest.param(_broken(rental_cost="6 114 kr"), id="rent text"),
        pytest.param(_broken(rental_cost="0"), id="zero rent"),
        pytest.param(_broken(size_main="33"), id="size as text"),
        pytest.param(_broken(rooms=""), id="no rooms"),
        pytest.param(_broken(street_address=" "), id="no address"),
        pytest.param(_broken(location=""), id="no location"),
        pytest.param(_broken(district_name=""), id="no district"),
        pytest.param(
            _broken(available_from_date="1 febr, 2027", available_from_date_text="1 febr, 2027"),
            id="unknown month",
        ),
        pytest.param(
            _broken(
                available_from_date="31 februari, 2027",
                available_from_date_text="31 februari, 2027",
            ),
            id="impossible date",
        ),
        pytest.param(_broken(available_from_date_text="1 mars, 2027"), id="text ≠ date"),
        pytest.param(_broken(available_from_date_text="Snart"), id="odd text"),
        pytest.param(_broken(labels="Nyproduktion"), id="labels not a list"),
        pytest.param(_broken(labels=[""]), id="empty label"),
        pytest.param(_broken(restriction=None), id="no restriction"),
    ],
)
def test_strict(page):
    with pytest.raises(ParseError):
        parse_hs_page(page, SEEN, DISTRICT)


def test_out_of_district_listing_must_still_parse():
    # A broken listing elsewhere still means the page changed shape.
    d = data()
    (o,) = [o for o in d["objects"] if o["district_name"] == "Stor-Malmö"][:1]
    o["rental_cost"] = ""
    with pytest.raises(ParseError):
        parse_hs_page(page_with(d), SEEN, DISTRICT)


def test_seen_at_must_be_tz_aware():
    with pytest.raises(ParseError):
        parse_hs_page(PAGE, datetime(2026, 11, 26, 21, 5), DISTRICT)


# --- hs_get: one plain GET of one URL, gzip, no redirects ---


class Resp:
    def __init__(self, body: bytes, encoding: str | None = None):
        self.body, self.encoding = body, encoding
        self.headers = self

    def get(self, name, default=None):
        return self.encoding if name.lower() == "content-encoding" else default

    def get_content_charset(self):
        return "utf-8"

    def read(self, *a):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_hs_get_is_a_plain_get_with_our_user_agent_and_gzip():
    seen = {}

    def opener(req, timeout):
        seen.update(
            url=req.full_url,
            method=req.get_method(),
            ua=req.get_header("User-agent"),
            enc=req.get_header("Accept-encoding"),
            timeout=timeout,
        )
        return Resp(gzip.compress("<html>Önskehemsgatan</html>".encode()), "gzip")

    assert hs_get(LIST_URL, opener=opener) == "<html>Önskehemsgatan</html>"
    assert seen == {
        "url": LIST_URL,
        "method": "GET",
        "ua": USER_AGENT,
        "enc": "gzip",
        "timeout": 30,
    }


def test_hs_get_reads_a_plain_body_too():
    assert hs_get(LIST_URL, opener=lambda r, timeout: Resp(b"<html></html>")) == "<html></html>"


def test_hs_get_refuses_other_encodings():
    with pytest.raises(ValueError):
        hs_get(LIST_URL, opener=lambda r, timeout: Resp(b"x", "br"))


@pytest.mark.parametrize(
    "url",
    [
        "https://heimstaden.com/se/sok-lagenhet/",
        "https://heimstaden.com/se/sok-lagenhet/Bandhagen-33m²-6937101-1210/",
        LIST_URL.replace("https://", "http://"),
        LIST_URL + "&x=1",
        LIST_URL.replace("heimstaden.com", "heimstaden.com.evil.example"),
        "https://mitt.heimstaden.com/",
    ],
)
def test_hs_get_only_fetches_the_list(url):
    assert not allowed(url)
    with pytest.raises(ValueError):
        hs_get(url, opener=lambda *a, **k: pytest.fail("must not fetch"))


def test_hs_get_follows_no_redirect():
    import urllib.error

    from housing_watch.heimstaden import _NoRedirects

    h = _NoRedirects()
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(None, None, 301, "Moved", {}, LIST_URL)
