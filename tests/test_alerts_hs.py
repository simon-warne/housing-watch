"""M6: the Heimstaden section of the weekly digest (digest only, no alerts)."""

import gzip
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housing_watch.alerts import digest_alert
from housing_watch.bf import parse_bf_list
from housing_watch.config import load_config, load_queue_days
from housing_watch.heimstaden import parse_hs_page
from housing_watch.history import bf_record, hs_record, open_db, sbs_record
from housing_watch.sbs import parse_sbs_page

FIX = Path(__file__).parent / "fixtures"
STHLM = ZoneInfo("Europe/Stockholm")
CFG = load_config(Path(__file__).parent.parent / "config.yaml")
Q = load_queue_days({"QUEUE_DAYS": "180@2026-11-26", "QUEUE_DAYS_UNTIL": "2027-12-31"})
T0 = datetime(2026, 11, 26, 21, 5, tzinfo=UTC)  # Thursday
SUNDAY_18 = datetime(2026, 11, 29, 18, 0, tzinfo=STHLM)
TABY = "6933111-1005"  # 1 rum och kokvrå, 5,080 kr, move-in 1 Feb 2027
BAND = "6937101-1210"  # 1 rum och kök, 6,114 kr
PAGE = gzip.decompress((FIX / "heimstaden" / "2026-11-26-sok-lagenhet.html.gz").read_bytes())


def hs0(when=T0):
    return parse_hs_page(PAGE.decode(), when, "Stor-Stockholm")


def hist(tmp_path, edits=None, when=T0):
    conn = open_db(tmp_path / "history.sqlite")
    edits = edits or {}
    hs_record(conn, [replace(x, **edits.get(x.id, {})) for x in hs0(when)], when)
    return conn


def hs_lines(d):
    return [x for x in d.body.splitlines() if x.startswith(("HS ", "Heimstaden:"))]


def test_digest_today(tmp_path):
    d = digest_alert(hist(tmp_path), CFG, Q, frozenset(), SUNDAY_18)
    assert " · HS 20 seen, 1 within rent" in d.title
    assert hs_lines(d) == [
        "Heimstaden: 20 seen, 1 within rent, 2 over budget, 17 other type (not listed)",
        "HS Täby - Täby Kyrkby, Hagmovägen 1, 1 rum och kokvrå, 5,080 kr"
        " · move-in 2027-02-01 · new, early move-in",
    ]


def test_line_order_and_tags(tmp_path):
    conn = hist(
        tmp_path,
        {
            BAND: {"rent": 5500, "move_in": None, "restricted": True, "labels": ("Nyproduktion",)},
            "6937101-2110": {"rent": 5900, "move_in": date(2027, 6, 1)},
        },
    )
    assert hs_lines(digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18))[1:] == [
        # In window first (by rent), then by agreement, then early.
        "HS Stockholm - Bandhagen, Önskehemsgatan 43, 1 rum och kök, 5,900 kr"
        " · move-in 2027-06-01 · new",
        "HS Stockholm - Bandhagen, Önskehemsgatan 43, 1 rum och kök, 5,500 kr"
        " · move-in by agreement · new, restricted, Nyproduktion",
        "HS Täby - Täby Kyrkby, Hagmovägen 1, 1 rum och kokvrå, 5,080 kr"
        " · move-in 2027-02-01 · new, early move-in",
    ]


def test_after_the_window_is_only_counted(tmp_path):
    conn = hist(tmp_path, {TABY: {"move_in": date(2027, 11, 15)}})
    assert hs_lines(digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)) == [
        "Heimstaden: 20 seen, 0 within rent, 1 after the window, 2 over budget,"
        " 17 other type (not listed)"
    ]


def test_closed_and_not_new(tmp_path):
    early = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    conn = hist(tmp_path, when=early)
    hs_record(conn, [x for x in hs0() if x.id != TABY], T0)
    lines = hs_lines(digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18))
    assert lines[1] == (
        "HS Täby - Täby Kyrkby, Hagmovägen 1, 1 rum och kokvrå, 5,080 kr"
        " · move-in 2027-02-01 · closed, early move-in"
    )


def test_removed_before_the_week_is_not_in_the_digest(tmp_path):
    early = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    conn = hist(tmp_path, when=early)
    gone = early + timedelta(days=1)
    hs_record(conn, [x for x in hs0(gone) if x.id != TABY], gone)
    d = digest_alert(conn, CFG, Q, frozenset(), SUNDAY_18)
    assert " · HS 19 seen, 0 within rent" in d.title


def test_section_order_sbs_hs_bf(tmp_path):
    conn = hist(tmp_path)
    sbs = (FIX / "sbs" / "2026-11-26-lediga-stockholm.html").read_text("utf-8")
    sbs_record(conn, parse_sbs_page(sbs, T0).listings, T0)
    bf = (FIX / "bf" / "2026-11-26-alla-annonser.json").read_text("utf-8")
    bf_record(conn, parse_bf_list(bf, T0), {}, T0)
    d = digest_alert(
        conn, CFG, Q, frozenset(), SUNDAY_18, bf_since=date(2025, 1, 1), fv_points=None
    )
    lines = d.body.splitlines()
    sbs_at = next(i for i, x in enumerate(lines) if x.startswith("SBS:"))
    hs_at = lines.index(hs_lines(d)[0])
    bf_at = next(i for i, x in enumerate(lines) if x.startswith("BF"))
    assert sbs_at < hs_at < bf_at
    assert d.title.index(" · SBS ") < d.title.index(" · HS ") < d.title.index(" · BF ")


def test_no_hs_rows_no_section(tmp_path):
    d = digest_alert(open_db(tmp_path / "history.sqlite"), CFG, Q, frozenset(), SUNDAY_18)
    assert " · HS" not in d.title and not hs_lines(d)
