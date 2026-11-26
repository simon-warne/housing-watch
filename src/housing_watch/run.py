"""The scheduled run (M4): fetch, details, record, alerts, heartbeat, encrypted state.

`python -m housing_watch.run --data DIR [--init] [--test-alert]`

DIR holds `history.sqlite` (public) and `state.db.enc` (Fernet, key = STATE_KEY). The plaintext
state only exists in a temp dir during the run. When the run has saved everything it writes
`DIR/ready`; the workflow pushes only then. Every run that gets that far re-encrypts state,
so every push looks alike (Simon, 2026-11-26).

Logs are one fixed line (`ok: N listings`, `skipped: recent fetch`, `failed: <ErrorType>`,
then ` · bf ok: M` for Bostadsförmedlingen and ` · fv ok: K` for Förvaltaren): the Action log
is public, so no alert counts, titles, queue days or error text. SSSB, BF and Förvaltaren run
one after the other, each in its own try: a failure in one never stops the others' alerts,
and each has its own guard and heartbeat.
"""

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from housing_watch import bf, fv
from housing_watch.alerts import (
    GRACE,
    Alert,
    bf_listing_alerts,
    digest_alert,
    dispatch,
    fv_alerted,
    fv_listing_alerts,
    listing_alerts,
    open_state,
    run_failed,
    run_ok,
)
from housing_watch.bf import (
    BfDetail,
    BfListing,
    bf_deadline,
    bf_get,
    parse_bf_detail,
    parse_bf_list,
)
from housing_watch.config import (
    QueueDays,
    load_bf_since,
    load_booked,
    load_config,
    load_fv_points,
    load_queue_days,
)
from housing_watch.crypto import decrypt, encrypt
from housing_watch.fv import (
    FvDetail,
    FvListing,
    fv_deadline,
    fv_get,
    parse_fv_detail,
    parse_fv_list,
)
from housing_watch.history import (
    bf_detail,
    bf_record,
    deadline,
    fv_detail,
    fv_record,
    open_db,
    record,
)
from housing_watch.ntfy import make_send
from housing_watch.rules import bf_worth_details, fv_worth_details
from housing_watch.sssb import (
    STOCKHOLM,
    Detail,
    Listing,
    ParseError,
    detail_url,
    http_get,
    list_url,
    load_jsonp,
    parse_detail,
    parse_list,
)

CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"
HISTORY, STATE_ENC, READY = "history.sqlite", "state.db.enc", "ready"
PAUSE_S = 5  # between SSSB requests
DETAIL_CAP = 10  # detail requests per run
LIVE_WINDOW = timedelta(hours=48)  # PRD §1: live top days this close to a deadline
GUARD = timedelta(minutes=25)  # never two list fetches closer than this (plan M4)


class EmptyRun(Exception):
    """SSSB answered, but with 0 listings (counts as a failed run, PRD §4)."""


class PartialRun(Exception):
    """Some BF detail pages didn't parse: the rest were recorded and their alerts stand, but
    the run still counts as failed for the heartbeat (M5 review)."""

    def __init__(self, cause: Exception, n: int, alerts: list):
        super().__init__(str(cause))
        self.cause, self.n, self.alerts = cause, n, alerts


def _meta(state: sqlite3.Connection, key: str) -> str | None:
    row = state.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(state: sqlite3.Connection, key: str, value: str) -> None:
    with state:
        state.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _observations(hist: sqlite3.Connection) -> tuple[int, ...]:
    """Rows per observation table, compared one by one: growth in one source must not hide
    a loss in the other (M5 review)."""
    return tuple(
        hist.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("observations", "bf_observations", "fv_observations")
    )


def _higher(a: int | None, b: int | None) -> int | None:
    return max((x for x in (a, b) if x is not None), default=None)


def merge(x: Listing, d: Detail | None) -> Listing:
    """The higher of the list's and the detail page's figures: can only be too cautious."""
    if d is None:
        return x
    return replace(
        x, top_days=_higher(x.top_days, d.top_days), applicants=max(x.applicants, d.applicants)
    )


