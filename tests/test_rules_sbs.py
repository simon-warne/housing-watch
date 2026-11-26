"""M6: SBS classification (digest only: watched rooms, rent, move-in window, short-term)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.config import load_config
from housing_watch.rules import sbs_classify
from housing_watch.sbs import SbsListing

CFG = load_config(Path(__file__).parent.parent / "config.yaml")  # window 2027-04-01..10-31
SEEN = datetime(2026, 11, 26, 22, 25, tzinfo=UTC)


def x(**kw) -> SbsListing:
    base = dict(
        id="nekvagen-23-lgh-1403",
        tag="Studentbostad",
        city="Stockholm",
        address="Nekvägen 23 lgh 1403",
        move_in=date(2027, 5, 1),
        rent=5900,
        size_m2=21,
        rooms="1 rum",
        labels=(),
        seen_at=SEEN,
    )
    return SbsListing(**{**base, **kw})


def test_watched_in_rent_in_window():
    v = sbs_classify(x(), CFG)
    assert (v.watched, v.over_budget, v.short_term, v.window) == (True, False, False, "in")


@pytest.mark.parametrize(
    ("rooms", "watched"),
    [("1 rum", True), ("Korridor", True), ("korridor", True), ("2 rum", False)],
)
def test_watched_rooms(rooms, watched):
    assert sbs_classify(x(rooms=rooms), CFG).watched is watched


@pytest.mark.parametrize(("rent", "over"), [(6000, False), (6001, True), (6777, True)])
def test_rent_has_no_stretch(rent, over):
    assert sbs_classify(x(rent=rent), CFG).over_budget is over


@pytest.mark.parametrize(
    ("move_in", "window"),
    [
        (date(2027, 3, 31), "early"),
        (date(2027, 4, 1), "in"),
        (date(2027, 10, 31), "in"),
        (date(2027, 11, 1), "late"),
        (None, "agreement"),
    ],
)
def test_move_in_window(move_in, window):
    assert sbs_classify(x(move_in=move_in), CFG).window == window


@pytest.mark.parametrize(
    ("tag", "short"),
    [
        ("Korttidkontrakt – Student", True),
        ("Korttidskontrakt", True),
        ("Studentbostad", False),
        ("Student & Ungdom", False),
    ],
)
def test_short_term_tag(tag, short):
    assert sbs_classify(x(tag=tag), CFG).short_term is short
