"""The scheduled run (M4): fetch, details, record, alerts, heartbeat, encrypted state.

`python -m housing_watch.run --data DIR [--init] [--test-alert]`

DIR holds `history.sqlite` (public) and `state.db.enc` (Fernet, key = STATE_KEY). The plaintext
state only exists in a temp dir during the run. When the run has saved everything it writes
`DIR/ready`; the workflow pushes only then. Every run that gets that far re-encrypts state,
so every push looks alike (Simon, 2026-11-26).

Logs are one fixed line (`ok: N listings`, `skipped: recent fetch`, `failed: <ErrorType>`):
the Action log is public, so no alert counts, titles, queue days or error text.
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

from housing_watch.alerts import (
    GRACE,
    Alert,
    digest_alert,
    dispatch,
    listing_alerts,
    open_state,
    run_failed,
    run_ok,
)
from housing_watch.config import QueueDays, load_booked, load_config, load_queue_days
from housing_watch.crypto import decrypt, encrypt
from housing_watch.history import deadline, open_db, record
from housing_watch.ntfy import make_send
from housing_watch.sssb import (
    Detail,
    Listing,
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


def _observations(hist: sqlite3.Connection) -> int:
    return hist.execute("SELECT COUNT(*) FROM observations").fetchone()[0]


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
    alerts = listing_alerts(hist, cfg, queue, booked, now)
    alerts.append(digest_alert(hist, cfg, queue, booked, now))
    return len(listings), alerts


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
    send: Callable[[Alert], object],
    sleep: Callable[[float], object],
    now: datetime,
    init: bool = False,
    test_alert: bool = False,
    config: Path = CONFIG,
) -> int:
    """One scheduled run. Returns 0 (ok or skipped) or 1 (failed run, saved and ready).

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
            failed: Exception | None = None
            n = 0
            last = _meta(state, "last_fetch")
            if last and now - datetime.fromisoformat(last) < GUARD:
                line = "skipped: recent fetch"
            else:
                _set_meta(state, "last_fetch", now.astimezone(UTC).isoformat())
                try:
                    n, alerts = _watch(hist, cfg, queue, booked, now, get, sleep)
                    run_ok(state)
                except Exception as e:  # noqa: BLE001 - every failure feeds the heartbeat
                    failed = e
                    alerts = [run_failed(state, now, f"{type(e).__name__}: {e}")]
                dispatch(state, alerts, send, now)
                line = f"failed: {type(failed).__name__}" if failed else f"ok: {n} listings"
            if _observations(hist) < before:
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
