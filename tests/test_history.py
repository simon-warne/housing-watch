"""M2 history tests: SQLite DB in tmp_path, fed from the saved SSSB fixtures."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.history import SCHEMA_VERSION, Changes, deadline, latest, open_db, record
from housing_watch.sssb import Detail, load_jsonp, parse_detail, parse_list

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)
T1 = T0 + timedelta(minutes=31)
T2 = T1 + timedelta(minutes=31)
STHLM = ZoneInfo("Europe/Stockholm")
DETAIL_ID = "5102-3203-028"


def load(name: str) -> dict:
    return load_jsonp((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def listings():
    return parse_list(load("2026-11-26-list.jsonp"), T0)


@pytest.fixture
def detail() -> Detail:
    return parse_detail(load("2026-11-26-detail-5102-3203-028.jsonp"), DETAIL_ID)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "history.sqlite"
    conn = open_db(path)
    yield path, conn
    conn.close()


def at(listings, when):
    return [replace(x, seen_at=when) for x in listings]


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def deadline_of(conn, id_: str):
    return conn.execute(
        "SELECT deadline FROM observations WHERE listing_id = ? ORDER BY id DESC LIMIT 1", (id_,)
    ).fetchone()[0]


# 1
def test_first_run_all_new(db, listings):
    _, conn = db
    ch = record(conn, listings, {}, T0)
    assert len(ch.new) == 24 and ch.changed == [] and ch.removed == [] and ch.unchanged == 0
    assert count(conn, "listings") == 24
    assert count(conn, "observations") == 24


# 2
def test_same_page_again_changes_nothing(db, listings, detail):
    path, conn = db
    record(conn, listings, {DETAIL_ID: detail}, T0)
    before = path.read_bytes()
    ch = record(conn, at(listings, T1), {DETAIL_ID: detail}, T1)
    assert ch == Changes(new=[], changed=[], removed=[], unchanged=24)
    assert count(conn, "listings") == 24 and count(conn, "observations") == 24
    assert path.read_bytes() == before


# 3
@pytest.mark.parametrize(
    "change",
    [
        {"top_days": 999},
        {"applicants": 42},
        {"rent": 1234},
        {"flags": ("Ny flagga",)},
    ],
)
def test_changed_field_detected(db, listings, change):
    _, conn = db
    record(conn, listings, {}, T0)
    nxt = at(listings, T1)
    nxt[3] = replace(nxt[3], **change)
    ch = record(conn, nxt, {}, T1)
    assert ch.new == [] and ch.removed == [] and ch.unchanged == 23
    [(old, new)] = ch.changed
    assert old.id == new.id == listings[3].id
    for k, v in change.items():
        assert getattr(new, k) == v
        assert getattr(old, k) == getattr(listings[3], k)
    assert old.seen_at == T0 and new.seen_at == T1
    assert count(conn, "observations") == 25


# 4
def test_removed_once(db, listings):
    _, conn = db
    record(conn, listings, {}, T0)
    gone = listings[0].id
    ch = record(conn, at(listings[1:], T1), {}, T1)
    assert ch.removed == [gone] and ch.new == [] and ch.changed == [] and ch.unchanged == 23
    row = conn.execute("SELECT removed_at FROM listings WHERE id = ?", (gone,)).fetchone()
    assert datetime.fromisoformat(row[0]) == T1
    ch = record(conn, at(listings[1:], T2), {}, T2)
    assert ch.removed == [] and ch.unchanged == 23
    row = conn.execute("SELECT removed_at FROM listings WHERE id = ?", (gone,)).fetchone()
    assert datetime.fromisoformat(row[0]) == T1


# 5
def test_reappearing_listing_is_new(db, listings):
    _, conn = db
    record(conn, listings, {}, T0)
    record(conn, at(listings[1:], T1), {}, T1)
    ch = record(conn, at(listings, T2), {}, T2)
    assert [x.id for x in ch.new] == [listings[0].id] and ch.unchanged == 23
    row = conn.execute("SELECT removed_at FROM listings WHERE id = ?", (listings[0].id,)).fetchone()
    assert row[0] is None
    assert count(conn, "listings") == 24


# 6
def test_deadline_join_and_carry_forward(db, listings, detail):
    _, conn = db
    record(conn, listings, {DETAIL_ID: detail}, T0)
    stored = datetime.fromisoformat(deadline_of(conn, DETAIL_ID))
    assert stored == datetime(2026, 11, 30, 16, 0, tzinfo=STHLM)

    ch = record(conn, at(listings, T1), {}, T1)  # detail not fetched this run
    assert ch.changed == [] and ch.unchanged == 24
    assert datetime.fromisoformat(deadline_of(conn, DETAIL_ID)) == stored

    later = replace(detail, deadline=detail.deadline + timedelta(days=1))
    ch = record(conn, at(listings, T2), {DETAIL_ID: later}, T2)
    assert [new.id for _, new in ch.changed] == [DETAIL_ID]
    assert datetime.fromisoformat(deadline_of(conn, DETAIL_ID)) == later.deadline


def test_detail_for_unknown_listing_rejected(db, listings, detail):
    _, conn = db
    with pytest.raises(ValueError):
        record(conn, listings, {"0000-0000-000": detail}, T0)


# 7
def test_empty_list_removes_nothing(db, listings):
    path, conn = db
    record(conn, listings, {}, T0)
    before = path.read_bytes()
    ch = record(conn, [], {}, T1)
    assert ch == Changes(new=[], changed=[], removed=[], unchanged=0)
    assert path.read_bytes() == before
    assert (
        conn.execute("SELECT COUNT(*) FROM listings WHERE removed_at IS NULL").fetchone()[0] == 24
    )


# 8
def test_latest_round_trip(db, listings):
    _, conn = db
    record(conn, listings, {}, T0)
    for x in listings:
        assert latest(conn, x.id) == x
    got = latest(conn, listings[0].id)
    assert got.seen_at.tzinfo is not None and isinstance(got.size_m2, float)
    assert isinstance(got.flags, tuple)
    assert latest(conn, "0000-0000-000") is None


def test_latest_after_change_has_new_values(db, listings):
    _, conn = db
    record(conn, listings, {}, T0)
    nxt = at(listings, T1)
    nxt[0] = replace(nxt[0], top_days=500)
    record(conn, nxt, {}, T1)
    assert latest(conn, listings[0].id) == nxt[0]


def test_seen_at_must_be_tz_aware(db, listings):
    _, conn = db
    with pytest.raises(ValueError):
        record(conn, listings, {}, datetime(2026, 11, 26, 17, 17))


# 9
def test_open_db_keeps_existing_data(tmp_path, listings):
    path = tmp_path / "h.sqlite"
    conn = open_db(path)
    record(conn, listings, {}, T0)
    conn.close()
    conn = open_db(path)
    assert count(conn, "observations") == 24
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_history_db_has_no_alerts_table(db):
    # The history DB is public; alerts live in state.db (plan M3).
    _, conn = db
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {
        "listings",
        "observations",
        "bf_listings",
        "bf_observations",
        "fv_listings",
        "fv_observations",
        "sbs_listings",
        "sbs_observations",
    }


# Review fixes
def test_deadline_readable(db, listings, detail):
    _, conn = db
    record(conn, listings, {DETAIL_ID: detail}, T0)
    assert deadline(conn, DETAIL_ID) == detail.deadline
    assert deadline(conn, listings[1].id) is None
    assert deadline(conn, "0000-0000-000") is None


def test_reappearing_listing_drops_stale_deadline(db, listings, detail):
    _, conn = db
    record(conn, listings, {DETAIL_ID: detail}, T0)
    record(conn, at([x for x in listings if x.id != DETAIL_ID], T1), {}, T1)
    ch = record(conn, at(listings, T2), {}, T2)
    assert DETAIL_ID in [x.id for x in ch.new]
    assert deadline(conn, DETAIL_ID) is None


def test_listing_seen_at_must_match_run(db, listings):
    _, conn = db
    with pytest.raises(ValueError):
        record(conn, listings, {}, T1)


def test_same_page_again_across_reopen(tmp_path, listings):
    path = tmp_path / "h.sqlite"
    conn = open_db(path)
    record(conn, listings, {}, T0)
    conn.close()
    before = path.read_bytes()
    conn = open_db(path)
    ch = record(conn, at(listings, T1), {}, T1)
    conn.close()
    assert ch.unchanged == 24 and path.read_bytes() == before


def test_error_mid_run_rolls_back(db, listings, monkeypatch):
    path, conn = db
    record(conn, listings, {}, T0)
    before = path.read_bytes()
    nxt = at(listings, T1)
    nxt[0] = replace(nxt[0], top_days=777)
    nxt[5] = replace(nxt[5], rent=None)  # not storable: NOT NULL
    with pytest.raises(sqlite3.IntegrityError):
        record(conn, nxt, {}, T1)
    assert path.read_bytes() == before and latest(conn, listings[0].id) == listings[0]