def _worth_details(x: Listing, cfg) -> bool:
    """Could alert for *someone*: the public config.yaml rule with status ignored.

    Never my status or BOOKED_IDS: which listings get detail requests shows in the public
    history and in the step's run time, so it must not depend on anything private (M4 review).
    """
    in_window = cfg.move_in_from <= x.move_in <= cfg.move_in_to
    rent_ok = x.rent <= cfg.rent_max or (x.rent <= cfg.rent_stretch and cfg.is_stretch(x.area))
    alert_type = x.type.casefold() in (t.casefold() for t in cfg.alert_types)
    return alert_type and in_window and rent_ok


def _rotate(xs: list[Listing], now: datetime) -> list[Listing]:
    """Shift by DETAIL_CAP per half-hour slot, so over two runs every listing gets a turn."""
    if not xs:
        return xs
    k = int(now.timestamp() // 1800) * DETAIL_CAP % len(xs)
    return xs[k:] + xs[:k]


def pick_details(
    hist: sqlite3.Connection, listings: Sequence[Listing], cfg, now: datetime
) -> list[Listing]:
    """Listings that need a detail request this run (public facts only, see _worth_details).

    No known deadline: fetched once (record() keeps it). Closing within 48 h: every run, for
    live top days, those in the last-call window first; rotated when there are more than
    DETAIL_CAP. Closed: never. At most DETAIL_CAP; the rest wait for the next run.
    """
    urgent_by = timedelta(hours=cfg.last_call_hours) + GRACE
    urgent: list[Listing] = []
    near: list[Listing] = []
    unknown: list[Listing] = []
    for x in sorted(listings, key=lambda x: x.id):
        if not _worth_details(x, cfg):
            continue
        dl = deadline(hist, x.id)
        if dl is None:
            unknown.append(x)
        elif now < dl <= now + urgent_by:
            urgent.append(x)
        elif now < dl <= now + LIVE_WINDOW:
            near.append(x)
    unknown.sort(key=lambda x: (cfg.tier(x.area), x.floor == 0, x.rent, x.id))
    return (_rotate(urgent, now) + _rotate(near, now) + unknown)[:DETAIL_CAP]


def pick_bf_details(
    hist: sqlite3.Connection, listings: Sequence[BfListing], cfg, now: datetime
) -> list[BfListing]:
    """BF listings that need a detail page this run, by public facts only (bf_worth_details,
    the list's deadline, the stored move-in): closing within 48 h → every run, the last-call
    window first, rotated; never fetched → once; known move-in outside the window or closed →
    never. At most DETAIL_CAP."""
    urgent_by = timedelta(hours=cfg.last_call_hours) + GRACE
    urgent: list[BfListing] = []
    near: list[BfListing] = []
    unknown: list[BfListing] = []
    for x in sorted(listings, key=lambda x: x.id):
        if not bf_worth_details(x, cfg):
            continue
        dl = bf_deadline(x.deadline)
        d = bf_detail(hist, x.id)
        if dl <= now or (d and not cfg.move_in_from <= d.move_in <= cfg.move_in_to):
            continue
        if dl <= now + urgent_by:
            urgent.append(x)
        elif dl <= now + LIVE_WINDOW:
            near.append(x)
        elif d is None:
            unknown.append(x)
    tier = cfg.bf.tier
    unknown.sort(key=lambda x: (tier(x.municipality, x.district), x.floor == 0, x.rent, x.id))
    return (_rotate(urgent, now) + _rotate(near, now) + unknown)[:DETAIL_CAP]


def _watch_bf(hist, cfg, since, now, get, sleep) -> tuple[int, list]:
    listings = parse_bf_list(get(bf.LIST_URL), now)
    if not listings:
        raise EmptyRun("0 BF listings parsed")
    details: dict[str, BfDetail] = {}
    errors: list[Exception] = []
    for x in pick_bf_details(hist, listings, cfg, now):
        sleep(PAUSE_S)
        try:
            details[x.id] = parse_bf_detail(get(bf.detail_url(x)), x)
        except (ParseError, OSError) as e:  # one odd or missing page must not block the others
            errors.append(e)
    bf_record(hist, listings, details, now)
    alerts = bf_listing_alerts(hist, cfg, since, frozenset(details), now)
    if errors:
        raise PartialRun(errors[0], len(listings), alerts)
    return len(listings), alerts


def pick_fv_details(
    hist: sqlite3.Connection, listings: Sequence[FvListing], cfg, now: datetime
) -> list[FvListing]:
    """FV flats that need a detail page this run, by public facts only (fv_worth_details, the
    list's deadline, the stored move-in), never my points: closing within 48 h → every run,
    the last-call window first, rotated; never fetched → once; known move-in outside the
    window or closed → never. At most DETAIL_CAP."""
    urgent_by = timedelta(hours=cfg.last_call_hours) + GRACE
    urgent: list[FvListing] = []
    near: list[FvListing] = []
    unknown: list[FvListing] = []
    for x in sorted(listings, key=lambda x: x.id):
        if not fv_worth_details(x, cfg):
            continue
        dl = fv_deadline(x.deadline)
        d = fv_detail(hist, x.id)
        if dl <= now or (d and not cfg.move_in_from <= d.move_in <= cfg.move_in_to):
            continue
        if dl <= now + urgent_by:
            urgent.append(x)
        elif dl <= now + LIVE_WINDOW:
            near.append(x)
        elif d is None:
            unknown.append(x)
    unknown.sort(key=lambda x: (x.floor == 0, x.rent, x.id))
    return (_rotate(urgent, now) + _rotate(near, now) + unknown)[:DETAIL_CAP]


def _watch_fv(hist, cfg, points, state, now, get, sleep) -> tuple[int, list]:
    listings = parse_fv_list(get(fv.LIST_URL), now)
    if not listings:
        raise EmptyRun("0 FV listings parsed")
    details: dict[str, FvDetail] = {}
    errors: list[Exception] = []
    for x in pick_fv_details(hist, listings, cfg, now):
        sleep(PAUSE_S)
        try:
            details[x.id] = parse_fv_detail(get(fv.detail_url(x)), x)
        except (ParseError, OSError) as e:  # one odd or missing page must not block the others
            errors.append(e)
    fv_record(hist, listings, details, now)
    alerts = fv_listing_alerts(hist, cfg, points, frozenset(details), fv_alerted(state), now)
    if errors:
        raise PartialRun(errors[0], len(listings), alerts)
    return len(listings), alerts


def _watch(hist, cfg, queue: QueueDays, booked, now, get, sleep) -> tuple[int, list]:
    listings = parse_list(load_jsonp(get(list_url())), now)
    if not listings:
        raise EmptyRun("0 listings parsed")
    details: dict[str, Detail] = {}
    for x in pick_details(hist, listings, cfg, now):
        sleep(PAUSE_S)
        details[x.id] = parse_detail(load_jsonp(get(detail_url(x))), x.id)
    listings = [merge(x, details.get(x.id)) for x in listings]
    record(hist, listings, details, now)
    return len(listings), listing_alerts(hist, cfg, queue, booked, now)


def _test_alert(now: datetime) -> Alert:
    return Alert(
        kind="test",
        listing_id=None,
        state_key=now.astimezone(UTC).isoformat(),
        priority="high",
        title="housing-watch test",
        body="Test from the watch workflow (M4). If you see this, real alerts reach your phone.",
        url=None,
    )


def run(
    data: Path,
    env: Mapping[str, str],
    *,
    get: Callable[[str], str],
    bf_get: Callable[[str], str] | None = None,
    fv_get: Callable[[str], str] | None = None,
    send: Callable[[Alert], object],
    sleep: Callable[[float], object],
    now: datetime,
    init: bool = False,
    test_alert: bool = False,
    config: Path = CONFIG,
) -> int:
    """One scheduled run. Returns 0 (ok or skipped) or 1 (SSSB or BF failed, saved and ready).

    `bf_get` fetches Bostadsförmedlingen and `fv_get` Förvaltaren; None leaves that source
    out (tests of one source).

    Setup problems (config, secrets, key, missing or unexpected files, a shrinking history)
    raise instead, before `ready` exists, so nothing is pushed.
    """
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    if test_alert:
        send(_test_alert(now))
        print("ok: test alert", flush=True)
        return 0

    cfg = load_config(config)
    queue = load_queue_days(env)
    booked = load_booked(env)
    bf_since = load_bf_since(env)
    fv_points = load_fv_points(env, now.astimezone(STOCKHOLM).date())
    key = env.get("STATE_KEY")
    encrypt(b"", key)  # a bad key fails here, before any request

    data = Path(data)
    hist_p, enc_p, ready = data / HISTORY, data / STATE_ENC, data / READY
    ready.unlink(missing_ok=True)
    if init:
        if hist_p.exists() or enc_p.exists():
            raise FileExistsError("--init with existing data")
    elif not (hist_p.exists() and enc_p.exists()):
        raise FileNotFoundError("data missing (use --init only for the very first run)")
    data.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        state_p = Path(tmp) / "state.db"
        if enc_p.exists():
            state_p.write_bytes(decrypt(enc_p.read_bytes(), key))
        hist, state = open_db(hist_p), open_state(state_p)
        try:
            before = _observations(hist)
            alerts: list = []
            failures: list[Exception] = []
            parts: list[str] = []
            ok: list[str] = []

            def part(name, meta_key, label, work):
                last = _meta(state, meta_key)
                if last and now - datetime.fromisoformat(last) < GUARD:
                    parts.append(f"{label}skipped: recent fetch")
                    return
                _set_meta(state, meta_key, now.astimezone(UTC).isoformat())
                try:
                    n, found = work()
                    run_ok(state, source=name)
                    alerts.extend(found)
                    parts.append(f"{label}ok: {n}" + ("" if name else " listings"))
                    ok.append(name)
                except Exception as e:  # noqa: BLE001 - every failure feeds the heartbeat
                    if isinstance(e, PartialRun):
                        alerts.extend(e.alerts)
                        ok.append(name)  # recorded: the digest may use it
                        e = e.cause
                    failures.append(e)
                    alerts.append(run_failed(state, now, f"{type(e).__name__}: {e}", source=name))
                    parts.append(f"{label}failed: {type(e).__name__}")

            part(
                "",
                "last_fetch",
                "",
                lambda: _watch(hist, cfg, queue, booked, now, get, sleep),
            )
            if bf_get is not None:
                part(
                    "bf",
                    "bf_last_fetch",
                    "bf ",
                    lambda: _watch_bf(hist, cfg, bf_since, now, bf_get, sleep),
                )
            if fv_get is not None:
                part(
                    "fv",
                    "fv_last_fetch",
                    "fv ",
                    lambda: _watch_fv(hist, cfg, fv_points, state, now, fv_get, sleep),
                )
            if ok:  # after every part, so it has this run's data and survives either failing
                try:
                    alerts.append(
                        digest_alert(
                            hist, cfg, queue, booked, now, bf_since=bf_since, fv_points=fv_points
                        )
                    )
                    run_ok(state, source="digest")
                except Exception as e:  # noqa: BLE001
                    failures.append(e)
                    alerts.append(
                        run_failed(state, now, f"{type(e).__name__}: {e}", source="digest")
                    )
                    parts.append(f"digest failed: {type(e).__name__}")
            dispatch(state, alerts, send, now)
            line = " · ".join(parts)
            failed = failures[0] if failures else None
            if any(n < b for n, b in zip(_observations(hist), before, strict=True)):
                raise RuntimeError("history shrank; not saving")
        finally:
            hist.close()
            state.close()
        enc_p.write_bytes(encrypt(state_p.read_bytes(), key))
    ready.touch()
    print(line, flush=True)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="housing_watch.run")
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--init", action="store_true", help="first run: no data branch yet")
    p.add_argument("--test-alert", action="store_true", help="send one test alert, nothing else")
    args = p.parse_args(argv)
    try:
        env = dict(os.environ)
        return run(
            args.data,
            env,
            get=http_get,
            bf_get=bf_get,
            fv_get=fv_get,
            send=make_send(env.get("NTFY_TOPIC", "")),
            sleep=time.sleep,
            now=datetime.now(UTC),
            init=args.init,
            test_alert=args.test_alert,
        )
    except Exception as e:  # noqa: BLE001 - the public log gets the type only
        print(f"error: {type(e).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
