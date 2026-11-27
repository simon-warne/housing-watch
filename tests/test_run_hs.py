"""M6: Heimstaden in the scheduled run (digest only, own 6 h guard). Offline: the saved page."""

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch import run as runmod
from housing_watch.heimstaden import LIST_URL, allowed
from housing_watch.run import run
from tests.test_run import ENV, Clock, FakeSSSB, Phone, state_rows
from tests.test_run_bf import FakeBF
from tests.test_run_fv import FakeFV
from tests.test_run_sbs import FakeSBS

FIX = Path(__file__).parent / "fixtures" / "heimstaden"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 37, tzinfo=UTC)
SIX_H = timedelta(hours=6)
PAGE = gzip.decompress((FIX / "2026-11-26-sok-lagenhet.html.gz").read_bytes()).decode()


class FakeHS:
    def __init__(self):
        self.page = PAGE
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        return self.page


@pytest.fixture
def sssb():
    return FakeSSSB()


@pytest.fixture
def hs():
    return FakeHS()


def go(tmp_path, sssb, hs, now, *, env=ENV, bf=None, fv=None, sbs=None):
    phone = Phone()
    init = not (tmp_path / "state.db.enc").exists()
    rc = run(
        tmp_path,
        env,
        get=sssb,
        bf_get=bf,
        fv_get=fv,
        sbs_get=sbs,
        hs_get=hs,
        send=phone,
        sleep=Clock(),
        now=now,
        init=init,
    )
    return rc, phone


def count(tmp_path, sql):
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_first_run_reads_the_page_once_and_alerts_nothing(tmp_path, sssb, hs, capsys):
    rc, phone = go(tmp_path, sssb, hs, T0)
    assert rc == 0
    assert hs.calls == [LIST_URL] and allowed(LIST_URL)
    assert capsys.readouterr().out.strip() == "ok: 24 listings · hs ok: 20"
    assert len(phone.got) == 6  # SSSB's own alerts only: Heimstaden is digest only
    assert count(tmp_path, "SELECT COUNT(*) FROM hs_observations") == 20


def test_six_hour_guard(tmp_path, sssb, hs, capsys):
    go(tmp_path, sssb, hs, T0)
    capsys.readouterr()
    hs.calls.clear()
    go(tmp_path, sssb, hs, T0 + timedelta(minutes=30))
    assert hs.calls == []
    assert capsys.readouterr().out.strip() == "ok: 24 listings · hs skipped: recent fetch"
    go(tmp_path, sssb, hs, T0 + SIX_H - timedelta(minutes=10))
    assert hs.calls == []
    capsys.readouterr()
    go(tmp_path, sssb, hs, T0 + SIX_H)
    assert hs.calls == [LIST_URL]
    assert capsys.readouterr().out.strip().endswith("hs ok: 20")


def test_hs_failure_does_not_stop_the_others(tmp_path, sssb, hs, capsys):
    hs.fail = OSError("down")
    rc, phone = go(tmp_path, sssb, hs, T0, sbs=FakeSBS())
    assert rc == 1 and len(phone.got) == 6
    assert capsys.readouterr().out.strip() == ("ok: 24 listings · sbs ok: 16 · hs failed: OSError")
    # Retried next run; the heartbeat comes after three failures.
    for i in (1, 2):
        _, phone = go(tmp_path, sssb, hs, T0 + timedelta(minutes=30 * i))
    assert [a.title.split(" failed")[0] for a in phone.got if a.kind == "heartbeat"] == ["HS: 3"]
    assert state_rows(tmp_path, "SELECT value FROM meta WHERE key = 'fail_count'") == []


def test_other_failures_do_not_stop_hs(tmp_path, sssb, hs, capsys):
    sssb.fail = OSError("down")
    bf, fv, sbs = FakeBF(), FakeFV(), FakeSBS()
    bf.fail = fv.fail = sbs.fail = OSError("down")
    rc, _ = go(tmp_path, sssb, hs, T0, bf=bf, fv=fv, sbs=sbs)
    assert rc == 1
    assert capsys.readouterr().out.strip() == (
        "failed: OSError · bf failed: OSError · fv failed: OSError · sbs failed: OSError"
        " · hs ok: 20"
    )
    assert count(tmp_path, "SELECT COUNT(*) FROM hs_observations") == 20


