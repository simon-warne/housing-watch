"""M5 part B: Bostadsförmedlingen parser and fetch guard. Only saved pages in tests/fixtures/bf/."""

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.bf import (
    LIST_URL,
    BfDetail,
    BfListing,
    bf_deadline,
    bf_get,
    detail_url,
    parse_bf_detail,
    parse_bf_list,
    parse_move_in,
)
from housing_watch.sssb import USER_AGENT, ParseError

FIX = Path(__file__).parent / "fixtures" / "bf"
LIST = FIX / "2026-11-26-alla-annonser.json"
CORRIDOR = FIX / "2026-11-26-detail-202619429-studentkorridor.html"
YOUTH = FIX / "2026-11-26-detail-202618022-ungdom.html"
SNABBT = FIX / "2026-11-26-detail-202601471-bostad-snabbt.html"
SEEN = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
STHLM = ZoneInfo("Europe/Stockholm")


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def items() -> list[dict]:
    return json.loads(text(LIST))


def dump(xs) -> str:
    return json.dumps(xs, ensure_ascii=False)


@pytest.fixture(scope="module")
def listings() -> dict[str, BfListing]:
    return {x.id: x for x in parse_bf_list(text(LIST), SEEN)}


# --- list ---
def test_list_parses_all_527(listings):
    assert len(listings) == 527
    assert all(isinstance(x, BfListing) for x in listings.values())


def test_corridor_listing_every_field(listings):
    assert listings["202619429"] == BfListing(
        id="202619429",
        apartment_no="59601333",
        district="Norra Djurgården",
        municipality="Stockholm",
        address="Ruddammsvägen 65",
        type="Studentkorridor",
        rooms=1.0,
        size_m2=14.0,
        rent=6213,
        floor=3,
        units=1,
        bostad_snabbt=False,
        advertised_from=listings["202619429"].advertised_from,
        deadline=date(2026, 11, 29),
        url="/bostad/202619429/",
        seen_at=SEEN,
    )
    assert isinstance(listings["202619429"].advertised_from, date)


def test_bostad_snabbt_flag_and_types(listings):
    assert listings["202601471"].bostad_snabbt is True
    assert listings["202601471"].type == "Hyresrätt"
    types = {x.type for x in listings.values()}
    assert {"Studentlägenhet", "Studentkorridor", "Ungdomslägenhet", "UngdomKorttid"} <= types


def test_project_ads_may_lack_rent_size_floor(listings):
    multi = [x for x in listings.values() if x.rent is None]
    assert len(multi) == 11
    assert all(x.size_m2 is None and x.floor is None for x in multi)
    assert all(x.apartment_no is None or x.apartment_no for x in listings.values())


def test_null_rent_on_a_single_flat_raises():
    xs = items()
    one = next(i for i in xs if i["Antal"] == 1 and not i["ProjektId"])
    one["Hyra"] = None
    with pytest.raises(ParseError, match="Hyra"):
        parse_bf_list(dump(xs), SEEN)


@pytest.mark.parametrize(
    "key,value",
    [
        ("LagenhetId", "x"),
        ("LagenhetId", 0),
        ("Stadsdel", ""),
        ("Kommun", None),
        ("Lagenhetstyp", 3),
        ("Hyra", -5),
        ("Hyra", True),
        ("Yta", 0),
        ("AntalRum", "1"),
        ("AnnonseradTill", "29/11/2026"),
        ("AnnonseradTill", "2026-02-30"),
        ("BostadSnabbt", "false"),
        ("Url", "/bostad/1/"),
        ("Antal", 0),
    ],
)
def test_bad_fields_raise(key, value):
    xs = items()
    xs[0][key] = value
    with pytest.raises(ParseError):
        parse_bf_list(dump(xs), SEEN)


def test_missing_key_raises():
    xs = items()
    del xs[3]["AnnonseradTill"]
    with pytest.raises(ParseError, match="AnnonseradTill"):
        parse_bf_list(dump(xs), SEEN)


def test_duplicate_ids_raise():
    xs = items()
    xs.append(xs[0])
    with pytest.raises(ParseError, match="duplicate"):
        parse_bf_list(dump(xs), SEEN)


@pytest.mark.parametrize("body", ["", "<html>", "{}", '{"a": 1}', "[1]", "[NaN]"])
def test_not_a_listing_array_raises(body):
    with pytest.raises(ParseError):
        parse_bf_list(body, SEEN)


def test_empty_array_is_empty():
    assert parse_bf_list("[]", SEEN) == []


def test_seen_at_must_be_aware():
    with pytest.raises(ParseError):
        parse_bf_list("[]", datetime(2026, 11, 26))


# --- deadline ---
def test_deadline_is_end_of_day_stockholm():
    dl = bf_deadline(date(2026, 11, 29))
    assert dl == datetime(2026, 11, 29, 23, 59, tzinfo=STHLM)


# --- detail ---
def test_corridor_detail(listings):
    d = parse_bf_detail(text(CORRIDOR), listings["202619429"])
    assert d == BfDetail(
        move_in=date(2027, 1, 1),
        top_dates=(date(2021, 11, 27), date(2026, 6, 4), date(2026, 11, 9)),
    )


def test_youth_detail(listings):
    d = parse_bf_detail(text(YOUTH), listings["202618022"])
    assert d.move_in == date(2026, 12, 1)
    assert d.top_dates == (date(2020, 1, 17), date(2020, 6, 21), date(2021, 7, 4))


