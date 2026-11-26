"""M5 part B: BF in the scheduled run. Offline: a fake BF from the fixtures, made-up dates."""

import html
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch import run as runmod
from housing_watch.bf import LIST_URL
from housing_watch.run import DETAIL_CAP, PAUSE_S, run
from tests.test_run import ENV, Clock, FakeSSSB, Phone, state_rows

FIX = Path(__file__).parent / "fixtures" / "bf"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 37, tzinfo=UTC)
CORRIDOR = "202619429"  # Norra Djurgården corridor, 6,213 kr, closes 2026-11-29
KISTA = "202514073"  # 6,461 kr in tier 2: not worth a detail
YOUTH = "202618022"  # Sätra, 7,810 kr: not worth a detail
SNABBT = "202601471"  # Huddinge: out of scope
SUBSET = [CORRIDOR, KISTA, YOUTH, SNABBT]
ENV_BF = {**ENV, "BF_QUEUE_SINCE": "2021-01-01"}  # made up
CLOSE = datetime(2026, 11, 29, 23, 59, tzinfo=STHLM)


class FakeBF:
    """Serves the list fixture (optionally a subset) and a detail page per listing, made from
    the corridor page with that listing's address, number, deadline, move-in and top dates."""

    def __init__(self, ids=SUBSET):
        allx = json.loads((FIX / "2026-11-26-alla-annonser.json").read_text("utf-8"))
        self.items = [i for i in allx if ids is None or str(i["LagenhetId"]) in ids]
        self.page = (FIX / "2026-11-26-detail-202619429-studentkorridor.html").read_text("utf-8")
        self.move_in = "2027-07-01"
        self.top: dict[str, list[str]] = {}
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if url == LIST_URL:
            return json.dumps(self.items, ensure_ascii=False)
        lid = re.fullmatch(r"https://bostad\.stockholm\.se/bostad/(\d+)/", url).group(1)
        item = next(i for i in self.items if str(i["LagenhetId"]) == lid)
        p = re.sub(
            r"(<h1[^>]*>)[^<]+", lambda m: m.group(1) + html.escape(item["Gatuadress"]), self.page
        )
        p = p.replace("59601333", item["Lagenhetsnummer"])
        p = re.sub(
            r'(Inflytt</span>\s*<span class="apartment-facts__heading">)[^<]*',
            lambda m: m.group(1) + self.move_in,
            p,
        )
        p = re.sub(
            r'(Anmälan senast</span>\s*</span>\s*<span class="apartment-facts__heading">)[^<]*',
            lambda m: m.group(1) + item["AnnonseradTill"],
            p,
        )
        top = self.top.get(lid, ["2021-11-27", "2026-06-04"])
        lis = "".join(f"<li>{d}</li>" for d in top)
        if top:
            p = re.sub(
                r"(kötider på denna bostad:</strong>\s*<ul[^>]*>).*?(</ul>)",
                lambda m: m.group(1) + lis + m.group(2),
                p,
                flags=re.DOTALL,
            )
        else:
            p = p.replace("Längsta anmälda kötider", "Inga anmälda")
        return p

    def detail_calls(self):
        return [u for u in self.calls if "/bostad/" in u]


@pytest.fixture
def sssb():
    return FakeSSSB()


@pytest.fixture
def bf():
    return FakeBF()


def go(tmp_path, sssb, bf, now, *, env=ENV_BF, phone=None, sleep=None):
    phone = phone if phone is not None else Phone()
    sleep = sleep if sleep is not None else Clock()
    init = not (tmp_path / "state.db.enc").exists()
    rc = run(tmp_path, env, get=sssb, bf_get=bf, send=phone, sleep=sleep, now=now, init=init)
    return rc, phone, sleep


def bf_ids(phone):
    return [(a.kind, a.listing_id) for a in phone.got if (a.listing_id or "").startswith("bf:")]


