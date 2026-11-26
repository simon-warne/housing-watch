"""M6: SBS tables in the history DB (schema v4), fed from the saved SBS fixtures."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from housing_watch.bf import parse_bf_list
from housing_watch.fv import parse_fv_list
from housing_watch.history import (
    SCHEMA_VERSION,
    bf_record,
    fv_record,
    open_db,
    record,
    sbs_active,
    sbs_record,
    sbs_seen_since,
)
from housing_watch.sbs import parse_sbs_page
from housing_watch.sssb import load_jsonp, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 22, 25, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)
T2 = T1 + timedelta(hours=6)
NEK = "nekvagen-23-lgh-1403"


@pytest.fixture(scope="module")
def sbs0():
    out = []
    for name in ("2026-11-26-lediga-stockholm.html", "2026-11-26-lediga-stockholm-page-2.html"):
        out += parse_sbs_page((FIX / "sbs" / name).read_text("utf-8"), T0).listings
    return out


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "h.sqlite")
    yield c
    c.close()


def at(xs, when):
    return [replace(x, seen_at=when) for x in xs]


def n(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_all_new(conn, sbs0):
    ch = sbs_record(conn, sbs0, T0)
    assert len(ch.new) == 16 and not ch.changed and not ch.removed
    assert n(conn, "sbs_observations") == 16
    for t in ("observations", "bf_observations", "fv_observations"):
        assert n(conn, t) == 0


def test_round_trip_keeps_every_field(conn, sbs0):
    sbs_record(conn, sbs0, T0)
    assert sorted(sbs_active(conn), key=lambda x: x.id) == sorted(sbs0, key=lambda x: x.id)


def test_same_list_again_writes_nothing(tmp_path, sbs0):
    path = tmp_path / "h.sqlite"
    c = open_db(path)
    sbs_record(c, sbs0, T0)
    c.close()
    before = path.read_bytes()
    c = open_db(path)
    ch = sbs_record(c, at(sbs0, T1), T1)
    c.close()
    assert ch.unchanged == 16 and not ch.new and not ch.changed
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"rent": 6500},
        {"move_in": date(2027, 4, 1)},
        {"move_in": None},
        {"labels": ("Kampanj",)},
        {"size_m2": None},
    ],
)
def test_a_changed_field_is_a_new_row(conn, sbs0, change):
    sbs_record(conn, sbs0, T0)
    xs = [replace(x, **change) if x.id == NEK else x for x in at(sbs0, T1)]
    ch = sbs_record(conn, xs, T1)
    assert [new.id for _, new in ch.changed] == [NEK]
    assert n(conn, "sbs_observations") == 17
    got = {x.id: x for x in sbs_active(conn)}[NEK]
    assert got == replace({x.id: x for x in sbs0}[NEK], seen_at=T1, **change)


def test_removed_and_relisted(conn, sbs0):
    sbs_record(conn, sbs0, T0)
    ch = sbs_record(conn, at([x for x in sbs0 if x.id != NEK], T1), T1)
    assert ch.removed == [NEK]
    assert NEK not in {x.id for x in sbs_active(conn)}
    ch = sbs_record(conn, at(sbs0, T2), T2)
    assert [x.id for x in ch.new] == [NEK]


def test_empty_list_removes_nothing(conn, sbs0):
    sbs_record(conn, sbs0, T0)
    ch = sbs_record(conn, [], T1)
    assert not ch.removed and len(sbs_active(conn)) == 16


def test_seen_since_includes_removed(conn, sbs0):
    sbs_record(conn, sbs0, T0)
    sbs_record(conn, at([x for x in sbs0 if x.id != NEK], T1), T1)
    rows = {x.id: removed for x, removed in sbs_seen_since(conn, T0)}
    assert rows[NEK] == T1 and rows["nekvagen-35-41-2"] is None
    assert NEK not in {x.id for x, _ in sbs_seen_since(conn, T1 + timedelta(seconds=1))}


def test_guards(conn, sbs0):
    with pytest.raises(ValueError):
        sbs_record(conn, sbs0 + sbs0[:1], T0)
    with pytest.raises(ValueError):
        sbs_record(conn, sbs0, T1)  # seen_at differs


def test_v3_db_migrates_in_place_and_keeps_other_rows(tmp_path):
    """A live v3 history.sqlite (data branch) gains the SBS tables; other rows stay identical."""
    path = tmp_path / "v3.sqlite"
    sssb = parse_list(load_jsonp((FIX / "2026-11-26-list.jsonp").read_text("utf-8")), T0)
    bf = parse_bf_list((FIX / "bf" / "2026-11-26-alla-annonser.json").read_text("utf-8"), T0)
    fv = parse_fv_list(
        (FIX / "forvaltaren" / "2026-11-26-objects-lagenhet.json").read_text("utf-8"), T0
    )
    c = open_db(path)
    record(c, sssb, {}, T0)
    bf_record(c, bf, {}, T0)
    fv_record(c, fv, {}, T0)
    c.execute("DROP TABLE sbs_observations")
    c.execute("DROP TABLE sbs_listings")
    c.execute("PRAGMA user_version = 3")
    c.commit()
    tables = ("observations", "bf_observations", "fv_observations", "listings", "bf_listings")
    dump = {
        t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
        for t in (*tables, "fv_listings")
    }
    c.close()

    c = open_db(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    for t, rows in dump.items():
        assert [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] == rows
    assert n(c, "sbs_listings") == 0
    c.close()
    raw = sqlite3.connect(path)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert {"sbs_listings", "sbs_observations"} <= names
