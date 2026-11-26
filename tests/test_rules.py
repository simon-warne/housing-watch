"""M3 rules tests: status, eligibility filters, stretch, ordering (pure functions)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.config import load_config
from housing_watch.rules import Status, classify, ranked, status
from housing_watch.sssb import load_jsonp, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
IN_WINDOW = date(2027, 7, 1)
MINE = 180


@pytest.fixture(scope="module")
def by_id():
    payload = load_jsonp((FIX / "2026-11-26-list.jsonp").read_text(encoding="utf-8"))
    return {x.id: x for x in parse_list(payload, T0)}


@pytest.fixture
def room(by_id):
    """Roslagstull corridor room, 4,523 kr, floor 6, top 175 (2 applicants), moved into window."""
    return replace(by_id["2405-0508-011"], move_in=IN_WINDOW)


@pytest.mark.parametrize(
    ("top", "want"),
    [(None, Status.WINNABLE), (179, Status.WINNABLE), (180, Status.TIED), (181, Status.BEHIND)],
)
def test_status_at_tie_boundary(top, want):
    assert status(top, MINE) == want


def test_eligible_room(room):
    v = classify(room, CFG, MINE)
    assert v.eligible and v.status == Status.WINNABLE and not v.over_budget
    assert v.category == "alert" and v.in_window and v.tier == 2


def test_behind_but_cheap_is_still_eligible(room):
    # Eligible = matches criteria; winnable is a separate question (the digest shows both).
    v = classify(replace(room, top_days=500), CFG, MINE)
    assert v.eligible and v.status == Status.BEHIND


@pytest.mark.parametrize("move_in", [date(2027, 5, 31), date(2027, 11, 1), date(2027, 1, 1)])
def test_outside_window_not_eligible(room, move_in):
    v = classify(replace(room, move_in=move_in), CFG, MINE)
    assert not v.eligible and not v.in_window


@pytest.mark.parametrize("move_in", [date(2027, 6, 1), date(2027, 10, 31)])
def test_window_edges_inclusive(room, move_in):
    assert classify(replace(room, move_in=move_in), CFG, MINE).eligible


@pytest.mark.parametrize(
    ("type_", "category"),
    [
        ("Rum i korridor", "alert"),
        ("1 rum & pentry", "alert"),
        ("1 rum & kök", "alert"),
        ("2 rum & kök", "digest"),
        ("3 rum & pentry", "digest"),
        ("Rum i dubbel", "other"),
    ],
)
def test_type_category(room, type_, category):
    v = classify(replace(room, type=type_), CFG, MINE)
    assert v.category == category
    assert v.eligible == (category == "alert")


@pytest.mark.parametrize(("rent", "ok"), [(6000, True), (6001, False), (7000, False)])
def test_rent_cap_outside_stretch_area(room, rent, ok):
    strix = replace(room, area="Strix", rent=rent)
    v = classify(strix, CFG, MINE)
    assert v.eligible == ok and not v.over_budget


@pytest.mark.parametrize(
    ("rent", "top", "ok"),
    [
        (6001, 179, True),
        (7000, 180, True),  # tied counts
        (7000, 181, False),  # behind: stretch needs winnable
        (7001, None, False),  # above stretch: digest only
    ],
)
def test_stretch_only_when_winnable_in_stretch_area(room, rent, top, ok):
    v = classify(replace(room, rent=rent, top_days=top), CFG, MINE)
    assert v.eligible == ok
    assert v.over_budget == ok


def test_stretch_central_area(by_id):
    idun = replace(by_id["3622-9205-209"], move_in=IN_WINDOW, top_days=100)  # 6,057 kr studio
    v = classify(idun, CFG, MINE)
    assert v.eligible and v.over_budget and v.tier == 0


def test_order_tier_then_ground_floor_then_rent(room):
    a = replace(room, id="a", area="Forum", floor=0, rent=4000)
    b = replace(room, id="b", area="Forum", floor=3, rent=5000)
    c = replace(room, id="c", area="Forum", floor=2, rent=4500)
    d = replace(room, id="d", area="Idun", floor=0, rent=5900)
    e = replace(room, id="e", area="Nowhere", floor=1, rent=3000)
    out = [v.listing.id for v in ranked([classify(x, CFG, MINE) for x in (a, b, c, d, e)])]
    assert out == ["d", "c", "b", "a", "e"]


def test_fixture_today_nothing_eligible(by_id):
    # All fixture listings move in Dec 2026/Jan 2027, outside the default window.
    assert not any(classify(x, CFG, 200).eligible for x in by_id.values())