def test_first_run_fetches_list_and_only_worthwhile_details(tmp_path, sssb, bf, capsys):
    rc, phone, _ = go(tmp_path, sssb, bf, T0)
    assert rc == 0
    assert bf.calls == [LIST_URL, f"https://bostad.stockholm.se/bostad/{CORRIDOR}/"]
    assert bf_ids(phone) == [("winnable", "bf:" + CORRIDOR)]
    assert capsys.readouterr().out.strip() == "ok: 24 listings · bf ok: 4"
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM bf_listings").fetchone()[0] == 4
    conn.close()


def test_detail_once_then_every_run_near_close(tmp_path, sssb, bf):
    go(tmp_path, sssb, bf, T0)
    bf.calls.clear()
    rc, phone, _ = go(tmp_path, sssb, bf, T0 + timedelta(minutes=30))
    assert rc == 0 and bf.detail_calls() == [] and bf_ids(phone) == []
    for h in (40, 39.5):
        bf.calls.clear()
        go(tmp_path, sssb, bf, CLOSE - timedelta(hours=h))
        assert len(bf.detail_calls()) == 1


def test_last_call_near_close(tmp_path, sssb, bf):
    go(tmp_path, sssb, bf, T0)
    _, phone, _ = go(tmp_path, sssb, bf, CLOSE - timedelta(hours=4))
    assert bf_ids(phone) == [("last_call", "bf:" + CORRIDOR)]


def test_lead_gone_near_close_no_last_call(tmp_path, sssb, bf):
    """The live detail near close decides: someone earlier applied, so no false last call."""
    go(tmp_path, sssb, bf, T0)
    bf.top[CORRIDOR] = ["2019-03-01", "2021-11-27"]
    _, phone, _ = go(tmp_path, sssb, bf, CLOSE - timedelta(hours=4))
    assert bf_ids(phone) == []


def test_no_applicants_shown_last_call_only(tmp_path, sssb, bf):
    bf.top[CORRIDOR] = []
    _, phone, _ = go(tmp_path, sssb, bf, T0)
    assert bf_ids(phone) == []
    _, phone, _ = go(tmp_path, sssb, bf, CLOSE - timedelta(hours=4))
    [a] = [a for a in phone.got if a.listing_id == "bf:" + CORRIDOR]
    assert a.kind == "last_call" and "no applicants shown" in a.title


def test_pause_before_every_bf_detail(tmp_path, sssb):
    bf = FakeBF(ids=None)
    _, _, sleep = go(tmp_path, sssb, bf, T0)
    n = len(bf.detail_calls())
    assert n == DETAIL_CAP
    assert sleep.slept.count(PAUSE_S) >= n + DETAIL_CAP  # SSSB's 10 plus BF's 10


def test_every_worthwhile_listing_gets_a_detail_within_a_few_hours(tmp_path, sssb):
    """26 worth a detail in the fixture; 9 close tonight and are re-fetched every run (live
    top dates, last-call window first), the rest get their turn under the cap once those close."""
    bf = FakeBF(ids=None)
    total = set()
    for i in range(14):
        bf.calls.clear()
        go(tmp_path, sssb, bf, T0 + timedelta(minutes=30 * i))
        assert len(bf.detail_calls()) <= DETAIL_CAP
        total |= set(bf.detail_calls())
    assert len(total) == 26


def test_bf_failure_does_not_stop_sssb(tmp_path, sssb, bf, capsys):
    bf.fail = OSError("down")
    rc, phone, _ = go(tmp_path, sssb, bf, T0)
    assert rc == 1
    assert {a.kind for a in phone.got} == {"winnable"} and bf_ids(phone) == []
    assert len(phone.got) == 6  # SSSB's winnable ones (tests/test_run.py WINNABLE)
    assert capsys.readouterr().out.strip() == "ok: 24 listings · bf failed: OSError"
    for i in (1, 2):
        _, phone, _ = go(tmp_path, sssb, bf, T0 + timedelta(minutes=30 * i))
    assert [a.title.split(" failed")[0] for a in phone.got] == ["BF: 3"]
    assert state_rows(tmp_path, "SELECT value FROM meta WHERE key = 'fail_count'") == []


