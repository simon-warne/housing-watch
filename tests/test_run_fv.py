"""M6: Förvaltaren in the scheduled run. Offline: a fake FV from the fixtures, made-up points."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housing_watch import run as runmod
from housing_watch.fv import LIST_URL
from housing_watch.run import PAUSE_S, run
from tests.test_run import ENV, Clock, FakeSSSB, Phone, state_rows
from tests.test_run_bf import FakeBF

FIX = Path(__file__).parent / "fixtures" / "forvaltaren"
STHLM = ZoneInfo("Europe/Stockholm")
T0 = datetime(2026, 11, 26, 19, 37, tzinfo=UTC)  # 20:37 Stockholm
RISSNE = "73-32-1-0102"  # 5,990 kr, closes today 23:59: in the last-call window at T0
DUVBO = "61-10-1-302"  # 4,290 kr, closes 30 Nov 23:59
ENV_FV = {**ENV, "FV_POINTS_AT": "3994@2026-11-20"}  # made up: 4,000 on 26 Nov
DUVBO_CLOSE = datetime(2026, 11, 30, 23, 59, tzinfo=STHLM)
BASE = "https://www.forvaltaren.se/objekt/"


class FakeFV:
    """Serves the list fixture and a detail page per flat, made from the Rissne page with that
    flat's id, deadline, move-in and top points."""

    def __init__(self, ids=None):
        data = json.loads((FIX / "2026-11-26-objects-lagenhet.json").read_text("utf-8"))
        self.items = [i for i in data["rentalObjects"] if ids is None or i["id"] in ids]
        self.page = (FIX / "2026-11-26-detail-73-32-1-0102.html").read_text("utf-8")
        self.move_in = "2027-05-01"
        self.top: dict[str, str | None] = {}  # id → "5 022"; None = no row
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if url == LIST_URL:
            return json.dumps({"rentalObjects": self.items}, ensure_ascii=False)
        lid = url.removeprefix(BASE)
        item = next(i for i in self.items if i["id"] == lid)
        p = self.page.replace(RISSNE, lid)
        p = p.replace("2026-11-26", item["showDateEnd"][:10])

        def fact(label, value, page):
            return re.sub(
                rf'(uppercase">{label}</span>\s*)[^<]*?(\s*</li>)',
                lambda m: m.group(1) + value + m.group(2),
                page,
            )

        p = fact("Tillträde", self.move_in, p)
        p = fact("Antal sökande", str(item["usercount"]), p)
        top = self.top.get(lid, "3 000")
        if top is None:
            p = p.replace('uppercase">Högst köpoäng</span>', 'uppercase">Övrigt</span>')
        else:
            p = fact("Högst köpoäng", top, p)
        return p

    def detail_calls(self):
        return [u.removeprefix(BASE) for u in self.calls if u.startswith(BASE)]


@pytest.fixture
def sssb():
    return FakeSSSB()


@pytest.fixture
def fv():
    return FakeFV()


def go(tmp_path, sssb, fv, now, *, env=ENV_FV, phone=None, sleep=None, bf=None):
    phone = phone if phone is not None else Phone()
    sleep = sleep if sleep is not None else Clock()
    init = not (tmp_path / "state.db.enc").exists()
    rc = run(
        tmp_path, env, get=sssb, bf_get=bf, fv_get=fv, send=phone, sleep=sleep, now=now, init=init
    )
    return rc, phone, sleep


def fv_ids(phone):
    return [(a.kind, a.listing_id) for a in phone.got if (a.listing_id or "").startswith("fv:")]


def test_first_run_fetches_list_and_only_worthwhile_details(tmp_path, sssb, fv, capsys):
    rc, phone, _ = go(tmp_path, sssb, fv, T0)
    assert rc == 0
    assert fv.calls[0] == LIST_URL and fv.detail_calls() == [RISSNE, DUVBO]  # urgent first
    assert fv_ids(phone) == [("last_call", "fv:" + RISSNE), ("winnable", "fv:" + DUVBO)]
    assert capsys.readouterr().out.strip() == "ok: 24 listings · fv ok: 17"
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM fv_listings").fetchone()[0] == 17
    conn.close()


def test_detail_once_then_every_run_near_close(tmp_path, sssb, fv):
    go(tmp_path, sssb, fv, T0)
    fv.calls.clear()
    rc, phone, _ = go(tmp_path, sssb, fv, T0 + timedelta(minutes=30))
    assert rc == 0 and fv.detail_calls() == [RISSNE] and fv_ids(phone) == []
    fv.calls.clear()
    go(tmp_path, sssb, fv, DUVBO_CLOSE - timedelta(hours=40))
    assert fv.detail_calls() == [DUVBO]  # Rissne is closed


