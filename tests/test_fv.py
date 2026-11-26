"""M6: Förvaltaren parser and fetch guard. Only saved pages in tests/fixtures/forvaltaren/."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.fv import (
    LIST_URL,
    FvDetail,
    FvListing,
    detail_url,
    fv_deadline,
    fv_get,
    parse_fv_detail,
    parse_fv_list,
)
from housing_watch.sssb import USER_AGENT, ParseError
from tests.test_bf import FakeResponse

FIX = Path(__file__).parent / "fixtures" / "forvaltaren"
LIST = FIX / "2026-11-26-objects-lagenhet.json"
DETAIL = FIX / "2026-11-26-detail-73-32-1-0102.html"
SEEN = datetime(2026, 11, 26, 21, 3, tzinfo=UTC)
STHLM = ZoneInfo("Europe/Stockholm")
RISSNE = "73-32-1-0102"


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def payload() -> dict:
    return json.loads(text(LIST))


def dump(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def with_item(**changes) -> str:
    """The list fixture with the Rissne item edited (None value = key removed)."""
    p = payload()
    item = next(i for i in p["rentalObjects"] if i["id"] == RISSNE)
    for k, v in changes.items():
        if v is None:
            del item[k]
        else:
            item[k] = v
    return dump(p)


@pytest.fixture(scope="module")
def listings() -> dict[str, FvListing]:
    return {x.id: x for x in parse_fv_list(text(LIST), SEEN)}


@pytest.fixture
def rissne(listings) -> FvListing:
    return listings[RISSNE]


# --- list ---
def test_list_parses_all_17(listings):
    assert len(listings) == 17


def test_rissne_every_field(rissne):
    assert rissne == FvListing(
        id=RISSNE,
        area="Rissne",
        address="Pjäsbacken 23",
        municipality="Sundbyberg",
        type="1 rum o kokvrå",
        rooms=1,
        size_m2=42,
        rent=5990,
        floor=-1,
        applicants=40,
        deadline=date(2026, 11, 26),
        special="",
        seen_at=SEEN,
    )


def test_types_and_deadlines_seen(listings):
    assert {x.type for x in listings.values()} == {
        "1 rum o kokvrå",
        "1 rum o kök",
        "2 rum o kök",
        "3 rum o kök",
    }
    assert listings["41-48-3-102"].deadline == date(2026, 12, 1)


def test_deadline_is_end_of_day_stockholm(rissne):
    assert fv_deadline(rissne.deadline) == datetime(2026, 11, 26, 23, 59, tzinfo=STHLM)


def test_empty_list_parses_to_empty():
    assert parse_fv_list(dump({"rentalObjects": []}), SEEN) == []


def test_special_contract_text_is_kept():
    xs = parse_fv_list(with_item(specialContract="  Ungdomsbostad 18–25 "), SEEN)
    assert next(x for x in xs if x.id == RISSNE).special == "Ungdomsbostad 18–25"


@pytest.mark.parametrize(
    "changes",
    [
        {"cost": None},
        {"cost": 0},
        {"cost": "5990"},
        {"cost": True},
        {"cost": 5990.5},
        {"rooms": 0},
        {"size": -1},
        {"floor": "1"},
        {"usercount": -1},
        {"usercount": None},
        {"showDateEnd": "2026-11-26"},
        {"showDateEnd": "2026-11-26T12:00:00"},
        {"showDateEnd": "2026-02-30T00:00:00"},
        {"areaname": " "},
        {"address1": None},
        {"specialContract": None},
        {"specialContract": 3},
        {"id": "73-32-1"},
        {"id": "../wp-admin"},
        {"objectType": {"id": 1}},
        {"objectType": "1 rum"},
    ],
)
def test_layout_changes_raise(changes):
    with pytest.raises(ParseError):
        parse_fv_list(with_item(**changes), SEEN)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not json",
        "[]",
        '{"objects": []}',
        '{"rentalObjects": {}}',
        '{"rentalObjects": [1]}',
        '{"rentalObjects": [{"cost": NaN}]}',
    ],
)
def test_bad_payloads_raise(body):
    with pytest.raises(ParseError):
        parse_fv_list(body, SEEN)


def test_duplicate_ids_raise():
    p = payload()
    p["rentalObjects"].append(p["rentalObjects"][0])
    with pytest.raises(ParseError):
        parse_fv_list(dump(p), SEEN)


def test_naive_seen_at_raises():
    with pytest.raises(ParseError):
        parse_fv_list(text(LIST), datetime(2026, 11, 26, 21, 3))


# --- detail ---
def test_detail_every_field(rissne):
    assert parse_fv_detail(text(DETAIL), rissne) == FvDetail(
        move_in=date(2027, 2, 1), top_points=5022, applicants=40
    )


def _edit(old: str, new: str, count: int = -1) -> str:
    page = text(DETAIL)
    assert old in page
    return page.replace(old, new, count)


def test_detail_of_another_listing_raises(rissne):
    with pytest.raises(ParseError, match="wrong listing"):
        parse_fv_detail(text(DETAIL), replace(rissne, id="73-32-1-0103"))


def test_detail_deadline_must_match_the_list(rissne):
    with pytest.raises(ParseError):
        parse_fv_detail(text(DETAIL), replace(rissne, deadline=date(2026, 11, 27)))


@pytest.mark.parametrize(
    "old,new",
    [
        ("Tillträde</span>\n                2027-02-01", "Tillträde</span>\n  Enligt ök"),
        ("Tillträde</span>", "Inflytt</span>"),
        ("Objektnummer</span>", "Nummer</span>"),
        ("Antal sökande</span>\n                40", "Antal sökande</span>\n  fyrtio"),
        ("Högst köpoäng</span>\n                5 022", "Högst köpoäng</span>\n  5,022"),
    ],
)
def test_detail_layout_changes_raise(rissne, old, new):
    with pytest.raises(ParseError):
        parse_fv_detail(_edit(old, new), rissne)


def test_detail_copies_must_agree(rissne):
    page = _edit("Högst köpoäng</span>\n                5 022", "Högst köpoäng</span>\n 6 000", 1)
    with pytest.raises(ParseError, match="disagree"):
        parse_fv_detail(page, rissne)


def test_no_top_points_row_means_none_shown(rissne):
    page = text(DETAIL).replace('uppercase">Högst köpoäng</span>', 'uppercase">Något</span>')
    d = parse_fv_detail(page, rissne)
    assert d.top_points is None and d.applicants == 40


def test_no_applicants_row_means_none_shown(rissne):
    page = text(DETAIL).replace('uppercase">Antal sökande</span>', 'uppercase">Något</span>')
    assert parse_fv_detail(page, rissne).applicants is None


def test_top_points_with_a_non_breaking_space(rissne):
    page = text(DETAIL).replace("5 022", "5\xa0022")
    assert parse_fv_detail(page, rissne).top_points == 5022


# --- fetch guard ---
def test_urls(rissne):
    assert (
        LIST_URL
        == "https://www.forvaltaren.se/wp-json/fv/v1/objects?group_id=1&group_slug=lagenhet"
    )
    assert detail_url(rissne) == "https://www.forvaltaren.se/objekt/73-32-1-0102"


def test_fv_get_is_a_plain_get_with_our_user_agent():
    seen = {}

    def opener(req, timeout):
        seen.update(method=req.get_method(), ua=req.get_header("User-agent"), timeout=timeout)
        return FakeResponse(b"{}")

    assert fv_get(LIST_URL, opener=opener) == "{}"
    assert seen == {"method": "GET", "ua": USER_AGENT, "timeout": 30}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.forvaltaren.se/",
        "http://www.forvaltaren.se/objekt/73-32-1-0102",
        "https://www.forvaltaren.se/objekt/73-32-1-0102/../wp-admin",
        "https://www.forvaltaren.se/wp-json/fv/v1/objects?group_id=3&group_slug=parkeringsplats",
        "https://minsida.forvaltaren.se/ledigt/detalj/id/73-32-1-0102",
        "https://www.forvaltaren.se.evil.example/objekt/73-32-1-0102",
        LIST_URL + "&x=1",
    ],
)
def test_fv_get_only_fetches_the_list_and_detail_pages(url):
    with pytest.raises(ValueError):
        fv_get(url, opener=lambda *a, **k: pytest.fail("must not fetch"))
