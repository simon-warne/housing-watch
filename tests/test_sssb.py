"""M1 parser tests. Run only against saved SSSB responses in tests/fixtures/."""

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.sssb import (
    LISTINGS_KEY,
    TOTAL_KEY,
    Detail,
    Listing,
    ParseError,
    load_jsonp,
    parse_deadline,
    parse_detail,
    parse_floor,
    parse_interest,
    parse_list,
    parse_rent,
)

FIX = Path(__file__).parent / "fixtures"
SEEN = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)
STHLM = ZoneInfo("Europe/Stockholm")


def load(name: str) -> dict:
    return load_jsonp((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def listing_payload() -> dict:
    return load("2026-11-26-list.jsonp")


@pytest.fixture
def detail_payload() -> dict:
    return load("2026-11-26-detail-5102-3203-028.jsonp")


# 1. Full list, first listing exact
def test_list_fixture_parses_all_24(listing_payload):
    listings = parse_list(listing_payload, SEEN)
    assert len(listings) == 24
    assert all(isinstance(x, Listing) for x in listings)


def test_first_listing_every_field(listing_payload):
    first = parse_list(listing_payload, SEEN)[0]
    assert first == Listing(
        id="5102-3203-028",
        area="Strix",
        area_code="STRIX",
        address="Armégatan 32 A / 1110",
        type="Rum i korridor",
        size_m2=16.0,
        rent=3841,
        floor=1,
        flags=("10 mån hyra", "Elström ingår", "Max 4 år"),
        move_in=date(2026, 12, 16),
        published=date(2026, 11, 26),
        top_days=132,
        applicants=5,
        detail_url=(
            "http://minasidor.sssb.se/lediga-bostader/lagenhet/?refid="
            "59346d4b4c6d72334639635355527562445a55345766746a4e2f4a50586974415070374749384b4d5752553d"
        ),
        seen_at=SEEN,
    )


# 2. Sanity across all listings
def test_all_listings_sane(listing_payload):
    listings = parse_list(listing_payload, SEEN)
    assert len({x.id for x in listings}) == 24
    for x in listings:
        assert x.rent > 0 and x.size_m2 > 0
        assert x.top_days is not None and x.top_days > 0 and x.applicants > 0
        assert x.area and x.address and x.type
        assert x.seen_at == SEEN
    by_id = {x.id: x for x in listings}
    assert by_id["2118-0403-042"].floor == 0  # Bottenvåning
    assert by_id["6202-1203-001"].rent == 13649
    assert by_id["2303-0920-004"].size_m2 == 60.0
    assert by_id["2303-0920-004"].top_days == 1553
    assert by_id["2303-0920-004"].applicants == 17
    assert by_id["6202-1203-001"].flags == ()


# 3. Helpers
@pytest.mark.parametrize(
    "raw,expected",
    [("3\xa0841", 3841), ("3 841", 3841), ("13\xa0649", 13649), ("950", 950)],
)
def test_parse_rent(raw, expected):
    assert parse_rent(raw) == expected


@pytest.mark.parametrize("raw,expected", [("Bottenvåning", 0), ("1", 1), ("20", 20)])
def test_parse_floor(raw, expected):
    assert parse_floor(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("132 (5st)", (132, 5)),
        ("1553 (17st)", (1553, 17)),
        (None, (None, 0)),
        ("", (None, 0)),
        ("0 (0st)", (None, 0)),
    ],
)
def test_parse_interest(raw, expected):
    assert parse_interest(raw) == expected


def test_parse_deadline():
    got = parse_deadline("x <div>Kan bokas till 2026-11-30 klockan 16:00. Erbjudande …</div>")
    assert got == datetime(2026, 11, 30, 16, 0, tzinfo=STHLM)
    assert got.utcoffset().total_seconds() == 3600


# 4. Layout changes raise
@pytest.mark.parametrize("raw", ["ca 3000", "", None, "3,841", "3 841 kr", 3841, "-100", "0"])
def test_bad_rent_raises(raw):
    with pytest.raises(ParseError):
        parse_rent(raw)


@pytest.mark.parametrize("raw", ["Källare", "", None, "-1", "1.5", 1, "0", "007"])
def test_bad_floor_raises(raw):
    with pytest.raises(ParseError):
        parse_floor(raw)


@pytest.mark.parametrize("raw", ["132", "132 (5)", "(5st)", "132 (0st)", "0 (3st)", 132])
def test_bad_interest_raises(raw):
    with pytest.raises(ParseError):
        parse_interest(raw)


def _mutate(payload: dict, field: str, value) -> dict:
    p = copy.deepcopy(payload)
    if value is _DELETE:
        del p["data"][LISTINGS_KEY][0][field]
    else:
        p["data"][LISTINGS_KEY][0][field] = value
    return p


_DELETE = object()


@pytest.mark.parametrize(
    "field,value",
    [
        ("hyra", "ca 3000"),
        ("hyra", None),
        ("antalIntresse", "132"),
        ("vaning", "Källare"),
        ("objektNr", _DELETE),
        ("objektNr", ""),
        ("adress", None),
        ("yta", "16"),
        ("yta", 0),
        ("yta", True),
        ("yta", float("nan")),
        ("yta", float("inf")),
        ("hyraEnhet", "kr/år"),
        ("hyraEnhet", None),
        ("hyraLabel", "Årshyra:"),
        ("inflyttningDatum", "16 dec"),
        ("publiceratDatum", None),
        ("egenskaper", "Max 4 år"),
        ("egenskaper", [{"id": "1"}]),
        ("detaljUrl", "http://minasidor.sssb.se/lediga-bostader/lagenhet/"),
    ],
)
def test_listing_layout_change_raises(listing_payload, field, value):
    with pytest.raises(ParseError) as exc:
        parse_list(_mutate(listing_payload, field, value), SEEN)
    assert exc.value.field == field
    if field != "objektNr":
        assert exc.value.objekt_nr == "5102-3203-028"


def test_null_egenskaper_means_no_flags(listing_payload):
    assert parse_list(_mutate(listing_payload, "egenskaper", None), SEEN)[0].flags == ()


def test_renamed_listings_key_raises(listing_payload):
    p = copy.deepcopy(listing_payload)
    p["data"]["objektlista@lagenheter"] = p["data"].pop(LISTINGS_KEY)
    with pytest.raises(ParseError):
        parse_list(p, SEEN)


def test_total_mismatch_raises(listing_payload):
    p = copy.deepcopy(listing_payload)
    p["data"][LISTINGS_KEY].pop()
    with pytest.raises(ParseError):
        parse_list(p, SEEN)


@pytest.mark.parametrize("total", ["tjugofyra", None, "", "²", "-1"])
def test_bad_total_raises(listing_payload, total):
    p = copy.deepcopy(listing_payload)
    p["html"][TOTAL_KEY] = total
    with pytest.raises(ParseError):
        parse_list(p, SEEN)


def test_naive_seen_at_raises(listing_payload):
    with pytest.raises(ParseError):
        parse_list(listing_payload, datetime(2026, 11, 26, 17, 17))


@pytest.mark.parametrize(
    "text",
    [
        "<!DOCTYPE html><html><body>Fel</body></html>",
        "",
        "cb(not json);",
        "cb([1, 2]);",
        'cb({"yta": NaN});',
        'cb({"yta": Infinity});',
    ],
)
def test_not_jsonp_raises(text):
    with pytest.raises(ParseError):
        load_jsonp(text)


def test_load_jsonp_strips_wrapper():
    assert load_jsonp('cb({"a": 1});\n') == {"a": 1}
    assert load_jsonp('cb({"a": "x);y"})') == {"a": "x);y"}


# 5. Empty list
def test_empty_list_without_total_check():
    assert parse_list(load("2026-11-26-list-empty.jsonp"), SEEN, check_total=False) == []


def test_empty_list_with_total_check_raises():
    with pytest.raises(ParseError):
        parse_list(load("2026-11-26-list-empty.jsonp"), SEEN)


# 6. Detail
NR = "5102-3203-028"


def test_detail_fixture(detail_payload):
    assert parse_detail(detail_payload, NR) == Detail(
        deadline=datetime(2026, 11, 30, 16, 0, tzinfo=STHLM), applicants=5, top_days=132
    )


def test_detail_one_person(detail_payload):
    p = copy.deepcopy(detail_payload)
    p["html"]["objektintresse"] = "Kan bokas till 2027-06-01 klockan 09:30."
    p["html"]["objektintressestatus"] = (
        "Denna bostad har bokats av 1 person, varav den med flest ködagar just nu har 7 ködagar."
    )
    got = parse_detail(p, NR)
    assert got == Detail(
        deadline=datetime(2027, 6, 1, 9, 30, tzinfo=STHLM), applicants=1, top_days=7
    )
    assert got.deadline.utcoffset().total_seconds() == 7200  # summer time


@pytest.mark.parametrize("status", [None, "", "  "])
def test_detail_nobody_booked(detail_payload, status):
    # Assumption (plan M1): no bookings shows as an empty status. Deadline must survive.
    p = copy.deepcopy(detail_payload)
    p["html"]["objektintressestatus"] = status
    assert parse_detail(p, NR) == Detail(
        deadline=datetime(2026, 11, 30, 16, 0, tzinfo=STHLM), applicants=0, top_days=None
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("objektintresse", "Kan bokas till 30 november klockan 16:00."),
        ("objektintresse", "Kan bokas till 2026-13-40 klockan 16:00."),
        ("objektintresse", None),
        ("objektintressestatus", "Denna bostad har bokats av flera personer."),
        ("objektintressestatus", "Ingen har bokat denna bostad."),
        ("objektintressestatus", 5),
        ("objektinformation@lagenheter", None),
        ("objektinformation@lagenheter", "<dl></dl>"),
    ],
)
def test_detail_layout_change_raises(detail_payload, key, value):
    p = copy.deepcopy(detail_payload)
    p["html"][key] = value
    with pytest.raises(ParseError):
        parse_detail(p, NR)


def test_detail_for_other_listing_raises(detail_payload):
    with pytest.raises(ParseError) as exc:
        parse_detail(detail_payload, "6202-1203-001")
    assert exc.value.raw == NR


def test_detail_missing_html_raises():
    with pytest.raises(ParseError):
        parse_detail({"data": {}}, NR)


def test_fixture_is_valid_json_after_strip():
    # Guards the fixture itself: plain json can read it once the wrapper is gone.
    text = (FIX / "2026-11-26-list.jsonp").read_text(encoding="utf-8")
    assert json.loads(text[3:-2])["html"][TOTAL_KEY] == "24"