def test_a_bad_page_records_nothing(tmp_path, sssb, hs, capsys):
    hs.page = PAGE.replace('"total_nr_of_objects":423', '"total_nr_of_objects":424')
    assert hs.page != PAGE
    rc, _ = go(tmp_path, sssb, hs, T0)
    assert rc == 1
    assert capsys.readouterr().out.strip() == "ok: 24 listings · hs failed: ParseError"
    assert count(tmp_path, "SELECT COUNT(*) FROM hs_observations") == 0


def test_digest_has_this_runs_hs(tmp_path, sssb, hs):
    sunday = datetime(2026, 11, 29, 18, 7, tzinfo=STHLM)
    _, phone = go(tmp_path, sssb, hs, sunday)
    [d] = [a for a in phone.got if a.kind == "digest"]
    assert " · HS 20 seen, 1 within rent" in d.title and "Heimstaden: 20 seen" in d.body


def test_without_hs_get_hs_is_left_out(tmp_path, sssb, capsys):
    rc = run(tmp_path, ENV, get=sssb, send=Phone(), sleep=Clock(), now=T0, init=True)
    assert rc == 0 and capsys.readouterr().out.strip() == "ok: 24 listings"


def test_hs_requests_do_not_depend_on_queue_days(tmp_path):
    def trace(path, env):
        hs, calls = FakeHS(), []
        for t in (T0, T0 + timedelta(minutes=30), T0 + SIX_H):
            hs.calls.clear()
            go(path, FakeSSSB(), hs, t, env=env)
            calls.append(list(hs.calls))
        conn = sqlite3.connect(path / "history.sqlite")
        rows = conn.execute("SELECT * FROM hs_observations ORDER BY id").fetchall()
        conn.close()
        return calls, rows

    a = trace(tmp_path / "a", {**ENV, "QUEUE_DAYS": "5@2026-11-01"})
    b = trace(tmp_path / "b", {**ENV, "QUEUE_DAYS": "900@2026-11-01"})
    assert a == b


def test_main_wires_the_hs_fetcher(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("NTFY_TOPIC", "made-up-topic")
    monkeypatch.setattr(runmod, "run", lambda *a, **k: seen.update(k) or 0)
    assert runmod.main(["--data", str(tmp_path)]) == 0
    assert seen["hs_get"] is runmod.hs_get


def test_shrinking_hs_history_aborts(tmp_path, sssb, hs, monkeypatch):
    go(tmp_path, sssb, hs, T0)
    real = runmod.hs_record

    def shrink(hist, *a, **k):
        out = real(hist, *a, **k)
        hist.execute("DELETE FROM hs_observations WHERE id = 1")
        hist.commit()
        return out

    monkeypatch.setattr(runmod, "hs_record", shrink)
    (tmp_path / "ready").unlink()
    with pytest.raises(RuntimeError, match="shrank"):
        go(tmp_path, sssb, hs, T0 + SIX_H)
    assert not (tmp_path / "ready").exists()


def test_a_failed_fetch_is_retried_next_run_but_not_sooner(tmp_path, sssb, hs):
    hs.fail = OSError("down")
    go(tmp_path, sssb, hs, T0)
    hs.calls.clear()
    go(tmp_path, sssb, hs, T0 + timedelta(minutes=10))  # the 25-min guard still holds
    assert hs.calls == []
    hs.fail = None
    go(tmp_path, sssb, hs, T0 + timedelta(minutes=30))
    assert hs.calls == [LIST_URL]
    hs.calls.clear()
    for m in (60, 90, 300):
        go(tmp_path, sssb, hs, T0 + timedelta(minutes=m))
    assert hs.calls == []
    go(tmp_path, sssb, hs, T0 + timedelta(minutes=30) + SIX_H)
    assert hs.calls == [LIST_URL]


def test_a_listing_gone_from_the_page_is_closed(tmp_path, sssb, hs):
    go(tmp_path, sssb, hs, T0)
    hs.page = PAGE.replace('"district_name":"Stor-Stockholm"', '"district_name":"Stor-Malmö"', 1)
    assert hs.page != PAGE
    go(tmp_path, sssb, hs, T0 + SIX_H)
    assert count(tmp_path, "SELECT COUNT(*) FROM hs_listings WHERE removed_at IS NULL") == 19
