"""M5 part B: BF tables in the history DB (schema v2), fed from the saved BF fixtures."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from housing_watch.bf import BfDetail, parse_bf_list
from housing_watch.history import (
    SCHEMA_VERSION,
    bf_active,
    bf_detail,
    bf_record,
    bf_seen_since,
    open_db,
    record,
)
from housing_watch.sssb import load_jsonp, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
T1 = T0 + timedelta(minutes=31)
T2 = T1 + timedelta(minutes=31)
CORRIDOR = "202619429"
DET = BfDetail(move_in=date(2027, 1, 1), top_dates=(date(2021, 11, 27), date(2026, 6, 4)))

V1_SCHEMA_TABLES = {"listings", "observations"}


@pytest.fixture(scope="module")
def bf0():
    return parse_bf_list((FIX / "bf" / "2026-11-26-alla-annonser.json").read_text("utf-8"), T0)


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "h.sqlite")
    yield c
    c.close()


def at(xs, when):
    return [replace(x, seen_at=when) for x in xs]


def n(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_all_new(conn, bf0):
    ch = bf_record(conn, bf0, {}, T0)
    assert len(ch.new) == 527 and not ch.changed and not ch.removed
    assert n(conn, "bf_observations") == 527
    assert n(conn, "observations") == 0  # SSSB tables untouched


def test_same_list_again_writes_nothing(tmp_path, bf0):
    path = tmp_path / "h.sqlite"
    c = open_db(path)
    bf_record(c, bf0, {CORRIDOR: DET}, T0)
    c.close()
    before = path.read_bytes()
    c = open_db(path)
    ch = bf_record(c, at(bf0, T1), {}, T1)
    c.close()
    assert ch.unchanged == 527 and path.read_bytes() == before


def test_detail_stored_and_carried_forward(conn, bf0):
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    assert bf_detail(conn, CORRIDOR) == DET
    bf_record(conn, at(bf0, T1), {}, T1)
    assert bf_detail(conn, CORRIDOR) == DET
    assert bf_detail(conn, "202618022") is None  # never fetched


def test_no_block_is_stored_as_fetched_with_no_dates(conn, bf0):
    empty = BfDetail(move_in=date(2027, 1, 1), top_dates=())
    bf_record(conn, bf0, {CORRIDOR: empty}, T0)
    assert bf_detail(conn, CORRIDOR) == empty


def test_new_top_dates_are_a_change(conn, bf0):
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    newer = replace(DET, top_dates=(date(2019, 5, 1), *DET.top_dates))
    ch = bf_record(conn, at(bf0, T1), {CORRIDOR: newer}, T1)
    assert [b.id for _, b in ch.changed] == [CORRIDOR]
    assert bf_detail(conn, CORRIDOR) == newer


def test_changed_rent_detected(conn, bf0):
    bf_record(conn, bf0, {}, T0)
    nxt = at(bf0, T1)
    nxt[0] = replace(nxt[0], rent=(nxt[0].rent or 0) + 100)
    ch = bf_record(conn, nxt, {}, T1)
    assert len(ch.changed) == 1 and ch.unchanged == 526


def test_removed_and_relisted_starts_fresh(conn, bf0):
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    ch = bf_record(conn, at([x for x in bf0 if x.id != CORRIDOR], T1), {}, T1)
    assert ch.removed == [CORRIDOR]
    ch = bf_record(conn, at(bf0, T2), {}, T2)
    assert CORRIDOR in [x.id for x in ch.new]
    assert bf_detail(conn, CORRIDOR) is None


def test_empty_list_removes_nothing(conn, bf0):
    bf_record(conn, bf0, {}, T0)
    ch = bf_record(conn, [], {}, T1)
    assert not ch.removed and len(bf_active(conn)) == 527


def test_active_returns_listing_and_detail(conn, bf0):
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    got = dict((x.id, (x, d)) for x, d in bf_active(conn))
    x, d = got[CORRIDOR]
    assert x == next(b for b in bf0 if b.id == CORRIDOR) and d == DET
    assert got["202618022"][1] is None


def test_seen_since_includes_removed(conn, bf0):
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    bf_record(conn, at([x for x in bf0 if x.id != CORRIDOR], T1), {}, T1)
    rows = {x.id: (d, removed) for x, d, removed in bf_seen_since(conn, T0)}
    assert rows[CORRIDOR] == (DET, T1)
    assert len(rows) == 527


def test_guards(conn, bf0):
    with pytest.raises(ValueError):
        bf_record(conn, bf0, {}, T1)  # seen_at mismatch
    with pytest.raises(ValueError):
        bf_record(conn, bf0, {"999": DET}, T0)
    with pytest.raises(ValueError):
        bf_record(conn, [bf0[0], bf0[0]], {}, T0)


def test_v1_db_migrates_in_place_and_keeps_sssb_rows(tmp_path):
    """A live v1 history.sqlite (data branch) gains the BF tables; SSSB rows stay identical."""
    path = tmp_path / "v1.sqlite"
    sssb = parse_list(load_jsonp((FIX / "2026-11-26-list.jsonp").read_text("utf-8")), T0)
    c = open_db(path)
    record(c, sssb, {}, T0)
    c.execute("DROP TABLE hs_observations")
    c.execute("DROP TABLE hs_listings")
    c.execute("DROP TABLE sbs_observations")
    c.execute("DROP TABLE sbs_listings")
    c.execute("DROP TABLE fv_observations")
    c.execute("DROP TABLE fv_listings")
    c.execute("DROP TABLE bf_observations")
    c.execute("DROP TABLE bf_listings")
    c.execute("PRAGMA user_version = 1")
    c.commit()
    rows = c.execute("SELECT * FROM observations ORDER BY id").fetchall()
    c.close()
    raw = sqlite3.connect(path)
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert tables == V1_SCHEMA_TABLES

    c = open_db(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [tuple(r) for r in c.execute("SELECT * FROM observations ORDER BY id")] == [
        tuple(r) for r in rows
    ]
    assert n(c, "bf_listings") == 0
    c.close()


def test_removed_listing_has_no_current_detail(conn, bf0):
    """Review: a re-listed listing must not be picked by its old listing's detail."""
    bf_record(conn, bf0, {CORRIDOR: DET}, T0)
    bf_record(conn, at([x for x in bf0 if x.id != CORRIDOR], T1), {}, T1)
    assert bf_detail(conn, CORRIDOR) is None
