"""The alerts of PRD §4 (SSSB, BF, Förvaltaren), their wording, and dedupe via state.db (M3).

state.db holds the alert log and the heartbeat's failure streak. It reveals which listings I
could win (so my queue days) and BOOKED_IDS, so it is never committed in the clear (M4
encrypts it). The public history DB is only read here.

Sending is a callable (`send(alert)`), so M4 plugs in ntfy and tests plug in a fake.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from housing_watch.bf import BASE as BF_BASE
from housing_watch.bf import BfListing, bf_deadline
from housing_watch.config import Config, FvPoints, QueueDays
from housing_watch.fv import FvListing, fv_deadline
from housing_watch.fv import detail_url as fv_url
from housing_watch.history import (
    active,
    bf_active,
    bf_seen_since,
    fv_active,
    fv_seen_since,
    hs_seen_since,
    sbs_seen_since,
    seen_since,
)
from housing_watch.rules import (
    CHANCE,
    FV_CHANCE,
    BfStatus,
    BfVerdict,
    FvStatus,
    FvVerdict,
    Status,
    Verdict,
    bf_classify,
    bf_rank_key,
    classify,
    fv_classify,
    fv_rank_key,
    hs_classify,
    rank_key,
    sbs_classify,
)
from housing_watch.sssb import STOCKHOLM, Listing

GRACE = timedelta(minutes=30)  # a late GitHub run still gets the last call
DIGEST_CATCH_UP = timedelta(hours=24)
NTFY_BODY_MAX = 3900  # ntfy's message limit is 4096 bytes
ORDER = {
    "last_call": 0,
    "lead_lost": 1,
    "winnable": 2,
    "first_come": 2,
    "heartbeat": 3,
    "setup": 3,
    "digest": 4,
}

STATE_VERSION = 1
STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    listing_id TEXT,
    kind       TEXT NOT NULL,
    state_key  TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    message    TEXT NOT NULL
);
-- COALESCE: a plain UNIQUE treats NULLs as distinct, so heartbeat alerts would never dedupe.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_dedupe
    ON alerts (COALESCE(listing_id, ''), kind, state_key);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Alert:
    kind: str  # last_call | lead_lost | winnable | first_come | heartbeat | digest
    listing_id: str | None
    state_key: str  # with kind and listing_id: one alert per listing and state
    priority: str  # urgent | high | low
    title: str
    body: str
    url: str | None


@dataclass(frozen=True)
class Dispatched:
    sent: list[Alert]
    failed: list[tuple[Alert, Exception]]


def open_state(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > STATE_VERSION:
        conn.close()
        raise RuntimeError(f"state DB v{version} is newer than this code (v{STATE_VERSION})")
    if version == 0:
        conn.executescript(STATE_SCHEMA)
        conn.execute(f"PRAGMA user_version = {STATE_VERSION}")
    return conn


# --- wording ---
def _kr(n: int) -> str:
    return f"{n:,} kr"


def _when(dt: datetime) -> str:
    d = dt.astimezone(STOCKHOLM)
    return f"{d:%a} {d.day} {d:%b %H:%M}"


def _floor(n: int) -> str:
    return "ground floor" if n == 0 else f"floor {n}"


def _link(url: str) -> str:
    """SSSB's detaljUrl is http://; the site serves https, so the tap link uses that."""
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def _head(x: Listing) -> str:
    return f"{x.area}, {x.type}, {_kr(x.rent)}"


def _applicants(n: int) -> str:
    return f"{n} applicant" + ("" if n == 1 else "s")


def _marks(v: Verdict) -> str:
    m = [s for s, on in (("tied", v.status == Status.TIED), ("over budget", v.over_budget)) if on]
    return f" ({', '.join(m)})" if m else ""


