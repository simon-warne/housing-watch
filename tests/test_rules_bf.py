"""M5 part B: BF status, scope, eligibility and ranking (pure functions)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.bf import BfDetail, parse_bf_list
from housing_watch.config import load_config
from housing_watch.rules import BfStatus, bf_classify, bf_ranked, bf_status

FIX = Path(__file__).parent / "fixtures" / "bf"
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
SINCE = date(2021, 1, 1)  # made up
IN_WINDOW = BfDetail(move_in=date(2027, 7, 1), top_dates=(date(2021, 6, 1), date(2022, 1, 1)))


@pytest.fixture(scope="module")
def by_id():
    xs = parse_bf_list((FIX / "2026-11-26-alla-annonser.json").read_text("utf-8"), T0)
    return {x.id: x for x in xs}


@pytest.fixture
def corridor(by_id):
    """Norra Djurgården (tier 1) student corridor, 6,213 kr, 1 room."""
    return by_id["202619429"]


@pytest.mark.parametrize(
    ("top", "want"),
    [
        ((), BfStatus.NO_APPLICANTS_SHOWN),
        ((date(2021, 1, 2),), BfStatus.WINNABLE),
        ((date(2021, 1, 1), date(2025, 1, 1)), BfStatus.TIED),
        ((date(2025, 1, 1), date(2020, 12, 31)), BfStatus.BEHIND),  # the earliest date counts
    ],
)
def test_status_against_earliest_top_date(top, want):
    assert bf_status(top, SINCE) == want


def test_no_queue_date_set():
    assert bf_status((date(2021, 1, 2),), None) == BfStatus.NO_QUEUE_DATE


def test_eligible_corridor_in_stretch_band(corridor):
    v = bf_classify(corridor, IN_WINDOW, CFG, SINCE)
    assert v.status == BfStatus.WINNABLE
    assert v.tier == 0 and v.category == "alert" and v.in_window
    assert v.eligible and v.over_budget  # 6,213 kr: tier 1 stretch, winnable


def test_stretch_needs_tier1(corridor):
    v = bf_classify(replace(corridor, district="Kista"), IN_WINDOW, CFG, SINCE)
    assert v.tier == 1 and not v.eligible


def test_stretch_needs_a_chance(corridor):
    late = date(2022, 6, 1)
    assert not bf_classify(corridor, IN_WINDOW, CFG, late).eligible


def test_within_rent_eligible_when_behind_too(corridor):
    v = bf_classify(replace(corridor, rent=5000), IN_WINDOW, CFG, date(2023, 1, 1))
    assert v.eligible and v.status == BfStatus.BEHIND and not v.over_budget


def test_above_stretch_never_eligible(corridor):
    assert not bf_classify(replace(corridor, rent=7001), IN_WINDOW, CFG, SINCE).eligible


def test_out_of_window_or_no_detail(corridor):
    early = replace(IN_WINDOW, move_in=date(2027, 1, 1))
    v = bf_classify(corridor, early, CFG, SINCE)
    assert not v.in_window and not v.eligible
    v = bf_classify(corridor, None, CFG, SINCE)
    assert v.in_window is None and not v.eligible and v.status == BfStatus.PENDING


def test_out_of_scope_municipality(by_id):
    v = bf_classify(by_id["202601471"], IN_WINDOW, CFG, SINCE)  # Huddinge
    assert v.tier is None and not v.eligible


def test_two_rooms_digest_only(corridor):
    v = bf_classify(replace(corridor, rooms=2.0, rent=5000), IN_WINDOW, CFG, SINCE)
    assert v.category == "digest" and not v.eligible


def test_regular_rental_is_not_watched(corridor):
    v = bf_classify(replace(corridor, type="Hyresrätt", rent=5000), IN_WINDOW, CFG, SINCE)
    assert v.category == "other" and not v.eligible


def test_bostad_snabbt_is_first_come_whatever_the_type(corridor):
    x = replace(corridor, type="Hyresrätt", bostad_snabbt=True, rent=5000)
    v = bf_classify(x, BfDetail(move_in=date(2027, 7, 1), top_dates=()), CFG, SINCE)
    assert v.category == "alert" and v.status == BfStatus.FIRST_COME and v.eligible


def test_project_ad_without_rent_is_not_eligible(corridor):
    assert not bf_classify(replace(corridor, rent=None), IN_WINDOW, CFG, SINCE).eligible


def test_ranking_tier_then_ground_floor_then_rent(corridor):
    a = bf_classify(replace(corridor, id="1", rent=5900), IN_WINDOW, CFG, SINCE)
    b = bf_classify(replace(corridor, id="2", rent=4000, floor=0), IN_WINDOW, CFG, SINCE)
    c = bf_classify(replace(corridor, id="3", rent=3000, district="Kista"), IN_WINDOW, CFG, SINCE)
    d = bf_classify(replace(corridor, id="4", rent=5000), IN_WINDOW, CFG, SINCE)
    assert [v.listing.id for v in bf_ranked([c, b, a, d])] == ["4", "1", "2", "3"]
