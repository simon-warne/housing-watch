"""M6: Heimstaden tables in the history DB (schema v5), fed from the saved Heimstaden page."""

import gzip
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from housing_watch.bf import parse_bf_list
from housing_watch.fv import parse_fv_list
from housing_watch.heimstaden import parse_hs_page
from housing_watch.history import (
    SCHEMA_VERSION,
    bf_record,
    fv_record,
    hs_active,
    hs_record,
    hs_seen_since,
    open_db,
    record,
    sbs_record,
)
from housing_watch.sbs import parse_sbs_page
from housing_watch.sssb import load_jsonp, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 21, 5, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)
T2 = T1 + timedelta(hours=6)
BAND = "6937101-1210"
TABY = "6933111-1005"


def hs_page() -> str:
    return gzip.decompress(
        (FIX / "heimstaden" / "2026-11-26-sok-lagenhet.html.gz").read_bytes()
    ).decode()


@pytest.fixture(scope="module")
def hs0():
    return parse_hs_page(hs_page(), T0, "Stor-Stockholm")


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "h.sqlite")
    yield c
    c.close()


def at(xs, when):
    return [replace(x, seen_at=when) for x in xs]


def n(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_all_new(conn, hs0):
    ch = hs_record(conn, hs0, T0)
    assert len(ch.new) == 20 and not ch.changed and not ch.removed
    assert n(conn, "hs_observations") == 20
    for t in ("observations", "bf_observations", "fv_observations", "sbs_observations"):
        assert n(conn, t) == 0


def test_round_trip_keeps_every_field(conn, hs0):
    hs_record(conn, hs0, T0)
    assert sorted(hs_active(conn), key=lambda x: x.id) == sorted(hs0, key=lambda x: x.id)


def test_same_list_again_writes_nothing(tmp_path, hs0):
    path = tmp_path / "h.sqlite"
    c = open_db(path)
    hs_record(c, hs0, T0)
    c.close()
    before = path.read_bytes()
    c = open_db(path)
    ch = hs_record(c, at(hs0, T1), T1)
    c.close()
    assert ch.unchanged == 20 and not ch.new and not ch.changed
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"rent": 5900},
        {"move_in": date(2027, 4, 1)},
        {"move_in": None},
        {"labels": ("Nyproduktion",)},
        {"restricted": True},
        {"rooms": "1 rum och kokvrå"},
        {"location": "Stockholm - Högdalen"},
        {"size_m2": 34},
    ],
)
def test_a_changed_field_is_a_new_row(conn, hs0, change):
    hs_record(conn, hs0, T0)
    xs = [replace(x, **change) if x.id == BAND else x for x in at(hs0, T1)]
    ch = hs_record(conn, xs, T1)
    assert [new.id for _, new in ch.changed] == [BAND]
    assert n(conn, "hs_observations") == 21
    got = {x.id: x for x in hs_active(conn)}[BAND]
    assert got == replace({x.id: x for x in hs0}[BAND], seen_at=T1, **change)


def test_removed_and_relisted(conn, hs0):
    hs_record(conn, hs0, T0)
    ch = hs_record(conn, at([x for x in hs0 if x.id != BAND], T1), T1)
    assert ch.removed == [BAND]
    ch = hs_record(conn, at(hs0, T2), T2)
    assert [x.id for x in ch.new] == [BAND]
    first = {x.id: f for x, f, _ in hs_seen_since(conn, T0)}
    assert first[BAND] == T2 and first[TABY] == T0


def test_an_empty_district_removes_everything(conn, hs0):
    # The page always covers all of Sweden, so a valid page with none here is real.
    hs_record(conn, hs0, T0)
    ch = hs_record(conn, [], T1)
    assert len(ch.removed) == 20 and hs_active(conn) == []
    assert n(conn, "hs_observations") == 20  # history kept


def test_seen_since_includes_removed(conn, hs0):
    hs_record(conn, hs0, T0)
    hs_record(conn, at([x for x in hs0 if x.id != BAND], T1), T1)
    rows = {x.id: (first, removed) for x, first, removed in hs_seen_since(conn, T0)}
    assert rows[BAND] == (T0, T1) and rows[TABY] == (T0, None)
    assert BAND not in {x.id for x, _, _ in hs_seen_since(conn, T1 + timedelta(seconds=1))}


def test_guards(conn, hs0):
    with pytest.raises(ValueError):
        hs_record(conn, hs0 + hs0[:1], T0)
    with pytest.raises(ValueError):
        hs_record(conn, hs0, T1)  # seen_at differs


def test_v4_db_migrates_in_place_and_keeps_other_rows(tmp_path):
    """A live v4 history.sqlite (data branch) gains the Heimstaden tables; other rows stay."""
    path = tmp_path / "v4.sqlite"
    sssb = parse_list(load_jsonp((FIX / "2026-11-26-list.jsonp").read_text("utf-8")), T0)
    bf = parse_bf_list((FIX / "bf" / "2026-11-26-alla-annonser.json").read_text("utf-8"), T0)
    fv = parse_fv_list(
        (FIX / "forvaltaren" / "2026-11-26-objects-lagenhet.json").read_text("utf-8"), T0
    )
    sbs = parse_sbs_page(
        (FIX / "sbs" / "2026-11-26-lediga-stockholm.html").read_text("utf-8"), T0
    ).listings
    c = open_db(path)
    record(c, sssb, {}, T0)
    bf_record(c, bf, {}, T0)
    fv_record(c, fv, {}, T0)
    sbs_record(c, sbs, T0)
    c.execute("DROP TABLE hs_observations")
    c.execute("DROP TABLE hs_listings")
    c.execute("PRAGMA user_version = 4")
    c.commit()
    tables = (
        "observations",
        "listings",
        "bf_observations",
        "bf_listings",
        "fv_observations",
        "fv_listings",
        "sbs_observations",
    )
    dump = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] for t in tables}
    sbs_l = [tuple(r) for r in c.execute("SELECT * FROM sbs_listings ORDER BY id")]
    c.close()

    c = open_db(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 5
    for t, rows in dump.items():
        assert [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] == rows
    assert [tuple(r) for r in c.execute("SELECT * FROM sbs_listings ORDER BY id")] == sbs_l
    assert n(c, "hs_listings") == 0
    c.close()
    raw = sqlite3.connect(path)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert {"hs_listings", "hs_observations"} <= names
