"""M3 alert tests: the five alerts, dedupe through state.db, privacy split (plan M3)."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.alerts import (
    digest_alert,
    dispatch,
    listing_alerts,
    open_state,
    run_failed,
    run_ok,
)
from housing_watch.config import load_config, load_queue_days
from housing_watch.history import open_db, record
from housing_watch.sssb import load_jsonp, parse_detail, parse_list

FIX = Path(__file__).parent / "fixtures"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)  # Thursday, ISO week 48
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
# Made-up queue days: 180 on the fixture date.
Q = load_queue_days({"QUEUE_DAYS": "180@2026-11-26", "QUEUE_DAYS_UNTIL": "2027-12-31"})
IN_WINDOW = date(2027, 7, 1)
STRIX = "5102-3203-028"  # corridor, 3,841 kr, top 132, has the detail fixture
FORUM = "2118-0403-042"  # corridor, 4,690 kr, top 504
WINNABLE_ORDER = [
    "2405-0509-005",  # Roslagstull 4,514
    "2405-0508-011",  # Roslagstull 4,523
    STRIX,  # Strix 3,841, floor 1
    "6202-4102-013",  # Kungshamra 4,171, ground floor
    "7909-0104-012",  # Lappkärrsberget floor 3
    "7505-1302-028",  # Lappkärrsberget ground floor
]


def load(name: str) -> dict:
    return load_jsonp((FIX / name).read_text(encoding="utf-8"))


def fixture_listings(when=T0):
    """All fixture listings, moved into the move-in window."""
    return [replace(x, move_in=IN_WINDOW) for x in parse_list(load("2026-11-26-list.jsonp"), when)]


@pytest.fixture
def detail():
    return parse_detail(load("2026-11-26-detail-5102-3203-028.jsonp"), STRIX)


@pytest.fixture
def hist(tmp_path, detail):
    conn = open_db(tmp_path / "history.sqlite")
    record(conn, fixture_listings(), {STRIX: detail}, T0)
    yield conn
    conn.close()


@pytest.fixture
def state(tmp_path):
    conn = open_state(tmp_path / "state.db")
    yield conn
    conn.close()


class Phone:
    def __init__(self, fail_on: set[str] = frozenset()):
        self.got, self.fail_on = [], set(fail_on)

    def __call__(self, alert):
        if alert.listing_id in self.fail_on:
            raise OSError("ntfy down")
        self.got.append(alert)


def kinds(alerts):
    return [(a.kind, a.listing_id) for a in alerts]


# --- Winnable now ---
def test_winnable_alerts_in_rank_order(hist):
    alerts = listing_alerts(hist, CFG, Q, frozenset(), T0)
    assert kinds(alerts) == [("winnable", i) for i in WINNABLE_ORDER]


def test_winnable_wording(hist):
    a = next(a for a in listing_alerts(hist, CFG, Q, frozenset(), T0) if a.listing_id == STRIX)
    assert a.priority == "high"
    assert a.title == "Winnable: Strix, Rum i korridor, 3,841 kr"
    assert "Top 132 vs your 180 · 5 applicants" in a.body
    assert "3,841 kr/month (10 mån hyra" in a.body and "move-in 2027-07-01" in a.body
    assert "Book by Mon 30 Nov 16:00" in a.body
    assert a.url.startswith("https://") and "refid=" in a.url


def test_tied_and_over_budget_in_title(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    room = next(x for x in fixture_listings() if x.id == "2405-0508-011")
    record(hist, [replace(room, top_days=180, rent=6500)], {}, T0)
    (a,) = listing_alerts(hist, CFG, Q, frozenset(), T0)
    assert a.title.startswith("Winnable (tied, over budget): Roslagstull")


def test_nobody_booked_wording(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    room = next(x for x in fixture_listings() if x.id == "2405-0508-011")
    record(hist, [replace(room, top_days=None, applicants=0)], {}, T0)
    (a,) = listing_alerts(hist, CFG, Q, frozenset(), T0)
    assert "No bookings yet · you have 180" in a.body
    assert "Book by" not in a.body  # deadline unknown


def test_becomes_winnable_as_my_days_grow(tmp_path, state):
    hist = open_db(tmp_path / "h.sqlite")
    room = next(x for x in fixture_listings() if x.id == "2405-0508-011")
    record(hist, [replace(room, top_days=181)], {}, T0)
    assert listing_alerts(hist, CFG, Q, frozenset(), T0) == []  # 180 < 181
    a1 = listing_alerts(hist, CFG, Q, frozenset(), T0 + timedelta(days=1))  # 181: tied
    a2 = listing_alerts(hist, CFG, Q, frozenset(), T0 + timedelta(days=2))  # 182: winnable
    assert [a.state_key for a in a1] == ["tied@2026-11-26"]
    assert [a.state_key for a in a2] == ["winnable@2026-11-26"]


# --- Dedupe ---
def test_same_run_twice_sends_nothing_new(hist, state):
    phone = Phone()
    dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), T0), phone, T0)
    assert len(phone.got) == 6
    dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), T0), phone, T0)
    assert len(phone.got) == 6


def test_relisting_alerts_again(hist, state):
    phone = Phone()
    dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), T0), phone, T0)
    t1 = T0 + timedelta(days=3)
    rest = [x for x in fixture_listings(t1) if x.id != STRIX]
    record(hist, rest, {}, t1)  # Strix gone
    # By now I have 183 days, so Pax (top 182) is winnable too: send that first.
    dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), t1), phone, t1)
    t2 = t1 + timedelta(days=3)
    again = [
        replace(x, published=date(2026, 12, 2)) if x.id == STRIX else x
        for x in fixture_listings(t2)
    ]
    record(hist, again, {}, t2)
    sent = dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), t2), phone, t2).sent
    assert kinds(sent) == [("winnable", STRIX)]


def test_failed_send_is_retried(hist, state):
    phone = Phone(fail_on={STRIX})
    out = dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), T0), phone, T0)
    assert len(out.sent) == 5 and [a.listing_id for a, _ in out.failed] == [STRIX]
    phone.fail_on.clear()
    out = dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), T0), phone, T0)
    assert kinds(out.sent) == [("winnable", STRIX)] and not out.failed


def test_state_db_dedupe_index(state):
    sql = "INSERT INTO alerts (listing_id, kind, state_key, sent_at, message) VALUES (?,?,?,?,?)"
    for lid in (STRIX, None):
        row = (lid, "k", "s", T0.isoformat(), "m")
        state.execute(sql, row)
        with pytest.raises(sqlite3.IntegrityError):
            state.execute(sql, row)


# --- Last call ---
DEADLINE = datetime(2026, 11, 30, 16, 0, tzinfo=STHLM)


@pytest.mark.parametrize(
    ("before", "fires"),
    [
        (timedelta(hours=5, minutes=30), True),
        (timedelta(hours=5, minutes=31), False),
        (timedelta(hours=3), True),
        (timedelta(minutes=1), True),
        (timedelta(0), False),
        (-timedelta(minutes=5), False),
    ],
)
def test_last_call_window(hist, before, fires):
    alerts = listing_alerts(hist, CFG, Q, frozenset(), DEADLINE - before)
    last = [a for a in alerts if a.kind == "last_call"]
    assert [a.listing_id for a in last] == ([STRIX] if fires else [])
    if fires:
        assert alerts[0] == last[0]  # urgent first
    # A last call replaces the winnable alert (one ping); after the deadline, neither.
    winnable = {a.listing_id for a in alerts if a.kind == "winnable"}
    assert (STRIX in winnable) == (before > timedelta(hours=5, minutes=30))


def test_last_call_wording(hist):
    (a,) = [
        a
        for a in listing_alerts(
            hist, CFG, Q, frozenset(), DEADLINE - timedelta(hours=4, minutes=40)
        )
        if a.kind == "last_call"
    ]
    assert a.priority == "urgent"
    assert a.title == "LAST CALL, closes 16:00: Strix, Rum i korridor, 3,841 kr"
    assert "Closes 16:00 today (in 4 h 40 min)" in a.body
    assert "you lead 184 vs 132 · 5 applicants" in a.body
    assert a.state_key == DEADLINE.astimezone(UTC).isoformat()


def test_no_last_call_when_behind(tmp_path, detail):
    hist = open_db(tmp_path / "h.sqlite")
    strix = next(x for x in fixture_listings() if x.id == STRIX)
    record(hist, [replace(strix, top_days=900)], {STRIX: detail}, T0)
    assert listing_alerts(hist, CFG, Q, frozenset(), DEADLINE - timedelta(hours=1)) == []


def test_no_last_call_without_deadline(hist):
    # Only Strix has a known deadline; others never get a last call.
    alerts = listing_alerts(hist, CFG, Q, frozenset(), DEADLINE - timedelta(hours=1))
    assert {a.listing_id for a in alerts if a.kind == "last_call"} == {STRIX}


# --- Lead lost ---
def test_lead_lost_on_booked_listing(hist):
    alerts = listing_alerts(hist, CFG, Q, frozenset({FORUM}), T0)
    (a,) = [a for a in alerts if a.kind == "lead_lost"]
    assert a.listing_id == FORUM and a.priority == "high"
    assert a.title.startswith("Lead lost: Forum, ")
    assert "Top is now 504, you have 180 · 3 applicants" in a.body
    assert a.state_key == "lost@2026-11-26"


def test_booked_and_leading_is_quiet(hist):
    alerts = listing_alerts(hist, CFG, Q, frozenset({STRIX}), DEADLINE - timedelta(hours=1))
    assert STRIX not in {a.listing_id for a in alerts}  # no winnable, no last call, no lead lost


def test_booked_and_tied_with_myself_is_quiet(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    strix = next(x for x in fixture_listings() if x.id == STRIX)
    record(hist, [replace(strix, top_days=180)], {}, T0)
    assert listing_alerts(hist, CFG, Q, frozenset({STRIX}), T0) == []


def test_removed_listing_gets_no_alerts(hist):
    t1 = T0 + timedelta(minutes=31)
    record(hist, [x for x in fixture_listings(t1) if x.id != FORUM], {}, t1)
    assert listing_alerts(hist, CFG, Q, frozenset({FORUM}), t1) == listing_alerts(
        hist, CFG, Q, frozenset(), t1
    )


# --- Weekly digest ---
SUNDAY_18 = datetime(2026, 11, 29, 18, 0, tzinfo=STHLM)


@pytest.mark.parametrize(
    ("now", "due"),
    [
        (SUNDAY_18 - timedelta(minutes=1), False),
        (SUNDAY_18, True),
        (SUNDAY_18 + timedelta(hours=23, minutes=59), True),  # late run catches up
        (SUNDAY_18 + timedelta(hours=24, minutes=1), False),
    ],
)
def test_digest_timing(hist, now, due):
    a = digest_alert(hist, CFG, Q, frozenset(), now)
    assert (a is not None) == due
    if due:
        assert a.kind == "digest" and a.state_key == "2026-W48" and a.listing_id is None
        assert a.priority == "low"


def test_digest_once_per_week(hist, state):
    phone = Phone()
    for now in (SUNDAY_18, SUNDAY_18 + timedelta(minutes=30)):
        dispatch(state, [digest_alert(hist, CFG, Q, frozenset(), now)], phone, now)
    assert len(phone.got) == 1


def test_digest_content(hist):
    t1 = T0 + timedelta(minutes=31)
    gone = {STRIX, "5102-3503-004"}  # Strix corridor (top 132) and 1 rum & kök (top 94)
    record(hist, [x for x in fixture_listings(t1) if x.id not in gone], {}, t1)
    a = digest_alert(hist, CFG, Q, frozenset(), SUNDAY_18)
    assert (
        a.title == "Weekly digest, week 48: 24 seen, 6 winnable"
    )  # -Strix (closed), +Pax (183 > 182)
    assert "Strix 94–132" in a.body
    assert "Kungshamra, 3 rum & kök, 13,649 kr · top 139 · winnable\n" in a.body  # shared rent
    assert "Strix, 1 rum & kök, 7,172 kr · top 94 · winnable, over budget" in a.body
    assert "closed" in a.body  # removed listings are marked


def test_digest_shows_other_types(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    strix = next(x for x in fixture_listings() if x.id == STRIX)
    record(hist, [replace(strix, type="Rum i dubbel")], {}, T0)
    a = digest_alert(hist, CFG, Q, frozenset(), SUNDAY_18)
    assert "other type: Rum i dubbel" in a.body


def test_digest_body_fits_ntfy(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    strix = next(x for x in fixture_listings() if x.id == STRIX)
    many = [replace(strix, id=f"9999-0000-{i:03d}") for i in range(300)]
    record(hist, many, {}, T0)
    a = digest_alert(hist, CFG, Q, frozenset(), SUNDAY_18)
    assert len(a.body.encode()) <= 4000 and "more" in a.body


# --- Heartbeat ---
def test_heartbeat_on_third_failure(state):
    t = [T0 + timedelta(minutes=31 * i) for i in range(6)]
    assert run_failed(state, t[0], "boom") is None
    assert run_failed(state, t[1], "boom") is None
    a = run_failed(state, t[2], "ParseError hyra: unexpected '3,841'")
    assert a.kind == "heartbeat" and a.priority == "high" and a.listing_id is None
    assert a.title == "housing-watch: 3 failed runs since 18:17"
    assert "ParseError hyra" in a.body
    phone = Phone()
    dispatch(state, [a], phone, t[2])
    dispatch(state, [run_failed(state, t[3], "boom")], phone, t[3])
    assert len(phone.got) == 1  # once per streak


def test_heartbeat_reset_by_success(state):
    for i in range(2):
        run_failed(state, T0 + timedelta(minutes=i), "boom")
    run_ok(state)
    assert run_failed(state, T0 + timedelta(minutes=5), "boom") is None


def test_new_streak_alerts_again(state):
    phone = Phone()
    for start in (T0, T0 + timedelta(days=1)):
        for i in range(3):
            a = run_failed(state, start + timedelta(minutes=31 * i), "boom")
        dispatch(state, [a], phone, start)
        run_ok(state)
    assert len(phone.got) == 2


def test_run_ok_writes_nothing_when_healthy(tmp_path):
    path = tmp_path / "state.db"
    conn = open_state(path)
    run_ok(conn)
    before = path.read_bytes()
    run_ok(conn)
    conn.close()
    assert path.read_bytes() == before


# --- Privacy split ---
def test_history_db_holds_no_alerts(hist, state, tmp_path):
    phone = Phone()
    alerts = listing_alerts(hist, CFG, Q, frozenset({FORUM}), T0)
    alerts.append(digest_alert(hist, CFG, Q, frozenset({FORUM}), SUNDAY_18))
    dispatch(state, alerts, phone, T0)
    tables = {r[0] for r in hist.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"listings", "observations"}
    raw = (tmp_path / "history.sqlite").read_bytes()
    assert b"you have" not in raw and b"Winnable" not in raw
    assert state.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == len(phone.got) == 8


def test_tied_then_winnable_both_sent(tmp_path, state):
    hist = open_db(tmp_path / "h.sqlite")
    room = next(x for x in fixture_listings() if x.id == "2405-0508-011")
    record(hist, [replace(room, top_days=181)], {}, T0)
    phone = Phone()
    for day in range(4):  # 180 behind, 181 tied, 182 winnable, 183 still winnable
        now = T0 + timedelta(days=day)
        dispatch(state, listing_alerts(hist, CFG, Q, frozenset(), now), phone, now)
    assert [a.title.split(":")[0] for a in phone.got] == ["Winnable (tied)", "Winnable"]
