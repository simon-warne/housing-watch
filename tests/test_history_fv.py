"""M6: Förvaltaren tables in the history DB (schema v3), fed from the saved FV fixture."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from housing_watch.bf import parse_bf_list
from housing_watch.fv import FvDetail, parse_fv_list
from housing_watch.history import (
    SCHEMA_VERSION,
    bf_record,
    fv_active,
    fv_detail,
    fv_record,
    fv_seen_since,
    open_db,
    record,
)
from housing_watch.sssb import load_jsonp, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
T1 = T0 + timedelta(minutes=31)
T2 = T1 + timedelta(minutes=31)
RISSNE = "73-32-1-0102"
DET = FvDetail(move_in=date(2027, 5, 1), top_points=5022, applicants=40)


@pytest.fixture(scope="module")
def fv0():
    body = (FIX / "forvaltaren" / "2026-11-26-objects-lagenhet.json").read_text("utf-8")
    return parse_fv_list(body, T0)


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "h.sqlite")
    yield c
    c.close()


def at(xs, when):
    return [replace(x, seen_at=when) for x in xs]


def n(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_all_new(conn, fv0):
    ch = fv_record(conn, fv0, {}, T0)
    assert len(ch.new) == 17 and not ch.changed and not ch.removed
    assert n(conn, "fv_observations") == 17
    assert n(conn, "observations") == 0 and n(conn, "bf_observations") == 0


def test_same_list_again_writes_nothing(tmp_path, fv0):
    path = tmp_path / "h.sqlite"
    c = open_db(path)
    fv_record(c, fv0, {RISSNE: DET}, T0)
    c.close()
    before = path.read_bytes()
    c = open_db(path)
    ch = fv_record(c, at(fv0, T1), {RISSNE: DET}, T1)
    c.close()
    assert ch.unchanged == 17 and not ch.new and not ch.changed
    assert path.read_bytes() == before


def test_detail_stored_and_carried_forward(conn, fv0):
    fv_record(conn, fv0, {RISSNE: DET}, T0)
    fv_record(conn, at(fv0, T1), {}, T1)
    assert fv_detail(conn, RISSNE) == DET
    assert n(conn, "fv_observations") == 17


def test_detail_without_top_row_is_stored_as_fetched(conn, fv0):
    d = FvDetail(move_in=date(2027, 5, 1), top_points=None, applicants=None)
    fv_record(conn, fv0, {RISSNE: d}, T0)
    assert fv_detail(conn, RISSNE) == d


def test_new_top_points_are_a_change(conn, fv0):
    fv_record(conn, fv0, {RISSNE: DET}, T0)
    ch = fv_record(conn, at(fv0, T1), {RISSNE: replace(DET, top_points=5100)}, T1)
    assert [new.id for _, new in ch.changed] == [RISSNE]
    assert fv_detail(conn, RISSNE).top_points == 5100


def test_more_applicants_in_the_list_are_a_change(conn, fv0):
    fv_record(conn, fv0, {}, T0)
    xs = [replace(x, applicants=x.applicants + 1) if x.id == RISSNE else x for x in at(fv0, T1)]
    ch = fv_record(conn, xs, {}, T1)
    assert [new.applicants for _, new in ch.changed] == [41]


def test_removed_and_relisted_starts_fresh(conn, fv0):
    fv_record(conn, fv0, {RISSNE: DET}, T0)
    ch = fv_record(conn, at([x for x in fv0 if x.id != RISSNE], T1), {}, T1)
    assert ch.removed == [RISSNE]
    assert fv_detail(conn, RISSNE) is None
    ch = fv_record(conn, at(fv0, T2), {}, T2)
    assert [x.id for x in ch.new] == [RISSNE]
    assert fv_detail(conn, RISSNE) is None


def test_empty_list_removes_nothing(conn, fv0):
    fv_record(conn, fv0, {}, T0)
    ch = fv_record(conn, [], {}, T1)
    assert not ch.removed and len(fv_active(conn)) == 17


def test_active_returns_listing_and_detail(conn, fv0):
    fv_record(conn, fv0, {RISSNE: DET}, T0)
    got = dict((x.id, (x, d)) for x, d in fv_active(conn))
    assert got[RISSNE] == (fv0[[x.id for x in fv0].index(RISSNE)], DET)
    assert got["61-10-1-302"][1] is None


def test_seen_since_includes_removed(conn, fv0):
    fv_record(conn, fv0, {}, T0)
    fv_record(conn, at([x for x in fv0 if x.id != RISSNE], T1), {}, T1)
    rows = {x.id: removed for x, _, removed in fv_seen_since(conn, T0)}
    assert rows[RISSNE] == T1 and rows["61-10-1-302"] is None


def test_guards(conn, fv0):
    with pytest.raises(ValueError):
        fv_record(conn, fv0 + fv0[:1], {}, T0)
    with pytest.raises(ValueError):
        fv_record(conn, fv0, {}, T1)  # seen_at differs
    with pytest.raises(ValueError):
        fv_record(conn, fv0, {"1-2-3-4": DET}, T0)


def test_v2_db_migrates_in_place_and_keeps_sssb_and_bf_rows(tmp_path):
    """A live v2 history.sqlite (data branch) gains the FV tables; other rows stay identical."""
    path = tmp_path / "v2.sqlite"
    sssb = parse_list(load_jsonp((FIX / "2026-11-26-list.jsonp").read_text("utf-8")), T0)
    bf = parse_bf_list((FIX / "bf" / "2026-11-26-alla-annonser.json").read_text("utf-8"), T0)
    c = open_db(path)
    record(c, sssb, {}, T0)
    bf_record(c, bf, {}, T0)
    c.execute("DROP TABLE fv_observations")
    c.execute("DROP TABLE fv_listings")
    c.execute("PRAGMA user_version = 2")
    c.commit()
    dump = {
        t: c.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
        for t in ("observations", "bf_observations", "listings", "bf_listings")
    }
    dump = {t: [tuple(r) for r in rows] for t, rows in dump.items()}
    c.close()

    c = open_db(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    for t, rows in dump.items():
        assert [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] == rows
    assert n(c, "fv_listings") == 0
    c.close()
    raw = sqlite3.connect(path)
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert {"fv_listings", "fv_observations"} <= tables
