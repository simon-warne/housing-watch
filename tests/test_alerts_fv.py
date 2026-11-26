"""M6: Förvaltaren alerts (winnable, last call, lead lost), FV heartbeat, FV digest section."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.alerts import (
    digest_alert,
    dispatch,
    fv_alerted,
    fv_listing_alerts,
    open_state,
    run_failed,
)
from housing_watch.config import FvPoints, load_config, load_queue_days
from housing_watch.fv import FvDetail, parse_fv_list
from housing_watch.history import fv_record, open_db

FIX = Path(__file__).parent / "fixtures" / "forvaltaren"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
Q = load_queue_days({"QUEUE_DAYS": "180@2026-11-26", "QUEUE_DAYS_UNTIL": "2027-12-31"})
PTS = FvPoints(base=3994, base_date=date(2026, 11, 20))  # made up: 4,000 on 26 Nov
DUVBO = "61-10-1-302"  # Duvbo 1 rum o kokvrå, 4,290 kr, floor 3, closes 2026-11-30
RISSNE = "73-32-1-0102"  # Rissne 1 rum o kokvrå, 5,990 kr, closes 2026-11-26 (today)
DET = FvDetail(move_in=date(2027, 5, 1), top_points=3000, applicants=8)
FRESH = frozenset({DUVBO, RISSNE})
NONE = frozenset()
LAST_CALL_AT = datetime(2026, 11, 30, 20, 0, tzinfo=STHLM)  # 3 h 59 min before 23:59
SUNDAY_18 = datetime(2026, 11, 29, 18, 0, tzinfo=STHLM)


def fv_listings(when=T0):
    return parse_fv_list((FIX / "2026-11-26-objects-lagenhet.json").read_text("utf-8"), when)


def hist_with(tmp_path, details):
    conn = open_db(tmp_path / "history.sqlite")
    fv_record(conn, fv_listings(), details, T0)
    return conn


@pytest.fixture
def hist(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: DET})
    yield conn
    conn.close()


def kinds(alerts):
    return [(a.kind, a.listing_id) for a in alerts]


def test_winnable_wording(hist):
    [a] = fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, T0)
    assert (a.kind, a.listing_id, a.priority) == ("winnable", "fv:" + DUVBO, "high")
    assert a.title == "FV · Winnable: Duvbo, 1 rum o kokvrå, 4,290 kr"
    assert "Duvbo · Järnvägsgatan 23, floor 3" in a.body
    assert "4,290 kr/month · move-in 2027-05-01" in a.body
    assert "Top 3000 points vs your 4000 · 8 applicants" in a.body
    assert "Apply by Mon 30 Nov" in a.body
    assert a.url == "https://www.forvaltaren.se/objekt/61-10-1-302"


def test_no_points_no_alerts(hist):
    assert fv_listing_alerts(hist, CFG, None, FRESH, NONE, T0) == []


def test_stale_detail_no_alerts(hist):
    assert fv_listing_alerts(hist, CFG, PTS, frozenset({RISSNE}), NONE, T0) == []


def test_behind_no_alert(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: replace(DET, top_points=4001)})
    assert fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, T0) == []


def test_tied_marked(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: replace(DET, top_points=4000)})
    [a] = fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, T0)
    assert a.title.startswith("FV · Winnable (tied): Duvbo")


def test_tied_after_my_own_alert_is_quiet(tmp_path):
    """Once I was told it's winnable, a tie is most likely my own application."""
    conn = hist_with(tmp_path, {DUVBO: replace(DET, top_points=4000)})
    assert fv_listing_alerts(conn, CFG, PTS, FRESH, frozenset({"fv:" + DUVBO}), T0) == []


def test_points_grow_daily(hist):
    """3000 top, and 2 days later I have 4,002: still the same winnable alert (same key)."""
    a = fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, T0)
    b = fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, T0 + timedelta(days=2))
    assert a[0].state_key == b[0].state_key and "your 4002" in b[0].body


def test_last_call_replaces_winnable(hist):
    [a] = fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, LAST_CALL_AT)
    assert (a.kind, a.priority) == ("last_call", "urgent")
    assert a.title == "FV · LAST CALL, closes 23:59: Duvbo, 1 rum o kokvrå, 4,290 kr"
    assert a.body.startswith("Closes 23:59 today (in 3 h 59 min) · you lead 4004 vs 3000")
    assert "Järnvägsgatan 23, floor 3 · move-in 2027-05-01" in a.body


def test_nothing_after_close(hist):
    end = datetime(2026, 11, 30, 23, 59, tzinfo=STHLM)
    assert fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, end) == []


def test_no_applicants_shown_last_call_only(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: replace(DET, top_points=None, applicants=None)})
    assert fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, T0) == []
    [a] = fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, LAST_CALL_AT)
    assert a.kind == "last_call" and "(no applicants shown)" in a.title
    assert "no applicants shown, you have 4004" in a.body


def test_lead_lost_after_an_alert(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: replace(DET, top_points=4500, applicants=9)})
    assert fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, T0) == []  # never alerted
    [a] = fv_listing_alerts(conn, CFG, PTS, FRESH, frozenset({"fv:" + DUVBO}), T0)
    assert (a.kind, a.listing_id, a.priority) == ("lead_lost", "fv:" + DUVBO, "high")
    assert a.title == "FV · Lead lost: Duvbo, Järnvägsgatan 23"
    assert a.body.startswith("Top is now 4500, you have 4000 · 9 applicants · closes Mon 30 Nov")


def test_not_eligible_no_alert(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: replace(DET, move_in=date(2027, 2, 1))})
    assert fv_listing_alerts(conn, CFG, PTS, FRESH, NONE, T0) == []


def test_fv_alerted_reads_the_alert_log(tmp_path, hist):
    state = open_state(tmp_path / "state.db")
    sent = []
    dispatch(state, fv_listing_alerts(hist, CFG, PTS, FRESH, NONE, T0), sent.append, T0)
    assert fv_alerted(state) == frozenset({"fv:" + DUVBO})
    again = fv_listing_alerts(hist, CFG, PTS, FRESH, fv_alerted(state), T0)
    assert dispatch(state, again, sent.append, T0).sent == [] and len(sent) == 1


def test_fv_heartbeat_is_separate(tmp_path):
    state = open_state(tmp_path / "state.db")
    for _ in range(2):
        assert run_failed(state, T0, "boom", source="fv") is None
    a = run_failed(state, T0, "boom", source="fv")
    assert a.title.startswith("FV: 3 failed runs since ")
    assert run_failed(state, T0, "boom") is None  # SSSB's streak untouched


def test_digest_fv_section(tmp_path):
    conn = hist_with(tmp_path, {DUVBO: DET, RISSNE: replace(DET, move_in=date(2027, 2, 1))})
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18, fv_points=PTS)
    lines = d.body.splitlines()
    assert "FV 17 seen, 1 winnable" in d.title
    assert "FV: 17 seen, 1 winnable" in lines
    assert "FV Duvbo, 1 rum o kokvrå, 4,290 kr · top 3000 · winnable" in lines
    assert any(
        x.startswith("FV Rissne, 1 rum o kokvrå, 5,990 kr") and "early move-in 2027-02-01" in x
        for x in lines
    )
    assert any(x.startswith("FV Ursvik, 1 rum o kök, 10,113 kr · top ? ") for x in lines)


def test_digest_without_fv_rows_has_no_fv_section(tmp_path):
    conn = open_db(tmp_path / "history.sqlite")
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18, fv_points=PTS)
    assert "FV" not in d.title and not any(x.startswith("FV") for x in d.body.splitlines())