def test_pause_before_every_fv_detail(tmp_path, sssb, fv):
    with_fv, without = Clock(), Clock()
    go(tmp_path / "a", sssb, fv, T0, sleep=with_fv)
    go(tmp_path / "b", FakeSSSB(), None, T0, sleep=without)
    assert with_fv.slept == without.slept + [PAUSE_S, PAUSE_S]  # one per FV detail, after SSSB


def test_lead_lost_end_to_end(tmp_path, sssb, fv):
    go(tmp_path, sssb, fv, T0)
    fv.top[DUVBO] = "4 500"
    _, phone, _ = go(tmp_path, sssb, fv, DUVBO_CLOSE - timedelta(hours=40))
    assert fv_ids(phone) == [("lead_lost", "fv:" + DUVBO)]


def test_no_top_points_row_last_call_only(tmp_path, sssb, fv):
    fv.top[DUVBO] = None
    next(i for i in fv.items if i["id"] == DUVBO)["usercount"] = 0
    _, phone, _ = go(tmp_path, sssb, fv, T0)
    assert ("winnable", "fv:" + DUVBO) not in fv_ids(phone)
    _, phone, _ = go(tmp_path, sssb, fv, DUVBO_CLOSE - timedelta(hours=4))
    assert fv_ids(phone) == [("last_call", "fv:" + DUVBO)]


def test_fv_failure_does_not_stop_sssb(tmp_path, sssb, fv, capsys):
    fv.fail = OSError("down")
    rc, phone, _ = go(tmp_path, sssb, fv, T0)
    assert rc == 1 and fv_ids(phone) == [] and len(phone.got) == 6
    assert capsys.readouterr().out.strip() == "ok: 24 listings · fv failed: OSError"
    for i in (1, 2):
        _, phone, _ = go(tmp_path, sssb, fv, T0 + timedelta(minutes=30 * i))
    assert [a.title.split(" failed")[0] for a in phone.got] == ["FV: 3"]
    assert state_rows(tmp_path, "SELECT value FROM meta WHERE key = 'fail_count'") == []


def test_sssb_and_bf_failures_do_not_stop_fv(tmp_path, sssb, fv, capsys):
    sssb.fail = OSError("down")
    bf = FakeBF()
    bf.fail = OSError("down")
    rc, phone, _ = go(tmp_path, sssb, fv, T0, bf=bf)
    assert rc == 1
    assert fv_ids(phone) == [("last_call", "fv:" + RISSNE), ("winnable", "fv:" + DUVBO)]
    assert capsys.readouterr().out.strip() == ("failed: OSError · bf failed: OSError · fv ok: 17")


def test_one_bad_detail_page_fails_fv_but_keeps_the_others(tmp_path, sssb):
    class OddPage(FakeFV):
        def __call__(self, url):
            page = super().__call__(url)
            return page.replace("2027-05-01", "enligt ök") if url.endswith(RISSNE) else page

    fv = OddPage()
    rc, phone, _ = go(tmp_path, sssb, fv, T0)
    assert rc == 1  # still a failed FV run, so the heartbeat counts it
    assert fv_ids(phone) == [("winnable", "fv:" + DUVBO)]
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    stored = conn.execute("SELECT listing_id FROM fv_observations WHERE move_in IS NOT NULL")
    assert stored.fetchall() == [(DUVBO,)]
    assert conn.execute("SELECT COUNT(*) FROM fv_observations").fetchone()[0] == 17
    conn.close()


def test_empty_fv_list_is_a_failed_run(tmp_path, sssb, fv, capsys):
    go(tmp_path, sssb, fv, T0)
    capsys.readouterr()
    fv.items = []
    rc, _, _ = go(tmp_path, sssb, fv, T0 + timedelta(minutes=30))
    assert rc == 1
    assert capsys.readouterr().out.strip() == "ok: 24 listings · fv failed: EmptyRun"
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM fv_listings WHERE removed_at IS NULL").fetchone()[0]
    conn.close()


def test_fv_guard(tmp_path, sssb, fv, capsys):
    go(tmp_path, sssb, fv, T0)
    capsys.readouterr()
    fv.calls.clear()
    go(tmp_path, sssb, fv, T0 + timedelta(minutes=10))
    assert fv.calls == []
    assert capsys.readouterr().out.strip() == "skipped: recent fetch · fv skipped: recent fetch"


def test_no_points_records_but_does_not_alert(tmp_path, sssb, fv):
    env = {k: v for k, v in ENV_FV.items() if not k.startswith("FV_")}
    rc, phone, _ = go(tmp_path, sssb, fv, T0, env=env)
    assert rc == 0 and fv_ids(phone) == [] and fv.detail_calls() == [RISSNE, DUVBO]


