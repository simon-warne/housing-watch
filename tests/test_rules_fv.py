"""M6: Förvaltaren status, eligibility and ranking (pure functions)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.config import load_config
from housing_watch.fv import FvDetail, parse_fv_list
from housing_watch.rules import (
    FvStatus,
    fv_category,
    fv_classify,
    fv_ranked,
    fv_status,
    fv_worth_details,
)

FIX = Path(__file__).parent / "fixtures" / "forvaltaren"
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
MINE = 4000  # made up
DET = FvDetail(move_in=date(2027, 5, 1), top_points=3000, applicants=8)


@pytest.fixture(scope="module")
def by_id():
    xs = parse_fv_list((FIX / "2026-11-26-objects-lagenhet.json").read_text("utf-8"), T0)
    return {x.id: x for x in xs}


@pytest.fixture
def duvbo(by_id):
    """Duvbo 1 rum o kokvrå, 4,290 kr, closes 2026-11-30."""
    return by_id["61-10-1-302"]


@pytest.mark.parametrize(
    ("top", "mine", "want"),
    [
        (None, MINE, FvStatus.NO_APPLICANTS_SHOWN),
        (3999, MINE, FvStatus.WINNABLE),
        (4000, MINE, FvStatus.TIED),
        (4001, MINE, FvStatus.BEHIND),
        (3000, None, FvStatus.NO_POINTS),
        (None, None, FvStatus.NO_POINTS),
    ],
)
def test_status(top, mine, want):
    assert fv_status(top, mine) == want


def test_categories(by_id):
    assert fv_category(by_id["61-10-1-302"], CFG) == "alert"  # 1 rum o kokvrå
    assert fv_category(by_id["41-48-3-103"], CFG) == "alert"  # 1 rum o kök
    assert fv_category(by_id["41-48-1-102"], CFG) == "digest"  # 2 rum o kök
    assert fv_category(replace(by_id["41-48-1-102"], type="Etta med loft"), CFG) == "other"


def test_worth_details_by_public_facts_only(by_id):
    worth = sorted(x.id for x in by_id.values() if fv_worth_details(x, CFG))
    assert worth == ["61-10-1-302", "73-32-1-0102"]  # the two 1-rooms at ≤ 6,000


def test_special_contract_is_never_worth_details_nor_eligible(duvbo):
    x = replace(duvbo, special="Ungdomsbostad")
    assert not fv_worth_details(x, CFG)
    v = fv_classify(x, DET, CFG, MINE)
    assert v.status == FvStatus.WINNABLE and not v.eligible


def test_eligible_winnable(duvbo):
    v = fv_classify(duvbo, DET, CFG, MINE)
    assert (v.status, v.category, v.in_window, v.eligible) == (
        FvStatus.WINNABLE,
        "alert",
        True,
        True,
    )


def test_no_stretch_band(duvbo):
    """All of Förvaltaren is tier 2: 6,001–7,000 never alerts, winnable or not."""
    assert not fv_classify(replace(duvbo, rent=6001), DET, CFG, MINE).eligible
    assert fv_classify(replace(duvbo, rent=6000), DET, CFG, MINE).eligible


@pytest.mark.parametrize("move_in", [date(2027, 3, 31), date(2027, 11, 1)])
def test_move_in_outside_window_not_eligible(duvbo, move_in):
    v = fv_classify(duvbo, replace(DET, move_in=move_in), CFG, MINE)
    assert v.in_window is False and not v.eligible


def test_no_detail_is_pending(duvbo):
    v = fv_classify(duvbo, None, CFG, MINE)
    assert v.status == FvStatus.PENDING and v.in_window is None and not v.eligible


def test_two_rooms_digest_only(by_id):
    v = fv_classify(replace(by_id["41-48-1-102"], rent=5000), DET, CFG, MINE)
    assert v.category == "digest" and not v.eligible


def test_ranking_ground_floor_last_then_rent(duvbo):
    a = replace(duvbo, id="1-1-1-1", rent=5000, floor=0)
    b = replace(duvbo, id="1-1-1-2", rent=5500, floor=2)
    c = replace(duvbo, id="1-1-1-3", rent=4000, floor=1)
    vs = [fv_classify(x, DET, CFG, MINE) for x in (a, b, c)]
    assert [v.listing.id for v in fv_ranked(vs)] == ["1-1-1-3", "1-1-1-2", "1-1-1-1"]
