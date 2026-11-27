"""SQLite history of SSSB, BF, Förvaltaren and SBS listings: a row per distinct state (M2, M5, M6).

A listing's state is every `Listing` field except `seen_at`, plus the deadline from its detail
page. A run that sees no change writes nothing, so the DB file stays byte-identical.
This DB is committed to the public `data` branch: it holds only public listing data.
Schema v2 (M5) adds the `bf_*` tables, v3 (M6) the `fv_*` tables (Förvaltaren), v4 the
`sbs_*` tables (SBS); an older DB gains them in place, existing rows untouched.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from housing_watch.bf import BfDetail, BfListing
from housing_watch.fv import FvDetail, FvListing
from housing_watch.sbs import SbsListing
from housing_watch.sssb import Detail, Listing

SCHEMA_VERSION = 4

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
"""
# No alerts here: this DB is public (data branch). Alerts live in the private state.db (M3).

# v2 (M5): Bostadsförmedlingen. move_in and top_dates_json stay NULL until a detail page is
# fetched; top_dates_json "[]" = fetched, no top-3 block shown.
BF_SCHEMA = """
CREATE TABLE IF NOT EXISTS bf_listings (
    id         TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS bf_observations (
    id              INTEGER PRIMARY KEY,
    listing_id      TEXT NOT NULL REFERENCES bf_listings(id),
    seen_at         TEXT NOT NULL,
    apartment_no    TEXT,
    district        TEXT NOT NULL,
    municipality    TEXT NOT NULL,
    address         TEXT NOT NULL,
    type            TEXT NOT NULL,
    rooms           REAL NOT NULL,
    size_m2         REAL,
    rent            INTEGER,
    floor           INTEGER,
    units           INTEGER NOT NULL,
    bostad_snabbt   INTEGER NOT NULL,
    advertised_from TEXT NOT NULL,
    deadline        TEXT NOT NULL,
    url             TEXT NOT NULL,
    move_in         TEXT,
    top_dates_json  TEXT
);
CREATE INDEX IF NOT EXISTS bf_observations_by_listing ON bf_observations (listing_id, id);
"""

