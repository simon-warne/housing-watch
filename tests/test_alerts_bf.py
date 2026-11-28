"""M5 part B: BF alerts (winnable, last call, first come), BF heartbeat, BF digest section."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch.alerts import (
    bf_listing_alerts,
    digest_alert,
    dispatch,
    open_state,
    run_failed,
    run_ok,
)
from housing_watch.bf import BfDetail, parse_bf_list
from housing_watch.config import load_config, load_queue_days
from housing_watch.history import bf_record, open_db

FIX = Path(__file__).parent / "fixtures" / "bf"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 34, tzinfo=UTC)
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
Q = load_queue_days({"QUEUE_DAYS": "180@2026-11-26", "QUEUE_DAYS_UNTIL": "2027-12-31"})
SINCE = date(2021, 1, 1)  # made up
CORRIDOR = "202619429"  # Norra Djurgården corridor, 6,213 kr (stretch), closes 2026-11-29
KISTA = "202514073"  # Kista student flat, 6,461 kr: tier 2, so never in the stretch band
DET = BfDetail(move_in=date(2027, 7, 1), top_dates=(date(2021, 11, 27), date(2026, 6, 4)))
FRESH = frozenset({CORRIDOR})
LAST_CALL_AT = datetime(2026, 11, 29, 20, 0, tzinfo=STHLM)  # 3 h 59 min before 23:59
SUNDAY_18 = datetime(2026, 11, 29, 18, 0, tzinfo=STHLM)


def bf_listings(when=T0):
    return parse_bf_list((FIX / "2026-11-26-alla-annonser.json").read_text("utf-8"), when)


def hist_with(tmp_path, details, listings=None):
    conn = open_db(tmp_path / "history.sqlite")
    bf_record(conn, listings or bf_listings(), details, T0)
    return conn


@pytest.fixture
def hist(tmp_path):
    conn = hist_with(tmp_path, {CORRIDOR: DET})
    yield conn
    conn.close()


def kinds(alerts):
    return [(a.kind, a.listing_id) for a in alerts]


def test_winnable_wording(hist):
    [a] = bf_listing_alerts(hist, CFG, SINCE, FRESH, T0)
    assert (a.kind, a.listing_id, a.priority) == ("winnable", "bf:" + CORRIDOR, "high")
    assert a.title == "BF · Winnable (over budget): Norra Djurgården, Studentkorridor, 6,213 kr"
    assert "Ruddammsvägen 65, floor 3" in a.body
    assert "6,213 kr/month · move-in 2027-07-01" in a.body
    assert "Top queue since 2021-11-27 vs yours 2021-01-01" in a.body
    assert "Apply by Sun 29 Nov" in a.body
    assert a.url == "https://bostad.stockholm.se/bostad/202619429/"


def test_half_floor_survives_history_and_reads_as_floor(tmp_path):
    xs = [replace(x, floor=0.5) if x.id == CORRIDOR else x for x in bf_listings()]
    conn = hist_with(tmp_path, {CORRIDOR: DET}, xs)
    [a] = bf_listing_alerts(conn, CFG, SINCE, FRESH, T0)
    assert "Ruddammsvägen 65, floor 0.5" in a.body
    conn.close()


def test_no_queue_date_no_alerts(hist):
    assert bf_listing_alerts(hist, CFG, None, FRESH, T0) == []


def test_stale_detail_no_alerts(hist):
    """Only a detail fetched this run may alert (the stored one can be days old)."""
    assert bf_listing_alerts(hist, CFG, SINCE, frozenset(), T0) == []


def test_behind_no_alert(hist):
    assert bf_listing_alerts(hist, CFG, date(2022, 1, 1), FRESH, T0) == []


def test_tied_marked(hist):
    [a] = bf_listing_alerts(hist, CFG, date(2021, 11, 27), FRESH, T0)
    assert a.title.startswith("BF · Winnable (tied, over budget): ")


def test_last_call_replaces_winnable(hist):
    [a] = bf_listing_alerts(hist, CFG, SINCE, FRESH, LAST_CALL_AT)
    assert (a.kind, a.priority) == ("last_call", "urgent")
    assert a.title == (
        "BF · LAST CALL, closes 23:59 (over budget): Norra Djurgården, Studentkorridor, 6,213 kr"
    )
    assert "Closes 23:59 today (in 3 h 59 min)" in a.body
    assert "yours 2021-01-01 vs top 2021-11-27" in a.body


def test_nothing_after_close(hist):
    after = datetime(2026, 11, 30, 0, 5, tzinfo=STHLM)
    assert bf_listing_alerts(hist, CFG, SINCE, FRESH, after) == []


def test_no_applicants_shown_last_call_only(tmp_path):
    hist = hist_with(tmp_path, {CORRIDOR: replace(DET, top_dates=())})
    assert bf_listing_alerts(hist, CFG, SINCE, FRESH, T0) == []
    [a] = bf_listing_alerts(hist, CFG, SINCE, FRESH, LAST_CALL_AT)
    assert a.kind == "last_call" and "no applicants shown" in a.title
    assert "no applicants shown" in a.body


def test_bostad_snabbt_first_come(tmp_path):
    xs = [
        replace(x, bostad_snabbt=True, rent=5000) if x.id == CORRIDOR else x for x in bf_listings()
    ]
    hist = hist_with(tmp_path, {CORRIDOR: replace(DET, top_dates=())}, xs)
    [a] = bf_listing_alerts(hist, CFG, SINCE, FRESH, T0)
    assert a.kind == "first_come" and a.title.startswith("BF · First come: ")
    assert "Bostad snabbt" in a.body


def test_not_eligible_no_alert(tmp_path):
    hist = hist_with(tmp_path, {KISTA: DET})
    assert bf_listing_alerts(hist, CFG, SINCE, frozenset({KISTA}), T0) == []


def test_bf_alert_dedupes(tmp_path, hist):
    state = open_state(tmp_path / "state.db")
    got = []
    for _ in range(2):
        dispatch(state, bf_listing_alerts(hist, CFG, SINCE, FRESH, T0), got.append, T0)
    assert len(got) == 1


# --- BF heartbeat: own streak ---
def test_bf_heartbeat_is_separate(tmp_path):
    state = open_state(tmp_path / "state.db")
    t = [T0 + timedelta(minutes=31 * i) for i in range(3)]
    for i in range(2):
        assert run_failed(state, t[i], "boom", source="bf") is None
    run_ok(state)  # SSSB fine: the BF streak goes on
    sssb = [run_failed(state, t[i], "x") for i in range(3)]
    bf = run_failed(state, t[2], "ParseError: Inflytt", source="bf")
    assert bf.title.startswith("BF: 3 failed runs since ")
    assert sssb[2].title.startswith("housing-watch: 3 failed runs since ")
    got = []
    dispatch(state, [sssb[2], bf], got.append, t[2])
    assert len(got) == 2  # same moment, both sent
    run_ok(state, source="bf")
    assert run_failed(state, t[2] + timedelta(minutes=31), "boom", source="bf") is None


# --- digest ---
def test_digest_bf_section(tmp_path):
    hist = hist_with(tmp_path, {CORRIDOR: DET})
    later = [x for x in bf_listings(SUNDAY_18) if x.id != CORRIDOR]
    bf_record(hist, later, {}, SUNDAY_18)  # the corridor closed
    a = digest_alert(hist, CFG, Q, frozenset(), SUNDAY_18, bf_since=SINCE)
    lines = a.body.splitlines()
    assert any(line.startswith("BF: ") and "seen" in line for line in lines)
    assert "BF winning dates: Norra Djurgården 2021-11-27" in lines
    corridor = [
        line for line in lines if "Ruddammsvägen" in line or "Studentkorridor, 6,213" in line
    ]
    assert corridor and "closed" in corridor[0] and corridor[0].startswith("BF ")
    assert not any("Flemingsberg" in line or "Hyresrätt" in line for line in lines)
    assert "BF" in a.title


def test_digest_without_bf_rows_is_unchanged(tmp_path):
    hist = open_db(tmp_path / "h.sqlite")
    a = digest_alert(hist, CFG, Q, frozenset(), SUNDAY_18)
    assert "BF" not in a.title and "BF" not in a.body
