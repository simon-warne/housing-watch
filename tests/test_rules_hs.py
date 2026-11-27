"""M6: Heimstaden classification (digest only: watched rooms, rent, move-in window)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from housing_watch.config import load_config
from housing_watch.heimstaden import HsListing
from housing_watch.rules import hs_classify

CFG = load_config(Path(__file__).parent.parent / "config.yaml")  # window 2027-04-01..10-31
SEEN = datetime(2026, 11, 26, 21, 5, tzinfo=UTC)


def x(**kw) -> HsListing:
    base = dict(
        id="6933111-1005",
        location="Täby - Täby Kyrkby",
        address="Hagmovägen 1",
        rooms="1 rum och kokvrå",
        rent=5080,
        size_m2=36,
        move_in=date(2027, 5, 1),
        restricted=False,
        labels=(),
        seen_at=SEEN,
    )
    return HsListing(**{**base, **kw})


def test_watched_in_rent_in_window():
    v = hs_classify(x(), CFG)
    assert (v.watched, v.over_budget, v.window) == (True, False, "in")


@pytest.mark.parametrize(
    ("rooms", "watched"),
    [
        ("1 rum och kök", True),
        ("1 rum och kokvrå", True),
        ("1 Rum och kök", True),
        ("1,5 rum och kök", False),
        ("2 rum och kök", False),
    ],
)
def test_watched_rooms(rooms, watched):
    assert hs_classify(x(rooms=rooms), CFG).watched is watched


@pytest.mark.parametrize(("rent", "over"), [(6000, False), (6001, True), (6114, True)])
def test_rent_no_stretch(rent, over):
    assert hs_classify(x(rent=rent), CFG).over_budget is over


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
def test_window(move_in, window):
    assert hs_classify(x(move_in=move_in), CFG).window == window