FV_SCHEMA = """
CREATE TABLE IF NOT EXISTS fv_listings (
    id         TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS fv_observations (
    id                INTEGER PRIMARY KEY,
    listing_id        TEXT NOT NULL REFERENCES fv_listings(id),
    seen_at           TEXT NOT NULL,
    area              TEXT NOT NULL,
    address           TEXT NOT NULL,
    municipality      TEXT NOT NULL,
    type              TEXT NOT NULL,
    rooms             INTEGER NOT NULL,
    size_m2           INTEGER NOT NULL,
    rent              INTEGER NOT NULL,
    floor             INTEGER NOT NULL,
    applicants        INTEGER NOT NULL,
    deadline          TEXT NOT NULL,
    special           TEXT NOT NULL,
    move_in           TEXT,
    top_points        INTEGER,
    detail_applicants INTEGER
);
CREATE INDEX IF NOT EXISTS fv_observations_by_listing ON fv_observations (listing_id, id);
"""
SBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sbs_listings (
    id         TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS sbs_observations (
    id          INTEGER PRIMARY KEY,
    listing_id  TEXT NOT NULL REFERENCES sbs_listings(id),
    seen_at     TEXT NOT NULL,
    tag         TEXT NOT NULL,
    city        TEXT NOT NULL,
    address     TEXT NOT NULL,
    move_in     TEXT,
    rent        INTEGER NOT NULL,
    size_m2     INTEGER,
    rooms       TEXT NOT NULL,
    labels_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sbs_observations_by_listing ON sbs_observations (listing_id, id);
"""
# Applied in order from the DB's version: v0 → all, v1 → BF + FV + SBS, v2 → FV + SBS, v3 → SBS.
MIGRATIONS = (SCHEMA, BF_SCHEMA, FV_SCHEMA, SBS_SCHEMA)

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
    if version < SCHEMA_VERSION:
        conn.executescript("".join(MIGRATIONS[version:]))
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


def deadline(conn: sqlite3.Connection, id_: str) -> datetime | None:
    """The listing's latest known booking deadline (UTC), or None if never fetched."""
    row = _latest_row(conn, id_)
    return datetime.fromisoformat(row["deadline"]) if row and row["deadline"] else None


def _active_rows(conn: sqlite3.Connection, where: str, args: tuple) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.*, l.removed_at FROM listings l JOIN observations o ON o.id = "
        "(SELECT MAX(id) FROM observations WHERE listing_id = l.id) "
        f"WHERE {where} ORDER BY l.id",
        args,
    ).fetchall()


def _dl(row: sqlite3.Row) -> datetime | None:
    return datetime.fromisoformat(row["deadline"]) if row["deadline"] else None


def active(conn: sqlite3.Connection) -> list[tuple[Listing, datetime | None]]:
    """Every listing not removed, in its latest state, with its known deadline."""
    return [(_listing(r), _dl(r)) for r in _active_rows(conn, "l.removed_at IS NULL", ())]


def seen_since(
    conn: sqlite3.Connection, since: datetime
) -> list[tuple[Listing, datetime | None, datetime | None]]:
    """Listings on SSSB at any point since `since`: (latest state, deadline, removed_at).

    A removed listing's latest state is its last one before removal (its winning days).
    """
    rows = _active_rows(conn, "l.removed_at IS NULL OR l.removed_at >= ?", (_ts(since),))
    return [
        (_listing(r), _dl(r), datetime.fromisoformat(r["removed_at"]) if r["removed_at"] else None)
        for r in rows
    ]


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
    keeps its last known deadline (unless it was removed in between: a re-listing starts with
    no deadline). An empty `listings` is treated as a failed run (PRD §4 heartbeat): nothing
    is written or removed. Every listing's `seen_at` must equal `seen_at`.
    """
    seen = _ts(seen_at)
    ids = [x.id for x in listings]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate listing ids")
    if any(x.seen_at != seen_at for x in listings):
        raise ValueError("listing seen_at differs from the run's seen_at")
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
                dl = _ts(details[x.id].deadline)
            elif x.id in active and prev is not None:
                dl = prev["deadline"]
            else:
                dl = None
            state = _state(x, dl)
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


# --- Bostadsförmedlingen (M5) ---
BF_STATE_COLS = (
    "apartment_no district municipality address type rooms size_m2 rent floor units "
    "bostad_snabbt advertised_from deadline url move_in top_dates_json"
).split()


@dataclass(frozen=True)
class BfChanges:
    new: list[BfListing]
    changed: list[tuple[BfListing, BfListing]]
    removed: list[str]
    unchanged: int


def _bf_detail_cols(d: BfDetail | None) -> dict:
    if d is None:
        return {"move_in": None, "top_dates_json": None}
    return {
        "move_in": d.move_in.isoformat(),
        "top_dates_json": json.dumps([x.isoformat() for x in d.top_dates]),
    }


def _bf_state(x: BfListing, detail_cols: dict) -> dict:
    return {
        "apartment_no": x.apartment_no,
        "district": x.district,
        "municipality": x.municipality,
        "address": x.address,
        "type": x.type,
        "rooms": float(x.rooms),
        "size_m2": None if x.size_m2 is None else float(x.size_m2),
        "rent": x.rent,
        "floor": x.floor,
        "units": x.units,
        "bostad_snabbt": int(x.bostad_snabbt),
        "advertised_from": x.advertised_from.isoformat(),
        "deadline": x.deadline.isoformat(),
        "url": x.url,
        **detail_cols,
    }


def _bf_listing(row: sqlite3.Row) -> BfListing:
    return BfListing(
        id=row["listing_id"],
        apartment_no=row["apartment_no"],
        district=row["district"],
        municipality=row["municipality"],
        address=row["address"],
        type=row["type"],
        rooms=row["rooms"],
        size_m2=row["size_m2"],
        rent=row["rent"],
        floor=row["floor"],
        units=row["units"],
        bostad_snabbt=bool(row["bostad_snabbt"]),
        advertised_from=date.fromisoformat(row["advertised_from"]),
        deadline=date.fromisoformat(row["deadline"]),
        url=row["url"],
        seen_at=datetime.fromisoformat(row["seen_at"]),
    )


def _bf_det(row: sqlite3.Row) -> BfDetail | None:
    if row["move_in"] is None:
        return None
    return BfDetail(
        move_in=date.fromisoformat(row["move_in"]),
        top_dates=tuple(date.fromisoformat(x) for x in json.loads(row["top_dates_json"])),
    )


def _bf_latest_row(conn: sqlite3.Connection, id_: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bf_observations WHERE listing_id = ? ORDER BY id DESC LIMIT 1", (id_,)
    ).fetchone()


def bf_detail(conn: sqlite3.Connection, id_: str) -> BfDetail | None:
    """The current listing's latest detail, or None if never fetched or the listing is not
    active (a re-listing must not reuse the old listing's move-in or dates; M5 review)."""
    rows = _bf_rows(conn, "l.id = ? AND l.removed_at IS NULL", (id_,))
    return _bf_det(rows[0]) if rows else None


def _bf_rows(conn: sqlite3.Connection, where: str, args: tuple) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.*, l.removed_at FROM bf_listings l JOIN bf_observations o ON o.id = "
        "(SELECT MAX(id) FROM bf_observations WHERE listing_id = l.id) "
        f"WHERE {where} ORDER BY l.id",
        args,
    ).fetchall()


def bf_active(conn: sqlite3.Connection) -> list[tuple[BfListing, BfDetail | None]]:
    return [(_bf_listing(r), _bf_det(r)) for r in _bf_rows(conn, "l.removed_at IS NULL", ())]


def bf_seen_since(
    conn: sqlite3.Connection, since: datetime
) -> list[tuple[BfListing, BfDetail | None, datetime | None]]:
    rows = _bf_rows(conn, "l.removed_at IS NULL OR l.removed_at >= ?", (_ts(since),))
    return [
        (
            _bf_listing(r),
            _bf_det(r),
            datetime.fromisoformat(r["removed_at"]) if r["removed_at"] else None,
        )
        for r in rows
    ]


def bf_record(
    conn: sqlite3.Connection,
    listings: Sequence[BfListing],
    details: Mapping[str, BfDetail],
    seen_at: datetime,
) -> BfChanges:
    """Same rules as `record`: a row only on change, an empty run writes nothing, a re-listed
    listing starts with no detail; a listing without a detail this run keeps its last one."""
    seen = _ts(seen_at)
    ids = [x.id for x in listings]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate listing ids")
    if any(x.seen_at != seen_at for x in listings):
        raise ValueError("listing seen_at differs from the run's seen_at")
    unknown = set(details) - set(ids)
    if unknown:
        raise ValueError(f"details for listings not in this run: {sorted(unknown)}")
    if not listings:
        return BfChanges(new=[], changed=[], removed=[], unchanged=0)

    new: list[BfListing] = []
    changed: list[tuple[BfListing, BfListing]] = []
    unchanged = 0
    cols = ["listing_id", "seen_at", *BF_STATE_COLS]
    insert = (
        f"INSERT INTO bf_observations ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    )
    with conn:
        act = {r["id"] for r in conn.execute("SELECT id FROM bf_listings WHERE removed_at IS NULL")}
        for x in listings:
            prev = _bf_latest_row(conn, x.id)
            if x.id in details:
                dcols = _bf_detail_cols(details[x.id])
            elif x.id in act and prev is not None:
                dcols = {"move_in": prev["move_in"], "top_dates_json": prev["top_dates_json"]}
            else:
                dcols = _bf_detail_cols(None)
            state = _bf_state(x, dcols)
            values = [x.id, seen, *(state[c] for c in BF_STATE_COLS)]
            if x.id not in act:
                conn.execute(
                    "INSERT INTO bf_listings (id, first_seen) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET removed_at = NULL",
                    (x.id, seen),
                )
                conn.execute(insert, values)
                new.append(x)
            elif prev is None:
                raise RuntimeError(f"active BF listing {x.id} has no observation")
            elif any(prev[c] != state[c] for c in BF_STATE_COLS):
                conn.execute(insert, values)
                changed.append((_bf_listing(prev), x))
            else:
                unchanged += 1
        removed = sorted(act - set(ids))
        conn.executemany(
            "UPDATE bf_listings SET removed_at = ? WHERE id = ?", [(seen, i) for i in removed]
        )
    return BfChanges(new=new, changed=changed, removed=removed, unchanged=unchanged)


# --- Förvaltaren (M6) ---
FV_DETAIL_COLS = ("move_in", "top_points", "detail_applicants")
FV_STATE_COLS = (
    "area address municipality type rooms size_m2 rent floor applicants deadline special".split()
    + list(FV_DETAIL_COLS)
)


@dataclass(frozen=True)
class FvChanges:
    new: list[FvListing]
    changed: list[tuple[FvListing, FvListing]]
    removed: list[str]
    unchanged: int


def _fv_detail_cols(d: FvDetail | None) -> dict:
    if d is None:
        return dict.fromkeys(FV_DETAIL_COLS)
    return {
        "move_in": d.move_in.isoformat(),
        "top_points": d.top_points,
        "detail_applicants": d.applicants,
    }


def _fv_state(x: FvListing, detail_cols: dict) -> dict:
    return {
        "area": x.area,
        "address": x.address,
        "municipality": x.municipality,
        "type": x.type,
        "rooms": x.rooms,
        "size_m2": x.size_m2,
        "rent": x.rent,
        "floor": x.floor,
        "applicants": x.applicants,
        "deadline": x.deadline.isoformat(),
        "special": x.special,
        **detail_cols,
    }


def _fv_listing(row: sqlite3.Row) -> FvListing:
    return FvListing(
        id=row["listing_id"],
        area=row["area"],
        address=row["address"],
        municipality=row["municipality"],
        type=row["type"],
        rooms=row["rooms"],
        size_m2=row["size_m2"],
        rent=row["rent"],
        floor=row["floor"],
        applicants=row["applicants"],
        deadline=date.fromisoformat(row["deadline"]),
        special=row["special"],
        seen_at=datetime.fromisoformat(row["seen_at"]),
    )


def _fv_det(row: sqlite3.Row) -> FvDetail | None:
    if row["move_in"] is None:
        return None
    return FvDetail(
        move_in=date.fromisoformat(row["move_in"]),
        top_points=row["top_points"],
        applicants=row["detail_applicants"],
    )


def _fv_rows(conn: sqlite3.Connection, where: str, args: tuple) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.*, l.removed_at FROM fv_listings l JOIN fv_observations o ON o.id = "
        "(SELECT MAX(id) FROM fv_observations WHERE listing_id = l.id) "
        f"WHERE {where} ORDER BY l.id",
        args,
    ).fetchall()


def fv_detail(conn: sqlite3.Connection, id_: str) -> FvDetail | None:
    """The current listing's latest detail; None if never fetched or not active."""
    rows = _fv_rows(conn, "l.id = ? AND l.removed_at IS NULL", (id_,))
    return _fv_det(rows[0]) if rows else None


def fv_active(conn: sqlite3.Connection) -> list[tuple[FvListing, FvDetail | None]]:
    return [(_fv_listing(r), _fv_det(r)) for r in _fv_rows(conn, "l.removed_at IS NULL", ())]


def fv_seen_since(
    conn: sqlite3.Connection, since: datetime
) -> list[tuple[FvListing, FvDetail | None, datetime | None]]:
    rows = _fv_rows(conn, "l.removed_at IS NULL OR l.removed_at >= ?", (_ts(since),))
    return [
        (
            _fv_listing(r),
            _fv_det(r),
            datetime.fromisoformat(r["removed_at"]) if r["removed_at"] else None,
        )
        for r in rows
    ]


def fv_record(
    conn: sqlite3.Connection,
    listings: Sequence[FvListing],
    details: Mapping[str, FvDetail],
    seen_at: datetime,
) -> FvChanges:
    """Same rules as `bf_record`: a row only on change, an empty run writes nothing, a
    re-listed flat starts with no detail; a flat without a detail this run keeps its last."""
    seen = _ts(seen_at)
    ids = [x.id for x in listings]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate listing ids")
    if any(x.seen_at != seen_at for x in listings):
        raise ValueError("listing seen_at differs from the run's seen_at")
    unknown = set(details) - set(ids)
    if unknown:
        raise ValueError(f"details for listings not in this run: {sorted(unknown)}")
    if not listings:
        return FvChanges(new=[], changed=[], removed=[], unchanged=0)

    new: list[FvListing] = []
    changed: list[tuple[FvListing, FvListing]] = []
    unchanged = 0
    cols = ["listing_id", "seen_at", *FV_STATE_COLS]
    insert = (
        f"INSERT INTO fv_observations ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    )
    with conn:
        act = {r["id"] for r in conn.execute("SELECT id FROM fv_listings WHERE removed_at IS NULL")}
        for x in listings:
            prev = conn.execute(
                "SELECT * FROM fv_observations WHERE listing_id = ? ORDER BY id DESC LIMIT 1",
                (x.id,),
            ).fetchone()
            if x.id in details:
                dcols = _fv_detail_cols(details[x.id])
            elif x.id in act and prev is not None:
                dcols = {c: prev[c] for c in FV_DETAIL_COLS}
            else:
                dcols = _fv_detail_cols(None)
            state = _fv_state(x, dcols)
            values = [x.id, seen, *(state[c] for c in FV_STATE_COLS)]
            if x.id not in act:
                conn.execute(
                    "INSERT INTO fv_listings (id, first_seen) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET removed_at = NULL",
                    (x.id, seen),
                )
                conn.execute(insert, values)
                new.append(x)
            elif prev is None:
                raise RuntimeError(f"active FV listing {x.id} has no observation")
            elif any(prev[c] != state[c] for c in FV_STATE_COLS):
                conn.execute(insert, values)
                changed.append((_fv_listing(prev), x))
            else:
                unchanged += 1
        removed = sorted(act - set(ids))
        conn.executemany(
            "UPDATE fv_listings SET removed_at = ? WHERE id = ?", [(seen, i) for i in removed]
        )
    return FvChanges(new=new, changed=changed, removed=removed, unchanged=unchanged)


# --- SBS (M6): list facts only, no detail pages ---
SBS_STATE_COLS = "tag city address move_in rent size_m2 rooms labels_json".split()


@dataclass(frozen=True)
class SbsChanges:
    new: list[SbsListing]
    changed: list[tuple[SbsListing, SbsListing]]
    removed: list[str]
    unchanged: int


def _sbs_state(x: SbsListing) -> dict:
    return {
        "tag": x.tag,
        "city": x.city,
        "address": x.address,
        "move_in": x.move_in.isoformat() if x.move_in else None,
        "rent": x.rent,
        "size_m2": x.size_m2,
        "rooms": x.rooms,
        "labels_json": json.dumps(list(x.labels), ensure_ascii=False),
    }


def _sbs_listing(row: sqlite3.Row) -> SbsListing:
    return SbsListing(
        id=row["listing_id"],
        tag=row["tag"],
        city=row["city"],
        address=row["address"],
        move_in=date.fromisoformat(row["move_in"]) if row["move_in"] else None,
        rent=row["rent"],
        size_m2=row["size_m2"],
        rooms=row["rooms"],
        labels=tuple(json.loads(row["labels_json"])),
        seen_at=datetime.fromisoformat(row["seen_at"]),
    )


def _sbs_rows(conn: sqlite3.Connection, where: str, args: tuple) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.*, l.first_seen, l.removed_at FROM sbs_listings l "
        "JOIN sbs_observations o ON o.id = "
        "(SELECT MAX(id) FROM sbs_observations WHERE listing_id = l.id) "
        f"WHERE {where} ORDER BY l.id",
        args,
    ).fetchall()


def sbs_active(conn: sqlite3.Connection) -> list[SbsListing]:
    return [_sbs_listing(r) for r in _sbs_rows(conn, "l.removed_at IS NULL", ())]


def sbs_seen_since(
    conn: sqlite3.Connection, since: datetime
) -> list[tuple[SbsListing, datetime, datetime | None]]:
    """(listing, first_seen, removed_at) for listings active at or after `since`. first_seen
    is when this listing (re-)appeared: a re-listing starts again."""
    rows = _sbs_rows(conn, "l.removed_at IS NULL OR l.removed_at >= ?", (_ts(since),))
    return [
        (
            _sbs_listing(r),
            datetime.fromisoformat(r["first_seen"]),
            datetime.fromisoformat(r["removed_at"]) if r["removed_at"] else None,
        )
        for r in rows
    ]


def sbs_record(
    conn: sqlite3.Connection,
    listings: Sequence[SbsListing],
    seen_at: datetime,
    *,
    confirmed_empty: bool = False,
) -> SbsChanges:
    """A row only on change; an empty run writes and removes nothing, unless the site said
    "no results" (`confirmed_empty`, Simon 2026-11-27): then every active listing is removed."""
    seen = _ts(seen_at)
    if confirmed_empty and listings:
        raise ValueError("confirmed_empty with listings")
    ids = [x.id for x in listings]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate listing ids")
    if any(x.seen_at != seen_at for x in listings):
        raise ValueError("listing seen_at differs from the run's seen_at")
    if not listings and not confirmed_empty:
        return SbsChanges(new=[], changed=[], removed=[], unchanged=0)

    new: list[SbsListing] = []
    changed: list[tuple[SbsListing, SbsListing]] = []
    unchanged = 0
    cols = ["listing_id", "seen_at", *SBS_STATE_COLS]
    insert = (
        f"INSERT INTO sbs_observations ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    )
    with conn:
        act = {
            r["id"] for r in conn.execute("SELECT id FROM sbs_listings WHERE removed_at IS NULL")
        }
        for x in listings:
            prev = conn.execute(
                "SELECT * FROM sbs_observations WHERE listing_id = ? ORDER BY id DESC LIMIT 1",
                (x.id,),
            ).fetchone()
            state = _sbs_state(x)
            values = [x.id, seen, *(state[c] for c in SBS_STATE_COLS)]
            if x.id not in act:
                conn.execute(
                    "INSERT INTO sbs_listings (id, first_seen) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET removed_at = NULL, "
                    "first_seen = excluded.first_seen",
                    (x.id, seen),
                )
                conn.execute(insert, values)
                new.append(x)
            elif prev is None:
                raise RuntimeError(f"active SBS listing {x.id} has no observation")
            elif any(prev[c] != state[c] for c in SBS_STATE_COLS):
                conn.execute(insert, values)
                changed.append((_sbs_listing(prev), x))
            else:
                unchanged += 1
        removed = sorted(act - set(ids))
        conn.executemany(
            "UPDATE sbs_listings SET removed_at = ? WHERE id = ?", [(seen, i) for i in removed]
        )
    return SbsChanges(new=new, changed=changed, removed=removed, unchanged=unchanged)