def _left(delta: timedelta) -> str:
    h, m = divmod(int(delta.total_seconds() // 60), 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p) or "now"


def _winnable(v: Verdict, mine: int, dl: datetime | None) -> Alert:
    x = v.listing
    flags = f" ({', '.join(x.flags)})" if x.flags else ""
    if x.top_days is None:
        days = f"No bookings yet · you have {mine}"
    else:
        days = f"Top {x.top_days} vs your {mine} · {_applicants(x.applicants)}"
    lines = [
        f"{x.area} · {x.address}, {_floor(x.floor)}",
        f"{_kr(x.rent)}/month{flags} · move-in {x.move_in.isoformat()}",
        days,
    ]
    if dl:
        lines.append(f"Book by {_when(dl)}")
    return Alert(
        kind="winnable",
        listing_id=x.id,
        state_key=f"{v.status}@{x.published.isoformat()}",
        priority="high",
        title=f"Winnable{_marks(v)}: {_head(x)}",
        body="\n".join(lines),
        url=_link(x.detail_url),
    )


def _last_call(v: Verdict, mine: int, dl: datetime, now: datetime, cfg: Config) -> Alert:
    x = v.listing
    local, today = dl.astimezone(STOCKHOLM), now.astimezone(STOCKHOLM).date()
    day = "today" if local.date() == today else f"{local:%a} {local.day} {local:%b}"
    if x.top_days is None:
        lead = f"no bookings yet, you have {mine}"
    elif v.status == Status.TIED:
        lead = f"you are tied {mine} vs {x.top_days} · {_applicants(x.applicants)}"
    else:
        lead = f"you lead {mine} vs {x.top_days} · {_applicants(x.applicants)}"
    return Alert(
        kind="last_call",
        listing_id=x.id,
        state_key=dl.astimezone(UTC).isoformat(),
        priority="urgent",
        title=f"LAST CALL, closes {local:%H:%M}{_marks(v)}: {_head(x)}",
        body=f"Closes {local:%H:%M} {day} (in {_left(dl - now)}) · {lead}\n"
        f"{x.address}, {_floor(x.floor)} · move-in {x.move_in.isoformat()}",
        url=_link(x.detail_url),
    )


def _lead_lost(x: Listing, mine: int, dl: datetime | None) -> Alert:
    closes = f" · closes {_when(dl)}" if dl else ""
    return Alert(
        kind="lead_lost",
        listing_id=x.id,
        state_key=f"lost@{x.published.isoformat()}",
        priority="high",
        title=f"Lead lost: {x.area}, {x.address}",
        body=f"Top is now {x.top_days}, you have {mine} · {_applicants(x.applicants)}{closes}",
        url=_link(x.detail_url),
    )


# --- deciding ---
def _mine(queue: QueueDays, now: datetime) -> int:
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    return queue.on(now.astimezone(STOCKHOLM).date())


def listing_alerts(
    hist: sqlite3.Connection,
    cfg: Config,
    queue: QueueDays,
    booked: frozenset[str],
    now: datetime,
) -> list[Alert]:
    """Winnable, last call and lead lost for every active listing, urgent first, then by rank.

    Call this only after a successful run: on a failed run the DB still holds the previous
    state, which may be stale.
    """
    mine = _mine(queue, now)
    window = timedelta(hours=cfg.last_call_hours) + GRACE
    out: list[tuple[tuple, Alert]] = []
    for x, dl in active(hist):
        v = classify(x, cfg, mine)
        if x.id in booked:
            # My own booking makes me the top applicant; only someone with more days matters.
            if x.top_days is not None and x.top_days > mine:
                out.append((rank_key(v), _lead_lost(x, mine, dl)))
            continue
        if not (v.eligible and v.ahead):
            continue
        if dl and dl <= now:
            continue  # closed: SSSB may still list it, but booking is over (no false "winnable")
        if dl and dl - now <= window:
            out.append((rank_key(v), _last_call(v, mine, dl, now, cfg)))  # replaces "winnable"
        else:
            out.append((rank_key(v), _winnable(v, mine, dl)))
    out.sort(key=lambda p: (ORDER[p[1].kind], p[0]))
    return [a for _, a in out]


def _digest_moment(cfg: Config, now: datetime) -> datetime:
    """The most recent configured digest time (weekday + HH:MM, Stockholm) at or before now."""
    local = now.astimezone(STOCKHOLM)
    back = (local.weekday() - cfg.digest_weekday) % 7
    moment = datetime.combine(local.date() - timedelta(days=back), cfg.digest_time, STOCKHOLM)
    if moment > local:
        moment = datetime.combine(moment.date() - timedelta(days=7), cfg.digest_time, STOCKHOLM)
    return moment


def digest_alert(
    hist: sqlite3.Connection,
    cfg: Config,
    queue: QueueDays,
    booked: frozenset[str],
    now: datetime,
    bf_since: date | None = None,
    fv_points: FvPoints | None = None,
) -> Alert | None:
    """The weekly digest, due from the digest time until 24 h later (catch-up for late runs).

    BF listings in scope of a watched type get their own summary lines and listing lines
    (after SSSB's); without any, the digest is SSSB only."""
    moment = _digest_moment(cfg, now)
    if now.astimezone(UTC) - moment.astimezone(UTC) > DIGEST_CATCH_UP:
        return None
    since = datetime.combine(moment.date() - timedelta(days=7), cfg.digest_time, STOCKHOLM)
    mine = _mine(queue, now)
    rows = [(classify(x, cfg, mine), removed) for x, _, removed in seen_since(hist, since)]

    winning: dict[str, list[int]] = defaultdict(list)
    for v, removed in rows:
        if removed and v.category == "alert" and v.listing.top_days is not None:
            winning[v.listing.area].append(v.listing.top_days)
    ranges = [
        f"{a} {min(d)}–{max(d)}" if min(d) != max(d) else f"{a} {d[0]}"
        for a, d in sorted(winning.items(), key=lambda p: (cfg.tier(p[0]), p[0]))
    ]
    open_ahead = sum(
        1
        for v, removed in rows
        if not removed and v.eligible and v.ahead and v.listing.id not in booked
    )
    # Move-in before the window: listed (Simon, 2026-11-26). After it: counted only.
    shown = sorted(
        (p for p in rows if p[0].listing.move_in <= cfg.move_in_to),
        key=lambda p: (not p[0].in_window, rank_key(p[0])),
    )
    late = len(rows) - len(shown)

    lines = ["Winning days by area: " + (" · ".join(ranges) or "none closed this week")]
    if late:
        lines.append(f"{late} more after the move-in window")
    rest = [_digest_line(v, removed, booked, cfg) for v, removed in shown]
    fv_head, fv_lines, fv_title = _fv_digest(hist, cfg, _fv_mine(fv_points, now), since)
    sbs_head, sbs_lines, sbs_title = _sbs_digest(hist, cfg, since)
    hs_head, hs_lines, hs_title = _hs_digest(hist, cfg, since)
    bf_head, bf_lines, bf_title = _bf_digest(hist, cfg, bf_since, since)
    lines += fv_head + sbs_head + hs_head + bf_head
    rest += fv_lines + sbs_lines + hs_lines + bf_lines
    while rest:
        line = rest.pop(0)
        more = f"+{len(rest) + 1} more"
        if len("\n".join([*lines, line, more]).encode()) > NTFY_BODY_MAX:
            lines.append(more)
            break
        lines.append(line)
    week = moment.isocalendar()
    return Alert(
        kind="digest",
        listing_id=None,
        state_key=f"{week.year}-W{week.week:02d}",
        priority="low",
        title=f"Weekly digest, week {week.week}: {len(rows)} seen, {open_ahead} winnable"
        + fv_title
        + sbs_title
        + hs_title
        + bf_title,
        body="\n".join(lines),
        url=None,
    )


def _digest_line(v: Verdict, removed: datetime | None, booked: frozenset[str], cfg: Config):
    x = v.listing
    kind = f"other type: {x.type}" if v.category == "other" else x.type
    tags = [str(v.status)]
    if x.rent > cfg.rent_max and v.category != "digest":  # 2+ rooms: rent would be shared
        tags.append("over budget")
    if x.id in booked:
        tags.append("booked")
    if removed:
        tags.append("closed")
    if x.move_in < cfg.move_in_from:
        tags.append(f"early move-in {x.move_in.isoformat()}")
    top = x.top_days if x.top_days is not None else "none"
    return f"{x.area}, {kind}, {_kr(x.rent)} · top {top} · {', '.join(tags)}"


# --- Bostadsförmedlingen (M5 part B) ---
def _bf_kind(x: BfListing) -> str:
    return "Bostad snabbt" if x.bostad_snabbt else x.type


def _bf_head(x: BfListing) -> str:
    rent = _kr(x.rent) if x.rent is not None else "rent unknown"
    return f"{x.district}, {_bf_kind(x)}, {rent}"


def _bf_where(x: BfListing) -> str:
    return x.address + (f", {_floor(x.floor)}" if x.floor is not None else "")


def _bf_marks(v: BfVerdict) -> str:
    m = [
        s
        for s, on in (
            ("tied", v.status == BfStatus.TIED),
            ("no applicants shown", v.status == BfStatus.NO_APPLICANTS_SHOWN),
            ("over budget", v.over_budget),
        )
        if on
    ]
    return f" ({', '.join(m)})" if m else ""


def _day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def _bf_lines(v: BfVerdict, since: date) -> list[str]:
    x, d = v.listing, v.detail
    lines = [f"{x.district} · {_bf_where(x)}", f"{_kr(x.rent)}/month · move-in {d.move_in}"]
    if v.status == BfStatus.FIRST_COME:
        lines.append("Bostad snabbt: first come, queue time not needed · apply early")
    elif d.top_dates:
        lines.append(f"Top queue since {min(d.top_dates)} vs yours {since}")
    else:
        lines.append(f"No applicants shown · yours {since}")
    lines.append(f"Apply by {_day(x.deadline)}")
    return lines


def _bf_alert(v: BfVerdict, since: date, now: datetime, window: timedelta) -> Alert | None:
    x, d = v.listing, v.detail
    dl = bf_deadline(x.deadline)
    if dl <= now:
        return None
    url = BF_BASE + x.url
    lid = f"bf:{x.id}"
    if v.status == BfStatus.FIRST_COME:
        return Alert(
            kind="first_come",
            listing_id=lid,
            state_key=f"first@{x.advertised_from.isoformat()}",
            priority="high",
            title=f"BF · First come{_bf_marks(v)}: {_bf_head(x)}",
            body="\n".join(_bf_lines(v, since)),
            url=url,
        )
    if dl - now <= window:
        local, today = dl.astimezone(STOCKHOLM), now.astimezone(STOCKHOLM).date()
        day = "today" if local.date() == today else _day(local.date())
        if not d.top_dates:
            lead = f"no applicants shown, yours {since}"
        else:
            word = "tied" if v.status == BfStatus.TIED else "you lead"
            lead = f"{word}: yours {since} vs top {min(d.top_dates)}"
        return Alert(
            kind="last_call",
            listing_id=lid,
            state_key=dl.astimezone(UTC).isoformat(),
            priority="urgent",
            title=f"BF · LAST CALL, closes {local:%H:%M}{_bf_marks(v)}: {_bf_head(x)}",
            body=f"Closes {local:%H:%M} {day} (in {_left(dl - now)}) · {lead}\n"
            f"{_bf_where(x)} · move-in {d.move_in}",
            url=url,
        )
    if v.status == BfStatus.NO_APPLICANTS_SHOWN:
        return None  # last call only (Simon, 2026-11-26)
    return Alert(
        kind="winnable",
        listing_id=lid,
        state_key=f"{v.status}@{x.advertised_from.isoformat()}",
        priority="high",
        title=f"BF · Winnable{_bf_marks(v)}: {_bf_head(x)}",
        body="\n".join(_bf_lines(v, since)),
        url=url,
    )


def bf_listing_alerts(
    hist: sqlite3.Connection,
    cfg: Config,
    since: date | None,
    fresh: frozenset[str],
    now: datetime,
) -> list[Alert]:
    """BF winnable, last call and first come, urgent first, then by rank.

    Only listings whose detail page was fetched this run (`fresh`): the BF list has no top
    figure, so an older detail could be stale. No queue date (secret unset) → no alerts."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    if since is None:
        return []
    window = timedelta(hours=cfg.last_call_hours) + GRACE
    out: list[tuple[tuple, Alert]] = []
    for x, d in bf_active(hist):
        if x.id not in fresh or d is None:
            continue
        v = bf_classify(x, d, cfg, since)
        if not (v.eligible and v.status in CHANCE):
            continue
        a = _bf_alert(v, since, now, window)
        if a:
            out.append((bf_rank_key(v), a))
    out.sort(key=lambda p: (ORDER[p[1].kind], p[0]))
    return [a for _, a in out]


def _bf_digest(
    hist: sqlite3.Connection, cfg: Config, since: date | None, week_start: datetime
) -> tuple[list[str], list[str], str]:
    """(summary lines, listing lines, title suffix) for the digest; empty without BF rows."""
    rows = [
        (v, removed)
        for x, d, removed in bf_seen_since(hist, week_start)
        if (v := bf_classify(x, d, cfg, since)).tier is not None and v.category != "other"
    ]
    if not rows:
        return [], [], ""
    winning: dict[tuple[int, str], list[date]] = defaultdict(list)
    for v, removed in rows:
        if removed and v.category == "alert" and v.detail and v.detail.top_dates:
            winning[(v.tier, v.listing.district)].append(min(v.detail.top_dates))
    ranges = [
        f"{a} {min(d)}–{max(d)}" if min(d) != max(d) else f"{a} {d[0]}"
        for (_, a), d in sorted(winning.items())
    ]
    chance = {BfStatus.WINNABLE, BfStatus.TIED, BfStatus.FIRST_COME}
    open_chance = sum(1 for v, removed in rows if not removed and v.eligible and v.status in chance)
    shown = sorted(
        (p for p in rows if p[0].detail is None or p[0].detail.move_in <= cfg.move_in_to),
        key=lambda p: (not p[0].in_window, bf_rank_key(p[0])),
    )
    late = len(rows) - len(shown)
    head = [
        f"BF: {len(rows)} seen, {open_chance} winnable"
        + (f", {late} after the window" if late else "")
    ]
    head.append("BF winning dates: " + (" · ".join(ranges) or "none closed this week"))
    lines = [_bf_digest_line(v, removed, cfg) for v, removed in shown]
    return head, lines, f" · BF {len(rows)} seen, {open_chance} winnable"


def _bf_digest_line(v: BfVerdict, removed: datetime | None, cfg: Config) -> str:
    x, d = v.listing, v.detail
    tags = [str(v.status)]
    if x.rent is not None and x.rent > cfg.rent_max and v.category == "alert":
        tags.append("over budget")
    if removed:
        tags.append("closed")
    if d and d.move_in < cfg.move_in_from:
        tags.append(f"early move-in {d.move_in.isoformat()}")
    top = (min(d.top_dates).isoformat() if d.top_dates else "none") if d else "?"
    return f"BF {_bf_head(x)} · top {top} · {', '.join(tags)}"


# --- Förvaltaren (M6) ---
def _fv_mine(points: FvPoints | None, now: datetime) -> int | None:
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    return None if points is None else points.on(now.astimezone(STOCKHOLM).date())


def _fv_head(x: FvListing) -> str:
    return f"{x.area}, {x.type}, {_kr(x.rent)}"


def _fv_marks(v: FvVerdict) -> str:
    m = [
        s
        for s, on in (
            ("tied", v.status == FvStatus.TIED),
            ("no applicants shown", v.status == FvStatus.NO_APPLICANTS_SHOWN),
        )
        if on
    ]
    return f" ({', '.join(m)})" if m else ""


def _fv_applicants(v: FvVerdict) -> str:
    n = v.detail.applicants if v.detail.applicants is not None else v.listing.applicants
    return _applicants(n)


def _fv_alert(
    v: FvVerdict, mine: int, alerted: frozenset[tuple[str, str]], now: datetime, window: timedelta
) -> Alert | None:
    x, d = v.listing, v.detail
    dl = fv_deadline(x.deadline)
    if dl <= now:
        return None  # closed: no false "winnable" after the deadline
    lid, url = f"fv:{x.id}", fv_url(x)
    told = (lid, x.deadline.isoformat()) in alerted  # this ad, not an earlier one (M6 review)
    if v.status == FvStatus.BEHIND:
        if not told:
            return None
        return Alert(
            kind="lead_lost",
            listing_id=lid,
            state_key=f"lost@{x.deadline.isoformat()}",
            priority="high",
            title=f"FV · Lead lost: {x.area}, {x.address}",
            body=f"Top is now {d.top_points}, you have {mine} · {_fv_applicants(v)}"
            f" · closes {_when(dl)}",
            url=url,
        )
    if v.status not in FV_CHANCE:
        return None
    if dl - now <= window:
        local, today = dl.astimezone(STOCKHOLM), now.astimezone(STOCKHOLM).date()
        day = "today" if local.date() == today else _day(local.date())
        if d.top_points is None:
            lead = f"no applicants shown, you have {mine}"
        else:
            word = "tied" if v.status == FvStatus.TIED else "you lead"
            lead = f"{word} {mine} vs {d.top_points} · {_fv_applicants(v)}"
        return Alert(
            kind="last_call",
            listing_id=lid,
            state_key=f"last@{x.deadline.isoformat()}",
            priority="urgent",
            title=f"FV · LAST CALL, closes {local:%H:%M}{_fv_marks(v)}: {_fv_head(x)}",
            body=f"Closes {local:%H:%M} {day} (in {_left(dl - now)}) · {lead}\n"
            f"{x.address}, {_floor(x.floor)} · move-in {d.move_in.isoformat()}",
            url=url,
        )
    if v.status == FvStatus.NO_APPLICANTS_SHOWN:
        return None  # last call only (Simon, 2026-11-26)
    if v.status == FvStatus.TIED and told:
        return None  # after my own alert, a tie is most likely my own application
    return Alert(
        kind="winnable",
        listing_id=lid,
        state_key=f"{v.status}@{x.deadline.isoformat()}",
        priority="high",
        title=f"FV · Winnable{_fv_marks(v)}: {_fv_head(x)}",
        body="\n".join(
            [
                f"{x.area} · {x.address}, {_floor(x.floor)}",
                f"{_kr(x.rent)}/month · move-in {d.move_in.isoformat()}",
                f"Top {d.top_points} points vs your {mine} · {_fv_applicants(v)}",
                f"Apply by {_day(x.deadline)}",
            ]
        ),
        url=url,
    )


def fv_alerted(state: sqlite3.Connection) -> frozenset[tuple[str, str]]:
    """(listing id, deadline) of FV ads I was already told I could win (winnable or last call;
    both keys end in "@<deadline>"), from the alert log. A later "behind" on the same ad is a
    lost lead; a re-listing has a new deadline, so it starts fresh."""
    rows = state.execute(
        "SELECT DISTINCT listing_id, state_key FROM alerts "
        "WHERE listing_id LIKE 'fv:%' AND kind IN ('winnable', 'last_call')"
    ).fetchall()
    return frozenset((r[0], r[1].rpartition("@")[2]) for r in rows)


def fv_listing_alerts(
    hist: sqlite3.Connection,
    cfg: Config,
    points: FvPoints | None,
    fresh: frozenset[str],
    alerted: frozenset[tuple[str, str]],
    now: datetime,
) -> list[Alert]:
    """FV winnable, last call and lead lost, urgent first, then by rank.

    Only listings whose detail page was fetched this run (`fresh`): the top points exist only
    there. No points (secrets unset) → no alerts."""
    mine = _fv_mine(points, now)
    if mine is None:
        return []
    window = timedelta(hours=cfg.last_call_hours) + GRACE
    out: list[tuple[tuple, Alert]] = []
    for x, d in fv_active(hist):
        if x.id not in fresh or d is None:
            continue
        v = fv_classify(x, d, cfg, mine)
        if not v.eligible:
            continue
        if a := _fv_alert(v, mine, alerted, now, window):
            out.append((fv_rank_key(v), a))
    out.sort(key=lambda p: (ORDER[p[1].kind], p[0]))
    return [a for _, a in out]


def _fv_digest(
    hist: sqlite3.Connection, cfg: Config, mine: int | None, week_start: datetime
) -> tuple[list[str], list[str], str]:
    """(summary lines, listing lines, title suffix) for the digest; empty without FV rows."""
    rows = [
        (fv_classify(x, d, cfg, mine), removed) for x, d, removed in fv_seen_since(hist, week_start)
    ]
    if not rows:
        return [], [], ""
    winning: dict[str, list[int]] = defaultdict(list)
    for v, removed in rows:
        if removed and v.category == "alert" and v.detail and v.detail.top_points is not None:
            winning[v.listing.area].append(v.detail.top_points)
    ranges = [
        f"{a} {min(p)}–{max(p)}" if min(p) != max(p) else f"{a} {p[0]}"
        for a, p in sorted(winning.items())
    ]
    chance = {FvStatus.WINNABLE, FvStatus.TIED}
    open_chance = sum(1 for v, removed in rows if not removed and v.eligible and v.status in chance)
    # 1-room flats over budget are only counted (Simon, 2026-11-26); 2+ rooms stay listed.
    over = [p for p in rows if p[0].category != "digest" and p[0].listing.rent > cfg.rent_max]
    listed = [p for p in rows if p not in over]
    shown = sorted(
        (p for p in listed if p[0].detail is None or p[0].detail.move_in <= cfg.move_in_to),
        key=lambda p: (not p[0].in_window, p[0].category != "alert", fv_rank_key(p[0])),
    )
    late = len(listed) - len(shown)
    head = [
        f"FV: {len(rows)} seen, {open_chance} winnable"
        + (f", {late} after the window" if late else "")
        + (f", {len(over)} over budget (not listed)" if over else ""),
        "FV winning points: " + (" · ".join(ranges) or "none closed this week"),
    ]
    lines = [_fv_digest_line(v, removed, cfg) for v, removed in shown]
    return head, lines, f" · FV {len(rows)} seen, {open_chance} winnable"


def _fv_digest_line(v: FvVerdict, removed: datetime | None, cfg: Config) -> str:
    x, d = v.listing, v.detail
    kind = f"other type: {x.type}" if v.category == "other" else x.type
    tags = [str(v.status)]
    if x.rent > cfg.rent_max and v.category != "digest":  # 2+ rooms: rent would be shared
        tags.append("over budget")
    if x.special:
        tags.append(x.special)
    if removed:
        tags.append("closed")
    if d and d.move_in < cfg.move_in_from:
        tags.append(f"early move-in {d.move_in.isoformat()}")
    top = (d.top_points if d.top_points is not None else "none") if d else "?"
    return f"FV {x.area}, {kind}, {_kr(x.rent)} · top {top} · {', '.join(tags)}"


# --- List-only sources (M6: SBS, Heimstaden): digest only ---
_LIST_ORDER = {"in": 0, "agreement": 1, "early": 2}


def _list_digest(
    rows: list, week_start: datetime, head_name: str, title_name: str, line
) -> tuple[list[str], list[str], str]:
    """(summary lines, listing lines, title suffix) from (verdict, first_seen, removed_at)
    rows; empty without rows. Only watched rooms within rent and not after the window get a
    line; the rest are counted."""
    if not rows:
        return [], [], ""
    other = sum(1 for v, _, _ in rows if not v.watched)
    over = sum(1 for v, _, _ in rows if v.watched and v.over_budget)
    fit = [r for r in rows if r[0].watched and not r[0].over_budget]
    late = sum(1 for v, _, _ in fit if v.window == "late")
    shown = sorted(
        (r for r in fit if r[0].window != "late"),
        key=lambda r: (_LIST_ORDER[r[0].window], r[0].listing.rent, r[0].listing.id),
    )
    counted = [
        f"{late} after the window" if late else "",
        f"{over} over budget" if over else "",
        f"{other} other type" if other else "",
    ]
    counted = [c for c in counted if c]
    head = [
        f"{head_name}: {len(rows)} seen, {len(shown)} within rent"
        + (", " + ", ".join(counted) + " (not listed)" if counted else "")
    ]
    lines = [line(v, first >= week_start, removed) for v, first, removed in shown]
    return head, lines, f" · {title_name} {len(rows)} seen, {len(shown)} within rent"


def _list_tags(v, new: bool, removed: datetime | None, extra: Iterable[str]) -> str:
    tags = ["new"] if new else []
    if removed:
        tags.append("closed")
    tags += extra
    if v.window == "early":
        tags.append("early move-in")
    tags += v.listing.labels
    return " · " + ", ".join(tags) if tags else ""


def _sbs_digest(
    hist: sqlite3.Connection, cfg: Config, week_start: datetime
) -> tuple[list[str], list[str], str]:
    rows = [(sbs_classify(x, cfg), f, r) for x, f, r in sbs_seen_since(hist, week_start)]
    return _list_digest(rows, week_start, "SBS", "SBS", _sbs_digest_line)


def _sbs_digest_line(v, new: bool, removed: datetime | None) -> str:
    x = v.listing
    when = x.move_in.isoformat() if x.move_in else "by agreement"
    extra = ["short-term"] if v.short_term else []
    return f"SBS {x.address}, {x.rooms}, {_kr(x.rent)} · move-in {when}" + _list_tags(
        v, new, removed, extra
    )


def _hs_digest(
    hist: sqlite3.Connection, cfg: Config, week_start: datetime
) -> tuple[list[str], list[str], str]:
    rows = [(hs_classify(x, cfg), f, r) for x, f, r in hs_seen_since(hist, week_start)]
    return _list_digest(rows, week_start, "Heimstaden", "HS", _hs_digest_line)


def _hs_digest_line(v, new: bool, removed: datetime | None) -> str:
    x = v.listing
    when = x.move_in.isoformat() if x.move_in else "by agreement"
    extra = ["restricted"] if x.restricted else []
    return f"HS {x.location}, {x.address}, {x.rooms}, {_kr(x.rent)} · move-in {when}" + _list_tags(
        v, new, removed, extra
    )


# --- heartbeat (failure streak in state.db meta) ---
SOURCE_NAMES = {"bf": "BF", "fv": "FV", "sbs": "SBS", "hs": "HS"}


def _meta(state: sqlite3.Connection, key: str) -> str | None:
    row = state.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _keys(source: str) -> tuple[str, str, str]:
    p = f"{source}_" if source else ""
    return f"{p}fail_count", f"{p}fail_since", f"{p}last_error"


def run_failed(
    state: sqlite3.Connection, now: datetime, error: str, *, source: str = ""
) -> Alert | None:
    """Count a failed or 0-listing run; from the 3rd in a row, return the heartbeat alert.

    `source="bf"` keeps Bostadsförmedlingen's streak apart from SSSB's (M5)."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    k_count, k_since, k_error = _keys(source)
    count = int(_meta(state, k_count) or 0) + 1
    since = _meta(state, k_since) if count > 1 else None
    since = since or now.astimezone(UTC).isoformat()
    with state:
        state.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k_count, str(count)), (k_since, since), (k_error, error[:1000])],
        )
    if count < 3:
        return None
    start = datetime.fromisoformat(since).astimezone(STOCKHOLM)
    same_day = start.date() == now.astimezone(STOCKHOLM).date()
    at = f"{start:%H:%M}" if same_day else _when(start)
    return Alert(
        kind="heartbeat",
        listing_id=None,
        state_key=f"{source}@{since}" if source else since,
        priority="high",
        title=f"{SOURCE_NAMES.get(source, 'housing-watch')}: {count} failed runs since {at}",
        body=f"Last error: {error[:1000]}",
        url=None,
    )