def test_sssb_failure_does_not_stop_bf(tmp_path, sssb, bf, capsys):
    sssb.fail = OSError("down")
    rc, phone, _ = go(tmp_path, sssb, bf, T0)
    assert rc == 1
    assert bf_ids(phone) == [("winnable", "bf:" + CORRIDOR)]
    assert capsys.readouterr().out.strip() == "failed: OSError · bf ok: 4"


def test_bf_detail_parse_error_fails_bf_only(tmp_path, sssb, bf):
    bf.move_in = "våren 2027"
    rc, phone, _ = go(tmp_path, sssb, bf, T0)
    assert rc == 1 and bf_ids(phone) == [] and len(phone.got) == 6
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM bf_observations").fetchone()[0] == 0
    conn.close()


def test_empty_bf_list_is_a_failed_run(tmp_path, sssb, bf, capsys):
    go(tmp_path, sssb, bf, T0)
    capsys.readouterr()
    bf.items = []
    rc, _, _ = go(tmp_path, sssb, bf, T0 + timedelta(minutes=30))
    assert rc == 1
    assert capsys.readouterr().out.strip() == "ok: 24 listings · bf failed: EmptyRun"
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert (
        conn.execute("SELECT COUNT(*) FROM bf_listings WHERE removed_at IS NULL").fetchone()[0] == 4
    )
    conn.close()


def test_bf_guard(tmp_path, sssb, bf, capsys):
    go(tmp_path, sssb, bf, T0)
    capsys.readouterr()
    bf.calls.clear()
    go(tmp_path, sssb, bf, T0 + timedelta(minutes=10))
    assert bf.calls == []
    assert capsys.readouterr().out.strip() == "skipped: recent fetch · bf skipped: recent fetch"


def test_no_queue_date_records_but_does_not_alert(tmp_path, sssb, bf):
    env = {k: v for k, v in ENV_BF.items() if k != "BF_QUEUE_SINCE"}
    rc, phone, _ = go(tmp_path, sssb, bf, T0, env=env)
    assert rc == 0 and bf_ids(phone) == [] and len(bf.detail_calls()) == 1


def test_bad_queue_date_stops_before_any_request(tmp_path, sssb, bf):
    with pytest.raises(Exception, match="BF_QUEUE_SINCE"):
        go(tmp_path, sssb, bf, T0, env={**ENV_BF, "BF_QUEUE_SINCE": "soon"})
    assert sssb.calls == [] and bf.calls == []


def _trace(tmp_path, env):
    sssb, bf = FakeSSSB(), FakeBF(ids=None)
    calls, sizes = [], []
    for t in (
        T0,
        T0 + timedelta(minutes=30),
        CLOSE - timedelta(hours=40),
        CLOSE - timedelta(hours=4),
    ):
        bf.calls.clear()
        go(tmp_path, sssb, bf, t, env=env)
        calls.append(list(bf.calls))
        sizes.append((tmp_path / "state.db.enc").stat().st_size)
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    rows = conn.execute("SELECT * FROM bf_observations ORDER BY listing_id, seen_at").fetchall()
    conn.close()
    return calls, rows, sizes


def test_public_traces_do_not_depend_on_bf_queue_date(tmp_path):
    a = _trace(tmp_path / "a", {**ENV_BF, "BF_QUEUE_SINCE": "2015-01-01"})  # wins everything
    b = _trace(tmp_path / "b", {**ENV_BF, "BF_QUEUE_SINCE": "2026-11-26"})  # wins nothing
    assert a == b


def test_main_wires_the_bf_fetcher(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("NTFY_TOPIC", "made-up-topic")
    monkeypatch.setattr(runmod, "run", lambda *a, **k: seen.update(k) or 0)
    assert runmod.main(["--data", str(tmp_path)]) == 0
    assert seen["bf_get"] is runmod.bf_get
