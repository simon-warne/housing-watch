"""M4 runner: fetch, details, record, alerts, heartbeat, encryption, quiet logs (plan M4).

Offline: a fake SSSB built from the fixtures, a fake phone, a fake clock and sleep.
All queue days and keys are made up.
"""

import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from housing_watch.crypto import decrypt
from housing_watch.run import DETAIL_CAP, PAUSE_S, main, run

FIX = Path(__file__).parent / "fixtures"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)
DEADLINE = datetime(2026, 11, 30, 16, 0, tzinfo=STHLM)  # the detail fixture's
IN_WINDOW = "2027-07-01"
STRIX = "5102-3203-028"
KEY = Fernet.generate_key().decode()
ENV = {
    "QUEUE_DAYS": "180@2026-11-26",  # made up
    "QUEUE_DAYS_UNTIL": "2027-12-31",
    "BOOKED_IDS": "",
    "STATE_KEY": KEY,
    "NTFY_TOPIC": "made-up-topic",
}
# Eligible and winnable with 180 days once moved into the window (see test_alerts).
WINNABLE = {
    "2405-0509-005",
    "2405-0508-011",
    STRIX,
    "6202-4102-013",
    "7909-0104-012",
    "7505-1302-028",
}
PAX = "5201-0108-050"  # top 182: tied from 28 Nov (180 + 2), winnable after (STATUS gotcha)
WINNABLE_LATE = WINNABLE | {PAX}
# Worth a detail request by the public rule (type, window, rent; stretch areas up to 7,000
# whatever the status): the 11 eligible ones plus Idun 1 rum & pentry 6,057. 12 > DETAIL_CAP.
PUBLIC = 12


def _unwrap(text: str) -> dict:
    return json.loads(re.fullmatch(r"\s*cb\((.*)\);?\s*", text, re.DOTALL).group(1))


def _wrap(payload: dict) -> str:
    return "cb(" + json.dumps(payload, ensure_ascii=False) + ");"


class FakeSSSB:
    """Serves the list fixture (every listing moved into the move-in window) and a detail page
    per listing made from the detail fixture, with an optional deadline and top-days override."""

    def __init__(self):
        self.list = _unwrap((FIX / "2026-11-26-list.jsonp").read_text(encoding="utf-8"))
        for item in self.items:
            item["inflyttningDatum"] = IN_WINDOW
        self.detail = _unwrap(
            (FIX / "2026-11-26-detail-5102-3203-028.jsonp").read_text(encoding="utf-8")
        )
        self.deadline = DEADLINE
        self.detail_top: dict[str, int] = {}
        self.calls: list[str] = []
        self.fail: Exception | None = None

    @property
    def items(self):
        return self.list["data"]["objektlistabilder@lagenheter"]

    def set_items(self, items):
        self.list["data"]["objektlistabilder@lagenheter"] = items
        self.list["html"]["objektsummering-TOTAL@lagenheter"] = str(len(items))

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if "objektlistabilder" in url:
            return _wrap(self.list)
        refid = re.search(r"refid=([0-9a-f]+)", url).group(1)
        item = next(i for i in self.items if i["detaljUrl"].endswith("refid=" + refid))
        nr = item["objektNr"]
        top, n = re.fullmatch(r"(\d+) \((\d+)st\)", item["antalIntresse"]).groups()
        top = self.detail_top.get(nr, int(top))
        page = json.loads(json.dumps(self.detail))
        html = page["html"]
        html["objektinformation@lagenheter"] = re.sub(
            r'(<dd class="ObjektNummer">\s*)[^<\s]+',
            rf"\g<1>{nr}",
            html["objektinformation@lagenheter"],
        )
        dl = self.deadline.astimezone(STHLM)
        html["objektintresse"] = f"<p>Kan bokas till {dl:%Y-%m-%d} klockan {dl:%H:%M}</p>"
        html["objektintressestatus"] = (
            f"Denna bostad har bokats av {n} personer, varav den med flest ködagar "
            f"just nu har {top} ködagar."
        )
        return _wrap(page)

    def detail_calls(self):
        return [u for u in self.calls if "refid=" in u]


class Phone:
    def __init__(self):
        self.got = []

    def __call__(self, alert):
        self.got.append(alert)


class Clock:
    def __init__(self):
        self.slept: list[float] = []

    def __call__(self, s: float):
        self.slept.append(s)


@pytest.fixture
def sssb():
    return FakeSSSB()