def setup_note(source: str, error: str) -> Alert:
    """A bad or half-set secret for one source: that source is skipped until it is fixed.
    Sent once per distinct problem (the message never holds the secret's value)."""
    name = SOURCE_NAMES.get(source, "housing-watch")
    return Alert(
        kind="setup",
        listing_id=None,
        state_key=f"{source}:{error}",
        priority="high",
        title=f"{name}: setup problem, not watching",
        body=f"{error}. Fix the GitHub secret; {name} is skipped until then "
        "(the other sources still run).",
        url=None,
    )


def run_ok(state: sqlite3.Connection, *, source: str = "") -> None:
    """A good run ends the streak. Writes nothing if there was none."""
    keys = _keys(source)
    if _meta(state, keys[0]) is not None:
        with state:
            state.execute("DELETE FROM meta WHERE key IN (?, ?, ?)", keys)


# --- sending ---
def _seen(state: sqlite3.Connection, a: Alert) -> bool:
    return (
        state.execute(
            "SELECT 1 FROM alerts WHERE COALESCE(listing_id, '') = COALESCE(?, '') "
            "AND kind = ? AND state_key = ?",
            (a.listing_id, a.kind, a.state_key),
        ).fetchone()
        is not None
    )


def dispatch(
    state: sqlite3.Connection,
    alerts: Iterable[Alert | None],
    send: Callable[[Alert], object],
    now: datetime,
) -> Dispatched:
    """Send each alert not yet logged, then log it. A failed send is not logged, so the next
    run retries it (a duplicate is better than a miss). `None` entries are skipped."""
    sent: list[Alert] = []
    failed: list[tuple[Alert, Exception]] = []
    for a in alerts:
        if a is None or _seen(state, a):
            continue
        try:
            send(a)
        except Exception as e:  # noqa: BLE001 - any send failure means "retry next run"
            failed.append((a, e))
            continue
        with state:
            state.execute(
                "INSERT INTO alerts (listing_id, kind, state_key, sent_at, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    a.listing_id,
                    a.kind,
                    a.state_key,
                    now.astimezone(UTC).isoformat(),
                    f"{a.title}\n{a.body}",
                ),
            )
        sent.append(a)
    return Dispatched(sent=sent, failed=failed)
