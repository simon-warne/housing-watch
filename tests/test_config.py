"""M3 config tests: the committed config.yaml, strict loading, queue days from env."""

from datetime import date, time
from pathlib import Path

import pytest
import yaml

from housing_watch.config import ConfigError, load_booked, load_config, load_queue_days

ROOT = Path(__file__).parent.parent
REAL = ROOT / "config.yaml"
# Made-up values (the real ones live in GitHub secrets).
ENV = {"QUEUE_DAYS": "200@2026-11-01", "QUEUE_DAYS_UNTIL": "2027-01-31"}


def write(tmp_path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def real_data() -> dict:
    return yaml.safe_load(REAL.read_text(encoding="utf-8"))


def test_real_config_loads():
    cfg = load_config(REAL)
    assert cfg.alert_types == ("Rum i korridor", "1 rum & pentry", "1 rum & kök")
    assert "2 rum & kök" in cfg.digest_types
    assert cfg.rent_max == 6000 and cfg.rent_stretch == 7000
    assert cfg.move_in_from == date(2027, 6, 1) and cfg.move_in_to == date(2027, 10, 31)
    assert cfg.last_call_hours == 5
    assert cfg.digest_weekday == 6 and cfg.digest_time == time(18, 0)
    assert cfg.tier("Idun") == 0 and cfg.tier("Forum") == 1 and cfg.tier("Lappkärrsberget") == 5
    assert cfg.tier("Somewhere new") == 6
    assert cfg.is_stretch("Roslagstull") and not cfg.is_stretch("Strix")


def test_area_match_ignores_case(tmp_path):
    cfg = load_config(REAL)
    assert cfg.tier("FORUM") == 1 and cfg.is_stretch("forum")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d["rent"].update(extra=1),
        lambda d: d.pop("rent"),
        lambda d: d["rent"].update(max=8000),  # max > stretch
        lambda d: d["rent"].update(max="6000"),
        lambda d: d["move_in"].update({"from": "2027-13-01"}),
        lambda d: d["move_in"].update({"to": date(2027, 1, 1)}),  # to < from
        lambda d: d.update(last_call_hours=0),
        lambda d: d["digest"].update(weekday="sundy"),
        lambda d: d["digest"].update(time="25:00"),
        lambda d: d["types"].update(alert=[]),
        lambda d: d["types"].update(digest=["Rum i korridor"]),  # both lists
        lambda d: d.update(stretch_areas=["Nowhere"]),  # not in any tier
        lambda d: d.update(area_tiers=[["Forum"], ["forum"]]),  # duplicate
    ],
)
def test_bad_config_raises(tmp_path, mutate):
    data = real_data()
    mutate(data)
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_queue_days_grow_daily_and_stop_at_until():
    q = load_queue_days(ENV)
    assert q.on(date(2026, 11, 1)) == 200
    assert q.on(date(2026, 11, 2)) == 201
    assert q.on(date(2027, 1, 31)) == 200 + 91
    assert q.on(date(2027, 3, 1)) == 200 + 91  # membership over: no more days


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"QUEUE_DAYS": "200@2026-11-01"},
        {"QUEUE_DAYS": "200", "QUEUE_DAYS_UNTIL": "2027-01-31"},
        {"QUEUE_DAYS": "-5@2026-11-01", "QUEUE_DAYS_UNTIL": "2027-01-31"},
        {"QUEUE_DAYS": "200@2026-11-31", "QUEUE_DAYS_UNTIL": "2027-01-31"},
        {"QUEUE_DAYS": "200@2026-11-01", "QUEUE_DAYS_UNTIL": "2026-10-01"},
    ],
)
def test_bad_queue_days_raise(env):
    with pytest.raises(ConfigError):
        load_queue_days(env)


def test_queue_days_error_does_not_echo_value():
    with pytest.raises(ConfigError) as e:
        load_queue_days({"QUEUE_DAYS": "987654@x", "QUEUE_DAYS_UNTIL": "2027-01-31"})
    assert "987654" not in str(e.value)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (None, frozenset()),
        ("", frozenset()),
        ("5102-3203-028", frozenset({"5102-3203-028"})),
        (" 5102-3203-028 , 2118-0403-042,", frozenset({"5102-3203-028", "2118-0403-042"})),
    ],
)
def test_booked_ids(raw, want):
    env = {} if raw is None else {"BOOKED_IDS": raw}
    assert load_booked(env) == want


def test_booked_ids_reject_junk():
    with pytest.raises(ConfigError):
        load_booked({"BOOKED_IDS": "5102-3203-028; rm -rf"})
