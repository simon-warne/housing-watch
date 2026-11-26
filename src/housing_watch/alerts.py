"""The five alerts of PRD §4, their wording, and dedupe through the private state.db (plan M3).

state.db holds the alert log and the heartbeat's failure streak. It reveals which listings I
could win (so my queue days) and BOOKED_IDS, so it is never committed in the clear (M4
encrypts it). The public history DB is only read here.

Sending is a callable (`send(alert)`), so M4 plugs in ntfy and tests plug in a fake.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from housing_watch.config import Config, QueueDays
from housing_watch.history import active, seen_since
from housing_watch.rules import Status, Verdict, classify, rank_key
from housing_watch.sssb import STOCKHOLM, Listing

GRACE = timedelta(minutes=30)  # a late GitHub run still gets the last call
DIGEST_CATCH_UP = timedelta(hours=24)
NTFY_BODY_MAX = 3900  # ntfy's message limit is 4096 bytes
ORDER = {"last_call": 0, "lead_lost": 1, "winnable": 2, "heartbeat": 3, "digest": 4}

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
    kind: str  # last_call | lead_lost | winnable | heartbeat | digest
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
) -> Alert | None:
    """The weekly digest, due from the digest time until 24 h later (catch-up for late runs)."""
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
    shown = sorted((p for p in rows if p[0].in_window), key=lambda p: rank_key(p[0]))
    outside = len(rows) - len(shown)

    lines = ["Winning days by area: " + (" · ".join(ranges) or "none closed this week")]
    if outside:
        lines.append(f"{outside} more outside the move-in window")
    rest = [_digest_line(v, removed, booked, cfg) for v, removed in shown]
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
        title=f"Weekly digest, week {week.week}: {len(rows)} seen, {open_ahead} winnable",
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
    top = x.top_days if x.top_days is not None else "none"
    return f"{x.area}, {kind}, {_kr(x.rent)} · top {top} · {', '.join(tags)}"


# --- heartbeat (failure streak in state.db meta) ---
def _meta(state: sqlite3.Connection, key: str) -> str | None:
    row = state.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def run_failed(state: sqlite3.Connection, now: datetime, error: str) -> Alert | None:
    """Count a failed or 0-listing run; from the 3rd in a row, return the heartbeat alert."""
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    count = int(_meta(state, "fail_count") or 0) + 1
    since = _meta(state, "fail_since") if count > 1 else None
    since = since or now.astimezone(UTC).isoformat()
    with state:
        state.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("fail_count", str(count)), ("fail_since", since), ("last_error", error[:1000])],
        )
    if count < 3:
        return None
    start = datetime.fromisoformat(since).astimezone(STOCKHOLM)
    same_day = start.date() == now.astimezone(STOCKHOLM).date()
    at = f"{start:%H:%M}" if same_day else _when(start)
    return Alert(
        kind="heartbeat",
        listing_id=None,
        state_key=since,
        priority="high",
        title=f"housing-watch: {count} failed runs since {at}",
        body=f"Last error: {error[:1000]}",
        url=None,
    )


def run_ok(state: sqlite3.Connection) -> None:
    """A good run ends the streak. Writes nothing if there was none."""
    if _meta(state, "fail_count") is not None:
        with state:
            state.execute(
                "DELETE FROM meta WHERE key IN ('fail_count', 'fail_since', 'last_error')"
            )


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
