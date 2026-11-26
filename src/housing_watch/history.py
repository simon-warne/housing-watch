"""SQLite history of SSSB listings: one observation row per distinct state (plan M2).

A listing's state is every `Listing` field except `seen_at`, plus the deadline from its detail
page. A run that sees no change writes nothing, so the DB file stays byte-identical.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from housing_watch.sssb import Detail, Listing

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id         TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES listings(id),
    seen_at    TEXT NOT NULL,
    area       TEXT NOT NULL,
    area_code  TEXT NOT NULL,
    address    TEXT NOT NULL,
    type       TEXT NOT NULL,
    size_m2    REAL NOT NULL,
    rent       INTEGER NOT NULL,
    floor      INTEGER NOT NULL,
    flags_json TEXT NOT NULL,
    move_in    TEXT NOT NULL,
    published  TEXT NOT NULL,
    top_days   INTEGER,
    applicants INTEGER NOT NULL,
    detail_url TEXT NOT NULL,
    deadline   TEXT
);
CREATE INDEX IF NOT EXISTS observations_by_listing ON observations (listing_id, id);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    listing_id TEXT REFERENCES listings(id),
    kind       TEXT NOT NULL,
    state_key  TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    message    TEXT NOT NULL,
    UNIQUE (listing_id, kind, state_key)
);
"""

# Columns compared to decide "changed" (everything but id, listing_id, seen_at).
STATE_COLS = (
    "area area_code address type size_m2 rent floor flags_json move_in published "
    "top_days applicants detail_url deadline"
).split()


@dataclass(frozen=True)
class Changes:
    new: list[Listing]
    changed: list[tuple[Listing, Listing]]  # (previous state, new state)
    removed: list[str]  # ids
    unchanged: int


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the history DB. Never drops or rewrites existing data."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"DB schema v{version} is newer than this code (v{SCHEMA_VERSION})")
    if version == 0:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"datetime must be tz-aware: {dt!r}")
    return dt.astimezone(UTC).isoformat()


def _state(x: Listing, deadline: str | None) -> dict:
    return {
        "area": x.area,
        "area_code": x.area_code,
        "address": x.address,
        "type": x.type,
        "size_m2": float(x.size_m2),
        "rent": x.rent,
        "floor": x.floor,
        "flags_json": json.dumps(list(x.flags), ensure_ascii=False),
        "move_in": x.move_in.isoformat(),
        "published": x.published.isoformat(),
        "top_days": x.top_days,
        "applicants": x.applicants,
        "detail_url": x.detail_url,
        "deadline": deadline,
    }


def _listing(row: sqlite3.Row) -> Listing:
    return Listing(
        id=row["listing_id"],
        area=row["area"],
        area_code=row["area_code"],
        address=row["address"],
        type=row["type"],
        size_m2=row["size_m2"],
        rent=row["rent"],
        floor=row["floor"],
        flags=tuple(json.loads(row["flags_json"])),
        move_in=date.fromisoformat(row["move_in"]),
        published=date.fromisoformat(row["published"]),
        top_days=row["top_days"],
        applicants=row["applicants"],
        detail_url=row["detail_url"],
        seen_at=datetime.fromisoformat(row["seen_at"]),
    )


def _latest_row(conn: sqlite3.Connection, id_: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM observations WHERE listing_id = ? ORDER BY id DESC LIMIT 1", (id_,)
    ).fetchone()


def latest(conn: sqlite3.Connection, id_: str) -> Listing | None:
    """The listing's latest stored state; `seen_at` is when that state was first seen."""
    row = _latest_row(conn, id_)
    return _listing(row) if row else None


def _insert(conn: sqlite3.Connection, id_: str, seen: str, state: dict) -> None:
    cols = ["listing_id", "seen_at", *STATE_COLS]
    marks = ", ".join("?" for _ in cols)
    values = [id_, seen, *(state[c] for c in STATE_COLS)]
    conn.execute(f"INSERT INTO observations ({', '.join(cols)}) VALUES ({marks})", values)


def record(
    conn: sqlite3.Connection,
    listings: Sequence[Listing],
    details: Mapping[str, Detail],
    seen_at: datetime,
) -> Changes:
    """Store one run's listings and return what changed since the previous run.

    `details` holds the detail pages fetched this run, by listing id; a listing without one
    keeps its last known deadline. An empty `listings` is treated as a failed run (PRD §4
    heartbeat): nothing is written or removed.
    """
    seen = _ts(seen_at)
    ids = [x.id for x in listings]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate listing ids")
    unknown = set(details) - set(ids)
    if unknown:
        raise ValueError(f"details for listings not in this run: {sorted(unknown)}")
    if not listings:
        return Changes(new=[], changed=[], removed=[], unchanged=0)

    new: list[Listing] = []
    changed: list[tuple[Listing, Listing]] = []
    unchanged = 0
    with conn:
        active = {r["id"] for r in conn.execute("SELECT id FROM listings WHERE removed_at IS NULL")}
        for x in listings:
            prev = _latest_row(conn, x.id)
            if x.id in details:
                deadline = _ts(details[x.id].deadline)
            else:
                deadline = prev["deadline"] if prev else None
            state = _state(x, deadline)
            if x.id not in active:
                conn.execute(
                    "INSERT INTO listings (id, first_seen) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET removed_at = NULL",
                    (x.id, seen),
                )
                _insert(conn, x.id, seen, state)
                new.append(x)
            elif prev is None:
                raise RuntimeError(f"active listing {x.id} has no observation")
            elif any(prev[c] != state[c] for c in STATE_COLS):
                _insert(conn, x.id, seen, state)
                changed.append((_listing(prev), x))
            else:
                unchanged += 1
        removed = sorted(active - set(ids))
        conn.executemany(
            "UPDATE listings SET removed_at = ? WHERE id = ?", [(seen, i) for i in removed]
        )
    return Changes(new=new, changed=changed, removed=removed, unchanged=unchanged)