def go(tmp_path, sssb, now, *, env=ENV, init=None, phone=None, sleep=None, **kw):
    phone = phone if phone is not None else Phone()
    sleep = sleep if sleep is not None else Clock()
    if init is None:
        init = not (tmp_path / "state.db.enc").exists()
    rc = run(tmp_path, env, get=sssb, send=phone, sleep=sleep, now=now, init=init, **kw)
    return rc, phone, sleep


def state_rows(tmp_path, sql):
    raw = decrypt((tmp_path / "state.db.enc").read_bytes(), KEY)
    p = tmp_path / "plain.db"
    p.write_bytes(raw)
    conn = sqlite3.connect(p)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
        p.unlink()


# --- the happy path ---
def test_first_run_alerts_winnable_listings_and_writes_encrypted_state(tmp_path, sssb):
    rc, phone, _ = go(tmp_path, sssb, T0)
    assert rc == 0
    assert {a.listing_id for a in phone.got} == WINNABLE
    assert {a.kind for a in phone.got} == {"winnable"}
    assert (tmp_path / "ready").exists()
    assert (tmp_path / "history.sqlite").exists()
    assert not (tmp_path / "state.db").exists()  # never plaintext next to the data
    raw = (tmp_path / "state.db.enc").read_bytes()
    assert b"SQLite" not in raw and b"Winnable" not in raw
    assert len(state_rows(tmp_path, "SELECT * FROM alerts")) == len(WINNABLE)


