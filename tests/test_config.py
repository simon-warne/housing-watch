"""M3 config tests: the committed config.yaml, strict loading, queue days from env."""

from datetime import date, time, timedelta
from pathlib import Path

import pytest
import yaml

from housing_watch.config import (
    ConfigError,
    FvPoints,
    load_bf_since,
    load_booked,
    load_config,
    load_fv_points,
    load_queue_days,
)

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
    assert cfg.move_in_from == date(2027, 4, 1) and cfg.move_in_to == date(2027, 10, 31)
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


# --- M5 part B: Bostadsförmedlingen ---
def test_real_config_bf_section():
    bf = load_config(REAL).bf
    assert bf.types == (
        "Studentlägenhet",
        "Studentkorridor",
        "Ungdomslägenhet",
        "UngdomKorttid",
        "Korttidskontrakt",
    )
    assert bf.bostad_snabbt is True
    assert bf.municipalities == ("Stockholm", "Solna", "Sundbyberg")
    assert bf.alert_max_rooms == 1.5
    assert bf.tier("Stockholm", "Södermalm") == 0
    assert bf.tier("Stockholm", "gamla stan") == 0
    assert bf.tier("Stockholm", "Kista") == 1
    assert bf.tier("Solna", "Hagalund") == 1
    assert bf.tier("Huddinge", "Flemingsberg") is None
    assert bf.tier("Nacka", "Södermalm") is None  # a tier-1 name outside scope stays out


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.pop("bf"),
        lambda d: d["bf"].pop("tier1"),
        lambda d: d["bf"].update(types=[]),
        lambda d: d["bf"].update(municipalities=[]),
        lambda d: d["bf"].update(bostad_snabbt="yes"),
        lambda d: d["bf"].update(alert_max_rooms=0),
        lambda d: d["bf"].update(extra=1),
        lambda d: d["bf"].update(tier1=["Södermalm", "södermalm"]),
    ],
)
def test_bad_bf_section_raises(tmp_path, edit):
    data = real_data()
    edit(data)
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_bf_queue_since():
    assert load_bf_since({}) is None
    assert load_bf_since({"BF_QUEUE_SINCE": " "}) is None
    assert load_bf_since({"BF_QUEUE_SINCE": "2026-11-20"}) == date(2026, 11, 20)


@pytest.mark.parametrize("raw", ["20261120", "2026-13-01", "soon", "2026-11-20@x"])
def test_bad_bf_queue_since_raises_without_echo(raw):
    with pytest.raises(ConfigError) as e:
        load_bf_since({"BF_QUEUE_SINCE": raw})
    assert raw not in str(e.value)


# --- M6: Förvaltaren ---
def test_real_config_fv_section():
    fv = load_config(REAL).fv
    assert fv.alert_types == ("1 rum o kokvrå", "1 rum o kök")
    assert fv.digest_types == ("2 rum o kök", "3 rum o kök", "4 rum o kök")


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.pop("fv"),
        lambda d: d["fv"].pop("types"),
        lambda d: d["fv"]["types"].update(alert=[]),
        lambda d: d["fv"]["types"].update(digest=["1 rum o kök"]),  # both lists
        lambda d: d["fv"].update(extra=1),
    ],
)
def test_bad_fv_section_raises(tmp_path, edit):
    data = real_data()
    edit(data)
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


TODAY = date(2026, 11, 26)


def test_fv_points_grow_one_a_day():
    p = load_fv_points({"FV_POINTS": "1200", "FV_POINTS_ON": "2026-11-20"}, TODAY)
    assert p == FvPoints(base=1200, base_date=date(2026, 11, 20))
    assert p.on(date(2026, 11, 20)) == 1200
    assert p.on(TODAY) == 1206


def test_fv_points_unset_is_none():
    assert load_fv_points({}, TODAY) is None
    assert load_fv_points({"FV_POINTS": " ", "FV_POINTS_ON": ""}, TODAY) is None


@pytest.mark.parametrize(
    "env",
    [
        {"FV_POINTS": "1200"},  # no date
        {"FV_POINTS_ON": "2026-11-20"},  # no points
        {"FV_POINTS": "12.5", "FV_POINTS_ON": "2026-11-20"},
        {"FV_POINTS": "-3", "FV_POINTS_ON": "2026-11-20"},
        {"FV_POINTS": "1200000", "FV_POINTS_ON": "2026-11-20"},
        {"FV_POINTS": "1200", "FV_POINTS_ON": "20261120"},
        {"FV_POINTS": "1200", "FV_POINTS_ON": "2026-02-30"},
        {"FV_POINTS": "1200", "FV_POINTS_ON": "2026-11-27"},  # in the future
    ],
)
def test_bad_fv_points_raise_without_echo(env):
    with pytest.raises(ConfigError) as e:
        load_fv_points(env, TODAY)
    for v in env.values():
        assert v not in str(e.value)


# --- M6: SBS ---
def test_real_config_sbs_section():
    sbs = load_config(REAL).sbs
    assert sbs.rooms == ("1 rum", "Korridor")
    assert sbs.every == timedelta(hours=6)
    assert sbs.max_pages == 5


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.pop("sbs"),
        lambda d: d["sbs"].pop("rooms"),
        lambda d: d["sbs"].update(rooms=[]),
        lambda d: d["sbs"].update(every_hours=0),
        lambda d: d["sbs"].update(every_hours=0.25),  # never more often than every 30 min
        lambda d: d["sbs"].update(every_hours=25),
        lambda d: d["sbs"].update(every_hours=True),
        lambda d: d["sbs"].update(max_pages=0),
        lambda d: d["sbs"].update(max_pages=21),
        lambda d: d["sbs"].update(extra=1),
    ],
)
def test_bad_sbs_section_raises(tmp_path, edit):
    data = real_data()
    edit(data)
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))