def test_bostad_snabbt_has_no_queue_block(listings):
    d = parse_bf_detail(text(SNABBT), listings["202601471"])
    assert d == BfDetail(move_in=date(2027, 1, 1), top_dates=())


def test_detail_of_another_listing_raises(listings):
    with pytest.raises(ParseError, match="wrong listing"):
        parse_bf_detail(text(CORRIDOR), listings["202618022"])


def test_detail_with_other_apartment_number_raises(listings):
    x = replace(listings["202619429"], apartment_no="1234")
    with pytest.raises(ParseError, match="wrong listing"):
        parse_bf_detail(text(CORRIDOR), x)


def test_detail_deadline_must_match_list(listings):
    x = replace(listings["202619429"], deadline=date(2026, 12, 5))
    with pytest.raises(ParseError, match="Anmälan senast"):
        parse_bf_detail(text(CORRIDOR), x)


def test_detail_without_move_in_raises(listings):
    page = text(CORRIDOR).replace(">Inflytt<", ">Tillträde<")
    with pytest.raises(ParseError, match="Inflytt"):
        parse_bf_detail(page, listings["202619429"])


def test_queue_block_without_dates_raises(listings):
    page = text(CORRIDOR).replace("<li>2021-11-27</li>", "<li>snart</li>")
    with pytest.raises(ParseError, match="kötider"):
        parse_bf_detail(page, listings["202619429"])


def _empty_queue(page: str, inner: str) -> str:
    return re.sub(
        r'(<ul class="u-m-t-half u-list-disc">)\s*<li>2021-11-27</li>.*?(</ul>)',
        lambda m: m.group(1) + inner + m.group(2),
        page,
        count=1,
        flags=re.DOTALL,
    )


def test_queue_block_with_empty_list_means_no_applicants(listings):
    """BF's live shape for a listing nobody has applied to yet (202618849, 2026-11-27)."""
    page = _empty_queue(text(CORRIDOR), "\n                            ")
    assert "2021-11-27" not in page
    assert parse_bf_detail(page, listings["202619429"]).top_dates == ()


@pytest.mark.parametrize("inner", [" &nbsp; ", " <li></li> ", " <p>Inga</p> ", " Inga sökande "])
def test_queue_block_with_odd_content_still_raises(listings, inner):
    page = _empty_queue(text(CORRIDOR), inner)
    with pytest.raises(ParseError, match="kötider"):
        parse_bf_detail(page, listings["202619429"])


def test_queue_block_removed_gives_no_dates(listings):
    page = text(CORRIDOR).replace("Längsta anmälda kötider", "Något annat")
    assert parse_bf_detail(page, listings["202619429"]).top_dates == ()


def test_move_in_as_text_on_the_page(listings):
    page = text(CORRIDOR).replace(
        '<span class="apartment-facts__heading">2027-01-01</span>',
        '<span class="apartment-facts__heading">Så snart som möjligt</span>',
    )
    assert parse_bf_detail(page, listings["202619429"]).move_in == date(2026, 11, 29)


@pytest.mark.parametrize(
    "raw,want",
    [
        ("2027-04-15", date(2027, 4, 15)),
        ("januari 2027", date(2027, 1, 1)),
        ("Augusti 2027", date(2027, 8, 1)),
        ("så snart som möjligt", date(2026, 11, 29)),
    ],
)
def test_parse_move_in(raw, want):
    assert parse_move_in(raw, date(2026, 11, 29)) == want


@pytest.mark.parametrize("raw", ["", "snarast", "2027-13-01", "maj", "våren 2027"])
def test_parse_move_in_rejects(raw):
    with pytest.raises(ParseError):
        parse_move_in(raw, date(2026, 11, 29))


# --- fetching ---
def test_urls(listings):
    assert LIST_URL == "https://bostad.stockholm.se/AllaAnnonser/"
    assert detail_url(listings["202619429"]) == "https://bostad.stockholm.se/bostad/202619429/"


class FakeResponse:
    headers = None

    def __init__(self, body: bytes):
        self.body = body
        self.headers = self

    def get_content_charset(self):
        return "utf-8"

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_bf_get_is_a_plain_get_with_our_user_agent():
    seen = {}

    def opener(req, timeout):
        seen.update(method=req.get_method(), ua=req.get_header("User-agent"), timeout=timeout)
        return FakeResponse(b"[]")

    assert bf_get(LIST_URL, opener=opener) == "[]"
    assert seen == {"method": "GET", "ua": USER_AGENT, "timeout": 30}


@pytest.mark.parametrize(
    "url",
    [
        "https://bostad.stockholm.se/",
        "http://bostad.stockholm.se/AllaAnnonser/",
        "https://bostad.stockholm.se/bostad/12a/",
        "https://bostad.stockholm.se/Minasidor/",
        "https://bostad.stockholm.se.evil.example/AllaAnnonser/",
        "https://bostad.stockholm.se/AllaAnnonser/?x=1",
        "https://minasidor.sssb.se/widgets/?callback=cb",
    ],
)
def test_bf_get_only_fetches_the_list_and_detail_pages(url):
    with pytest.raises(ValueError):
        bf_get(url, opener=lambda *a, **k: pytest.fail("must not fetch"))