def test_details_fetched_once_for_unknown_deadlines(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    assert len(sssb.detail_calls()) == DETAIL_CAP  # eligible, deadline unknown, capped
    sssb.calls.clear()
    rc, phone, _ = go(tmp_path, sssb, T0 + timedelta(minutes=30))
    assert rc == 0
    assert len(sssb.detail_calls()) == PUBLIC - DETAIL_CAP  # the ones left over
    assert phone.got == []  # nothing new
    sssb.calls.clear()
    go(tmp_path, sssb, T0 + timedelta(minutes=60))
    assert sssb.detail_calls() == []  # all deadlines known and > 48 h away


def test_details_every_run_within_48h_of_deadline_rotating(tmp_path, sssb):
    near = DEADLINE - timedelta(hours=47)
    go(tmp_path, sssb, near - timedelta(hours=3))
    go(tmp_path, sssb, near - timedelta(hours=2))  # all 12 deadlines known now
    seen = set()
    for i in range(2):
        sssb.calls.clear()
        go(tmp_path, sssb, near + timedelta(minutes=30 * (i + 1)))
        calls = sssb.detail_calls()
        assert len(calls) == DETAIL_CAP  # 12 close within 48 h, capped
        seen |= set(calls)
    assert len(seen) == PUBLIC  # rotation: everyone gets a live figure within two runs


def test_no_details_after_the_deadline(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    go(tmp_path, sssb, T0 + timedelta(minutes=30))
    sssb.calls.clear()
    rc, phone, _ = go(tmp_path, sssb, DEADLINE + timedelta(minutes=5))
    assert rc == 0
    assert sssb.detail_calls() == []
    assert {a.kind for a in phone.got} <= {"digest"}  # Monday 16:05: digest catch-up only


def test_detail_cap_per_run(tmp_path, sssb):
    for item in sssb.items:  # make every listing an eligible corridor room
        item["typ"], item["hyra"] = "Rum i korridor", "3\xa0000"
    go(tmp_path, sssb, T0)
    assert len(sssb.detail_calls()) == DETAIL_CAP == 10
    sssb.calls.clear()
    go(tmp_path, sssb, T0 + timedelta(minutes=30))
    assert len(sssb.detail_calls()) == 10
    sssb.calls.clear()
    go(tmp_path, sssb, T0 + timedelta(minutes=60))
    assert len(sssb.detail_calls()) == len(sssb.items) - 20


def _public_trace(tmp_path, sssb, env):
    """What the world can see: detail requests per run, and the history DB's rows."""
    times = [
        T0,
        T0 + timedelta(minutes=30),
        DEADLINE - timedelta(hours=40),
        DEADLINE - timedelta(hours=4),
    ]
    trace = []
    for t in times:
        sssb.calls.clear()
        go(tmp_path, sssb, t, env=env)
        trace.append(sorted(sssb.detail_calls()))
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    rows = conn.execute("SELECT * FROM observations ORDER BY listing_id, seen_at").fetchall()
    conn.close()
    return trace, rows


def test_public_traces_do_not_depend_on_queue_days_or_bookings(tmp_path):
    """M4 review: requests and history must not reveal my days or BOOKED_IDS."""
    a = _public_trace(tmp_path / "a", FakeSSSB(), ENV)
    other = {**ENV, "QUEUE_DAYS": "400@2026-11-26", "BOOKED_IDS": "1106-2108-009,7202-5306-106"}
    b = _public_trace(tmp_path / "b", FakeSSSB(), other)
    assert a == b


def test_higher_detail_top_suppresses_false_winnable(tmp_path, sssb):
    """PRD §1: the list may lag; near close the detail page's higher figure wins."""
    sssb.set_items([i for i in sssb.items if i["objektNr"] in WINNABLE_LATE])  # under the cap
    go(tmp_path, sssb, DEADLINE - timedelta(hours=60))  # deadlines known, alerts sent
    sssb.detail_top[STRIX] = 200  # someone with 200 days booked; the list still says 132
    rc, phone, _ = go(tmp_path, sssb, DEADLINE - timedelta(hours=4))  # last-call window
    assert rc == 0
    assert STRIX not in {a.listing_id for a in phone.got}
    others = {a.listing_id for a in phone.got if a.kind == "last_call"}
    assert others == WINNABLE_LATE - {STRIX}


def test_lower_detail_top_does_not_lower_the_figure(tmp_path, sssb):
    sssb.set_items([i for i in sssb.items if i["objektNr"] in WINNABLE_LATE])
    go(tmp_path, sssb, DEADLINE - timedelta(hours=60))
    sssb.detail_top[STRIX] = 50  # the higher (list) figure is kept
    go(tmp_path, sssb, DEADLINE - timedelta(hours=40))
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    top = conn.execute(
        "SELECT top_days FROM observations WHERE listing_id = ? ORDER BY seen_at DESC LIMIT 1",
        (STRIX,),
    ).fetchone()[0]
    conn.close()
    assert top == 132


# --- politeness ---
def test_pause_before_every_request_after_the_first(tmp_path, sssb):
    _, _, clock = go(tmp_path, sssb, T0)
    assert len(sssb.calls) == 1 + DETAIL_CAP
    assert clock.slept == [PAUSE_S] * DETAIL_CAP
    assert PAUSE_S >= 5


def test_no_fetch_within_25_minutes_of_the_last(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    sssb.calls.clear()
    rc, phone, _ = go(tmp_path, sssb, T0 + timedelta(minutes=24))
    assert rc == 0 and sssb.calls == [] and phone.got == []
    assert (tmp_path / "ready").exists()  # still pushed like any other run
    go(tmp_path, sssb, T0 + timedelta(minutes=26))
    assert "objektlistabilder" in sssb.calls[0]


def test_a_failed_fetch_also_counts_for_the_guard(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    sssb.fail = OSError("down")
    go(tmp_path, sssb, T0 + timedelta(minutes=30))
    sssb.fail, n = None, len(sssb.calls)
    go(tmp_path, sssb, T0 + timedelta(minutes=40))
    assert len(sssb.calls) == n


# --- failures and the heartbeat ---
@pytest.mark.parametrize("kind", ["empty", "parse", "network"])
def test_three_failed_runs_send_the_heartbeat(tmp_path, sssb, kind):
    go(tmp_path, sssb, T0)
    if kind == "empty":
        sssb.set_items([])
    elif kind == "parse":
        sssb.items[0]["hyra"] = "gratis"
    else:
        sssb.fail = OSError("connection reset")
    sent = []
    for i in range(1, 4):
        rc, phone, _ = go(tmp_path, sssb, T0 + timedelta(minutes=30 * i))
        assert rc == 1
        assert (tmp_path / "ready").exists()  # the streak is saved and pushed
        sent += phone.got
    assert [a.kind for a in sent] == ["heartbeat"]
    sssb.__init__()
    rc, phone, _ = go(tmp_path, sssb, T0 + timedelta(hours=2))
    assert rc == 0
    assert state_rows(tmp_path, "SELECT * FROM meta WHERE key = 'fail_count'") == []


def test_detail_parse_error_fails_the_whole_run(tmp_path, sssb):
    sssb.detail["html"]["objektintresse"] = "<p>Bokning stängd</p>"
    orig = sssb.__call__

    def get(url):
        if "refid=" in url:
            sssb.calls.append(url)
            page = json.loads(json.dumps(sssb.detail))
            return _wrap(page)
        return orig(url)

    rc = run(tmp_path, ENV, get=get, send=Phone(), sleep=Clock(), now=T0, init=True)
    assert rc == 1
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    conn.close()


# --- data safety ---
def test_missing_state_without_init_refuses(tmp_path, sssb):
    with pytest.raises(FileNotFoundError):
        run(tmp_path, ENV, get=sssb, send=Phone(), sleep=Clock(), now=T0, init=False)
    assert not (tmp_path / "ready").exists()


def test_init_refuses_existing_data(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    with pytest.raises(FileExistsError):
        go(tmp_path, sssb, T0 + timedelta(hours=1), init=True)


def test_wrong_key_stops_without_ready(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    env = {**ENV, "STATE_KEY": Fernet.generate_key().decode()}
    with pytest.raises(Exception):  # noqa: B017 - any error, as long as nothing is pushed
        go(tmp_path, sssb, T0 + timedelta(hours=1), env=env)
    assert not (tmp_path / "ready").exists()


def test_shrinking_history_aborts_without_ready(tmp_path, sssb, monkeypatch):
    go(tmp_path, sssb, T0)
    import housing_watch.run as r

    real = r._observations
    calls = iter([(10**6, 0)])
    monkeypatch.setattr(r, "_observations", lambda c: next(calls, None) or real(c))
    with pytest.raises(RuntimeError, match="shrank"):
        go(tmp_path, sssb, T0 + timedelta(hours=1))
    assert not (tmp_path / "ready").exists()


@pytest.mark.parametrize("missing", ["QUEUE_DAYS", "STATE_KEY"])
def test_missing_secret_stops_before_fetching(tmp_path, sssb, missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(Exception):  # noqa: B017
        go(tmp_path, sssb, T0, env=env)
    assert sssb.calls == []
    assert not (tmp_path / "ready").exists()


# --- quiet logs ---
def test_logs_say_nothing_private(tmp_path, sssb, capsys):
    go(tmp_path, sssb, T0)
    out = capsys.readouterr()
    text = out.out + out.err
    assert text.strip() == "ok: 24 listings"
    sssb.fail = OSError("made-up-topic 180 Winnable")
    go(tmp_path, sssb, T0 + timedelta(hours=1))
    out = capsys.readouterr()
    assert (out.out + out.err).strip() == "failed: OSError"


def test_main_prints_only_the_error_type(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUEUE_DAYS", "garbage 180")
    for k in ("QUEUE_DAYS_UNTIL", "STATE_KEY", "NTFY_TOPIC"):
        monkeypatch.setenv(k, ENV[k])
    rc = main(["--data", str(tmp_path), "--init"])
    out = capsys.readouterr()
    assert rc == 2
    assert (out.out + out.err).strip() == "error: ConfigError"


# --- test alert ---
def test_test_alert_goes_to_the_phone_and_touches_nothing(tmp_path, sssb):
    phone = Phone()
    rc = run(tmp_path, ENV, get=sssb, send=phone, sleep=Clock(), now=T0, test_alert=True)
    assert rc == 0
    assert [a.kind for a in phone.got] == ["test"]
    assert phone.got[0].priority == "high"
    assert sssb.calls == []
    assert list(tmp_path.iterdir()) == []


def test_digest_on_sunday_evening(tmp_path, sssb):
    go(tmp_path, sssb, T0)
    sunday = datetime.combine(date(2026, 11, 29), datetime.min.time(), STHLM).replace(
        hour=18, minute=10
    )
    _, phone, _ = go(tmp_path, sssb, sunday)
    assert "digest" in {a.kind for a in phone.got}


def test_state_size_does_not_change_when_alerts_are_logged(tmp_path, sssb):
    """M4 review: Fernet hides content, not length; padding hides that alerts were logged."""
    for item in sssb.items:
        item["inflyttningDatum"] = "2026-12-16"  # outside the window: no alerts
    _, phone, _ = go(tmp_path, sssb, T0)
    assert phone.got == []
    before = len((tmp_path / "state.db.enc").read_bytes())
    for item in sssb.items:  # 24 winnable corridor rooms: enough alerts to grow SQLite pages
        item["inflyttningDatum"] = IN_WINDOW
        item["typ"], item["hyra"], item["antalIntresse"] = "Rum i korridor", "3\xa0000", "9 (1st)"
    _, phone, _ = go(tmp_path, sssb, T0 + timedelta(minutes=30))
    assert len(phone.got) == 24
    assert len((tmp_path / "state.db.enc").read_bytes()) == before
