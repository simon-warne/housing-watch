"""M6: SBS in the scheduled run (digest only, own 6 h guard). Offline: the saved SBS pages."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch import run as runmod
from housing_watch.run import PAUSE_S, run
from housing_watch.sbs import allowed, page_url
from tests.test_run import ENV, Clock, FakeSSSB, Phone, state_rows
from tests.test_run_bf import FakeBF
from tests.test_run_fv import FakeFV

FIX = Path(__file__).parent / "fixtures" / "sbs"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 37, tzinfo=UTC)
SIX_H = timedelta(hours=6)


class FakeSBS:
    def __init__(self):
        self.pages = {
            page_url(1): (FIX / "2026-11-26-lediga-stockholm.html").read_text("utf-8"),
            page_url(2): (FIX / "2026-11-26-lediga-stockholm-page-2.html").read_text("utf-8"),
        }
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        return self.pages[url]


@pytest.fixture
def sssb():
    return FakeSSSB()


@pytest.fixture
def sbs():
    return FakeSBS()


def go(tmp_path, sssb, sbs, now, *, env=ENV, bf=None, fv=None, sleep=None):
    phone, sleep = Phone(), sleep if sleep is not None else Clock()
    init = not (tmp_path / "state.db.enc").exists()
    rc = run(
        tmp_path,
        env,
        get=sssb,
        bf_get=bf,
        fv_get=fv,
        sbs_get=sbs,
        send=phone,
        sleep=sleep,
        now=now,
        init=init,
    )
    return rc, phone, sleep


def count(tmp_path, sql):
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_first_run_reads_both_pages_and_alerts_nothing(tmp_path, sssb, sbs, capsys):
    rc, phone, _ = go(tmp_path, sssb, sbs, T0)
    assert rc == 0
    assert sbs.calls == [page_url(1), page_url(2)]
    assert all(allowed(u) for u in sbs.calls)
    assert capsys.readouterr().out.strip() == "ok: 24 listings · sbs ok: 16"
    assert len(phone.got) == 6  # SSSB's own alerts only: SBS is digest only
    assert count(tmp_path, "SELECT COUNT(*) FROM sbs_observations") == 16


def test_pause_between_pages(tmp_path, sbs):
    class Logged:
        """One timeline of SBS requests and sleeps."""

        def __init__(self):
            self.log: list = []

        def get(self, url):
            self.log.append(url)
            return sbs(url)

        def sleep(self, s):
            self.log.append(s)

    t = Logged()
    sssb = FakeSSSB()
    go(tmp_path, sssb, t.get, T0, sleep=t.sleep)
    sbs_part = t.log[t.log.index(page_url(1)) :]
    assert sbs_part[:3] == [page_url(1), PAUSE_S, page_url(2)]


def test_six_hour_guard(tmp_path, sssb, sbs, capsys):
    go(tmp_path, sssb, sbs, T0)
    capsys.readouterr()
    sbs.calls.clear()
    go(tmp_path, sssb, sbs, T0 + timedelta(minutes=30))
    assert sbs.calls == []
    assert capsys.readouterr().out.strip() == "ok: 24 listings · sbs skipped: recent fetch"
    go(tmp_path, sssb, sbs, T0 + SIX_H - timedelta(minutes=10))
    assert sbs.calls == []
    capsys.readouterr()
    go(tmp_path, sssb, sbs, T0 + SIX_H)
    assert sbs.calls == [page_url(1), page_url(2)]
    assert capsys.readouterr().out.strip().endswith("sbs ok: 16")


def test_sbs_failure_does_not_stop_sssb(tmp_path, sssb, sbs, capsys):
    sbs.fail = OSError("down")
    rc, phone, _ = go(tmp_path, sssb, sbs, T0)
    assert rc == 0 and len(phone.got) == 6  # digest only: green (Simon, 2026-11-28)
    assert capsys.readouterr().out.strip() == "ok: 24 listings · sbs failed: OSError"
    # A failure is retried next run (Simon, 2026-11-27): the heartbeat comes within ~1 h.
    for i in (1, 2):
        _, phone, _ = go(tmp_path, sssb, sbs, T0 + timedelta(minutes=30 * i))
    assert [a.title.split(" failed")[0] for a in phone.got if a.kind == "heartbeat"] == ["SBS: 3"]
    assert state_rows(tmp_path, "SELECT value FROM meta WHERE key = 'fail_count'") == []


def test_other_failures_do_not_stop_sbs(tmp_path, sssb, sbs, capsys):
    sssb.fail = OSError("down")
    bf, fv = FakeBF(), FakeFV()
    bf.fail = fv.fail = OSError("down")
    rc, _, _ = go(tmp_path, sssb, sbs, T0, bf=bf, fv=fv)
    assert rc == 1
    assert capsys.readouterr().out.strip() == (
        "failed: OSError · bf failed: OSError · fv failed: OSError · sbs ok: 16"
    )
    assert count(tmp_path, "SELECT COUNT(*) FROM sbs_observations") == 16


def test_a_page_that_doesnt_line_up_records_nothing(tmp_path, sssb, sbs, capsys):
    sbs.pages[page_url(2)] = sbs.pages[page_url(2)].replace("av 16", "av 17")
    rc, _, _ = go(tmp_path, sssb, sbs, T0)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "ok: 24 listings · sbs failed: ParseError"
    assert count(tmp_path, "SELECT COUNT(*) FROM sbs_observations") == 0


def test_digest_has_this_runs_sbs(tmp_path, sssb, sbs):
    sunday = datetime(2026, 11, 29, 18, 7, tzinfo=STHLM)
    _, phone, _ = go(tmp_path, sssb, sbs, sunday)
    [d] = [a for a in phone.got if a.kind == "digest"]
    assert "SBS 16 seen, 0 within rent" in d.title and "SBS: 16 seen" in d.body


def test_without_sbs_get_sbs_is_left_out(tmp_path, sssb, capsys):
    rc = run(tmp_path, ENV, get=sssb, send=Phone(), sleep=Clock(), now=T0, init=True)
    assert rc == 0 and capsys.readouterr().out.strip() == "ok: 24 listings"


def test_sbs_requests_do_not_depend_on_queue_days(tmp_path):
    def trace(path, env):
        sbs, calls = FakeSBS(), []
        for t in (T0, T0 + timedelta(minutes=30), T0 + SIX_H):
            sbs.calls.clear()
            go(path, FakeSSSB(), sbs, t, env=env)
            calls.append(list(sbs.calls))
        conn = sqlite3.connect(path / "history.sqlite")
        rows = conn.execute("SELECT * FROM sbs_observations ORDER BY id").fetchall()
        conn.close()
        return calls, rows

    a = trace(tmp_path / "a", {**ENV, "QUEUE_DAYS": "5@2026-11-01"})
    b = trace(tmp_path / "b", {**ENV, "QUEUE_DAYS": "900@2026-11-01"})
    assert a == b


def test_main_wires_the_sbs_fetcher(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("NTFY_TOPIC", "made-up-topic")
    monkeypatch.setattr(runmod, "run", lambda *a, **k: seen.update(k) or 0)
    assert runmod.main(["--data", str(tmp_path)]) == 0
    assert seen["sbs_get"] is runmod.sbs_get


def test_shrinking_sbs_history_aborts(tmp_path, sssb, sbs, monkeypatch):
    go(tmp_path, sssb, sbs, T0)
    real = runmod.sbs_record

    def shrink(hist, *a, **k):
        out = real(hist, *a, **k)
        hist.execute("DELETE FROM sbs_observations WHERE id = 1")
        hist.commit()
        return out

    monkeypatch.setattr(runmod, "sbs_record", shrink)
    (tmp_path / "ready").unlink()
    with pytest.raises(RuntimeError, match="shrank"):
        go(tmp_path, sssb, sbs, T0 + SIX_H)
    assert not (tmp_path / "ready").exists()


def test_a_failed_fetch_is_retried_next_run_but_not_sooner(tmp_path, sssb, sbs):
    sbs.fail = OSError("down")
    go(tmp_path, sssb, sbs, T0)
    sbs.calls.clear()
    go(tmp_path, sssb, sbs, T0 + timedelta(minutes=10))  # the 25-min guard still holds
    assert sbs.calls == []
    sbs.fail = None
    go(tmp_path, sssb, sbs, T0 + timedelta(minutes=30))
    assert sbs.calls == [page_url(1), page_url(2)]
    sbs.calls.clear()
    # After a success, back to every 6 h.
    for m in (60, 90, 300):
        go(tmp_path, sssb, sbs, T0 + timedelta(minutes=m))
    assert sbs.calls == []
    go(tmp_path, sssb, sbs, T0 + timedelta(minutes=30) + SIX_H)
    assert sbs.calls == [page_url(1), page_url(2)]


def test_an_empty_list_closes_every_listing(tmp_path, sssb, sbs, capsys):
    go(tmp_path, sssb, sbs, T0)
    capsys.readouterr()
    empty = (FIX / "2026-11-27-lediga-stockholm-empty.html").read_text("utf-8")
    sbs.pages = {page_url(1): empty}
    rc, phone, _ = go(tmp_path, sssb, sbs, T0 + SIX_H)
    assert rc == 0 and not any(a.kind == "heartbeat" for a in phone.got)
    assert capsys.readouterr().out.strip().endswith("sbs ok: 0")
    assert count(tmp_path, "SELECT COUNT(*) FROM sbs_listings WHERE removed_at IS NULL") == 0
    assert count(tmp_path, "SELECT COUNT(*) FROM sbs_observations") == 16
