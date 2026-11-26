"""M6: the SBS section of the weekly digest (digest only, no alerts)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housing_watch.alerts import digest_alert
from housing_watch.config import load_config, load_queue_days
from housing_watch.fv import parse_fv_list
from housing_watch.history import fv_record, open_db, sbs_record
from housing_watch.sbs import parse_sbs_page

FIX = Path(__file__).parent / "fixtures"
STHLM = ZoneInfo("Europe/Stockholm")
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
Q = load_queue_days({"QUEUE_DAYS": "180@2026-11-26", "QUEUE_DAYS_UNTIL": "2027-12-31"})
T0 = datetime(2026, 11, 26, 22, 25, tzinfo=UTC)  # Thursday
SUNDAY_18 = datetime(2026, 11, 29, 18, 0, tzinfo=STHLM)
NEK = "nekvagen-23-lgh-1403"  # 1 rum, 6,777 kr, move-in 1 jan. 2027
SHORT = "nekvagen-3-lgh-1103"  # Korttidkontrakt – Student, 1 rum, 8,576 kr
AGREE = "nekvagen-13-lgh-1103"  # 1 rum, 9,832 kr, Enligt överenskommelse, Möblerad + Kampanj
CORR = "nekvagen-1-lgh-1003-2"  # Korridor, 7,646 kr


def sbs0(when=T0):
    out = []
    for name in ("2026-11-26-lediga-stockholm.html", "2026-11-26-lediga-stockholm-page-2.html"):
        out += parse_sbs_page((FIX / "sbs" / name).read_text("utf-8"), when).listings
    return out


def hist(tmp_path, edits=None, when=T0):
    conn = open_db(tmp_path / "history.sqlite")
    edits = edits or {}
    sbs_record(conn, [replace(x, **edits.get(x.id, {})) for x in sbs0(when)], when)
    return conn


def sbs_lines(d):
    return [x for x in d.body.splitlines() if x.startswith("SBS")]


def test_digest_today_counts_everything_lists_nothing(tmp_path):
    d = digest_alert(hist(tmp_path), CFG, Q, frozenset(), SUNDAY_18)
    assert "SBS 16 seen, 0 within rent" in d.title
    assert sbs_lines(d) == [
        "SBS: 16 seen, 0 within rent, 13 over budget, 3 other type (not listed)"
    ]


def test_in_rent_listing_gets_a_line(tmp_path):
    conn = hist(tmp_path, {NEK: {"rent": 5900, "move_in": date(2027, 5, 1)}})
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    assert "SBS 16 seen, 1 within rent" in d.title
    assert "SBS: 16 seen, 1 within rent, 12 over budget, 3 other type (not listed)" in d.body
    assert "SBS Nekvägen 23 lgh 1403, 1 rum, 5,900 kr · move-in 2027-05-01 · new" in sbs_lines(d)


def test_line_tags(tmp_path):
    conn = hist(
        tmp_path,
        {
            NEK: {"rent": 5000, "move_in": date(2027, 1, 1)},
            SHORT: {"rent": 5100, "move_in": date(2027, 6, 1)},
            AGREE: {"rent": 5200},
            CORR: {"rent": 5300, "move_in": date(2027, 4, 1)},
        },
    )
    lines = sbs_lines(digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18))
    assert lines[1:] == [
        # In window first (by rent), then by agreement, then early.
        "SBS Nekvägen 3 lgh 1103, 1 rum, 5,100 kr · move-in 2027-06-01 · new, short-term, Kampanj",
        "SBS Nekvägen 1 lgh 1003, Korridor, 5,300 kr · move-in 2027-04-01 · new, Möblerad, Kampanj",
        "SBS Nekvägen 13 lgh 1103, 1 rum, 5,200 kr · move-in by agreement · new, Möblerad, Kampanj",
        "SBS Nekvägen 23 lgh 1403, 1 rum, 5,000 kr · move-in 2027-01-01 · new, early move-in",
    ]


def test_after_the_window_is_only_counted(tmp_path):
    conn = hist(tmp_path, {NEK: {"rent": 5900, "move_in": date(2027, 11, 15)}})
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    assert sbs_lines(d) == [
        "SBS: 16 seen, 0 within rent, 1 after the window, 12 over budget, 3 other type (not listed)"
    ]


def test_closed_and_not_new(tmp_path):
    # First seen in an earlier week, removed this week.
    early = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    conn = hist(tmp_path, {NEK: {"rent": 5900, "move_in": date(2027, 5, 1)}}, when=early)
    rest = [replace(x, seen_at=T0) for x in sbs0() if x.id != NEK]
    sbs_record(conn, rest, T0)
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    assert "SBS Nekvägen 23 lgh 1403, 1 rum, 5,900 kr · move-in 2027-05-01 · closed" in sbs_lines(d)


def test_removed_before_the_week_is_not_in_the_digest(tmp_path):
    early = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    conn = hist(tmp_path, when=early)
    gone = early + timedelta(days=1)
    sbs_record(conn, [replace(x, seen_at=gone) for x in sbs0() if x.id != NEK], gone)
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    assert "SBS 15 seen, 0 within rent" in d.title


def test_section_order_sssb_fv_sbs(tmp_path):
    conn = hist(tmp_path)
    body = (FIX / "forvaltaren" / "2026-11-26-objects-lagenhet.json").read_text("utf-8")
    fv_record(conn, parse_fv_list(body, T0), {}, T0)
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    lines = d.body.splitlines()
    fv_at = next(i for i, x in enumerate(lines) if x.startswith("FV winning points"))
    sbs_at = lines.index(sbs_lines(d)[0])
    assert fv_at < sbs_at
    assert d.title.index(" · FV ") < d.title.index(" · SBS ")


def test_no_sbs_rows_no_section(tmp_path):
    d = digest_alert(open_db(tmp_path / "history.sqlite"), CFG, Q, frozenset(), SUNDAY_18)
    assert "SBS" not in d.title and not sbs_lines(d)