@pytest.mark.parametrize(
    "bad", [{"FV_POINTS_AT": "many"}, {"FV_POINTS_AT": "3994@2026-12-01"}, {"FV_POINTS_AT": "3994"}]
)
def test_bad_points_stop_only_fv_and_leave_a_note(tmp_path, sssb, fv, bad, capsys):
    """Simon, 2026-11-26: a bad FV secret stops FV's part only, with a note on the phone."""
    rc, phone, _ = go(tmp_path, sssb, fv, T0, env={**ENV_FV, **bad})
    assert rc == 0 and fv.calls == [] and sssb.calls  # SSSB still ran
    assert capsys.readouterr().out.strip() == "ok: 24 listings · fv failed: ConfigError"
    [note] = [a for a in phone.got if a.kind == "setup"]
    assert note.title == "FV: setup problem, not watching" and "FV_POINTS_AT" in note.body
    for v in bad.values():
        assert not v or v not in note.body  # never echoes the secret
    assert len(phone.got) == 7  # SSSB's 6 winnable + the note
    _, again, _ = go(tmp_path, sssb, fv, T0 + timedelta(minutes=30), env={**ENV_FV, **bad})
    assert [a for a in again.got if a.kind == "setup"] == []  # one note per problem
    _, later, _ = go(tmp_path, sssb, fv, T0 + timedelta(minutes=60), env={**ENV_FV, **bad})
    assert not any(a.kind == "heartbeat" for a in later.got)  # the note is enough


def test_digest_has_this_runs_fv(tmp_path, sssb, fv):
    sunday = datetime(2026, 11, 29, 18, 7, tzinfo=STHLM)
    _, phone, _ = go(tmp_path, sssb, fv, sunday)
    [d] = [a for a in phone.got if a.kind == "digest"]
    assert "FV 17 seen" in d.title and "FV:" in d.body


def _trace(tmp_path, env):
    sssb, fv = FakeSSSB(), FakeFV()
    fv.top[DUVBO] = "4 002"  # between the two made-up point values below
    calls, sizes, sent = [], [], []
    for t in (
        T0,
        T0 + timedelta(minutes=30),
        DUVBO_CLOSE - timedelta(hours=40),
        DUVBO_CLOSE - timedelta(hours=4),
    ):
        fv.calls.clear()
        _, phone, _ = go(tmp_path, sssb, fv, t, env=env)
        sent += fv_ids(phone)
        calls.append(list(fv.calls))
        sizes.append((tmp_path / "state.db.enc").stat().st_size)
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    rows = conn.execute("SELECT * FROM fv_observations ORDER BY listing_id, seen_at").fetchall()
    conn.close()
    return (calls, rows, sizes), sent


def test_public_traces_do_not_depend_on_fv_points(tmp_path):
    a, sent_a = _trace(
        tmp_path / "a", {**ENV_FV, "FV_POINTS_AT": "9000@2026-11-20"}
    )  # wins everything
    b, sent_b = _trace(tmp_path / "b", {**ENV_FV, "FV_POINTS_AT": "10@2026-11-20"})  # wins nothing
    assert sent_a and not sent_b  # the private outcome differs...
    assert a == b  # ...the public trace doesn't


def test_main_wires_the_fv_fetcher(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("NTFY_TOPIC", "made-up-topic")
    monkeypatch.setattr(runmod, "run", lambda *a, **k: seen.update(k) or 0)
    assert runmod.main(["--data", str(tmp_path)]) == 0
    assert seen["fv_get"] is runmod.fv_get


def test_shrinking_fv_history_aborts(tmp_path, sssb, fv, monkeypatch):
    go(tmp_path, sssb, fv, T0)
    real = runmod.fv_record

    def shrink(hist, *a, **k):
        out = real(hist, *a, **k)
        hist.execute("DELETE FROM fv_observations WHERE id = 1")
        hist.commit()
        return out

    monkeypatch.setattr(runmod, "fv_record", shrink)
    (tmp_path / "ready").unlink()
    with pytest.raises(RuntimeError, match="shrank"):
        go(tmp_path, sssb, fv, T0 + timedelta(minutes=30))
    assert not (tmp_path / "ready").exists()


def test_a_network_error_on_one_detail_page_keeps_the_others(tmp_path, sssb):
    """Review: a withdrawn flat (404) between list and detail used to drop every FV alert."""

    class Gone(FakeFV):
        def __call__(self, url):
            if url.endswith(RISSNE):
                self.calls.append(url)
                raise OSError("HTTP Error 404")
            return super().__call__(url)

    fv = Gone()
    rc, phone, _ = go(tmp_path, sssb, fv, T0)
    assert rc == 1 and fv_ids(phone) == [("winnable", "fv:" + DUVBO)]
