"""Criteria from config.yaml and personal values from env (plan M3).

config.yaml is committed, so it holds nothing personal. Queue days and booked listings come
from GitHub secrets via env. Loading is strict: anything unexpected raises `ConfigError`.
Errors about secrets never echo the secret's value.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any

import yaml

WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
_QUEUE = re.compile(r"(\d{1,5})@(\d{4}-\d{2}-\d{2})")
_ID = re.compile(r"[0-9]{3,5}(?:-[0-9]{2,5}){2}")
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    alert_types: tuple[str, ...]
    digest_types: tuple[str, ...]
    area_tiers: tuple[tuple[str, ...], ...]
    stretch_areas: frozenset[str]  # casefolded
    rent_max: int
    rent_stretch: int
    move_in_from: date
    move_in_to: date
    last_call_hours: float
    digest_weekday: int  # Monday = 0
    digest_time: time

    def tier(self, area: str) -> int:
        """0 = best; unlisted areas rank last (len(area_tiers))."""
        a = area.casefold()
        for i, tier in enumerate(self.area_tiers):
            if a in (x.casefold() for x in tier):
                return i
        return len(self.area_tiers)

    def is_stretch(self, area: str) -> bool:
        return area.casefold() in self.stretch_areas


@dataclass(frozen=True)
class QueueDays:
    base: int
    base_date: date
    until: date  # union membership valid through this date

    def on(self, d: date) -> int:
        return self.base + (min(d, self.until) - self.base_date).days


def _keys(d: Any, want: set[str], where: str) -> dict:
    if not isinstance(d, dict):
        raise ConfigError(f"{where}: expected a mapping")
    if set(d) != want:
        raise ConfigError(f"{where}: keys {sorted(map(str, d))}, expected {sorted(want)}")
    return d


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ConfigError(f"{where}: expected a positive integer")
    return v


def _date(v: Any, where: str) -> date:
    if isinstance(v, date):
        return v
    raise ConfigError(f"{where}: expected a date (YYYY-MM-DD)")


def _names(v: Any, where: str, *, empty_ok: bool = False) -> tuple[str, ...]:
    if not isinstance(v, list) or (not v and not empty_ok):
        raise ConfigError(f"{where}: expected a non-empty list")
    if not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{where}: expected names")
    out = tuple(x.strip() for x in v)
    if len({x.casefold() for x in out}) != len(out):
        raise ConfigError(f"{where}: duplicate names")
    return out


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml: {e}") from None
    top = {"types", "area_tiers", "stretch_areas", "rent", "move_in", "last_call_hours", "digest"}
    d = _keys(raw, top, "config")
    types = _keys(d["types"], {"alert", "digest"}, "types")
    alert_types = _names(types["alert"], "types.alert")
    digest_types = _names(types["digest"], "types.digest", empty_ok=True)
    if {x.casefold() for x in alert_types} & {x.casefold() for x in digest_types}:
        raise ConfigError("types: a type is in both alert and digest")

    if not isinstance(d["area_tiers"], list) or not d["area_tiers"]:
        raise ConfigError("area_tiers: expected a non-empty list of lists")
    tiers = tuple(_names(t, f"area_tiers[{i}]") for i, t in enumerate(d["area_tiers"]))
    flat = [a.casefold() for t in tiers for a in t]
    if len(set(flat)) != len(flat):
        raise ConfigError("area_tiers: an area is listed twice")
    stretch = {a.casefold() for a in _names(d["stretch_areas"], "stretch_areas", empty_ok=True)}
    if not stretch <= set(flat):
        raise ConfigError("stretch_areas: every area must also be in area_tiers")

    rent = _keys(d["rent"], {"max", "stretch"}, "rent")
    rent_max, rent_stretch = _int(rent["max"], "rent.max"), _int(rent["stretch"], "rent.stretch")
    if rent_max > rent_stretch:
        raise ConfigError("rent: max must be <= stretch")

    move = _keys(d["move_in"], {"from", "to"}, "move_in")
    m_from, m_to = _date(move["from"], "move_in.from"), _date(move["to"], "move_in.to")
    if m_from > m_to:
        raise ConfigError("move_in: from must be <= to")

    hours = d["last_call_hours"]
    if isinstance(hours, bool) or not isinstance(hours, int | float) or not 0 < hours <= 48:
        raise ConfigError("last_call_hours: expected a number in (0, 48]")

    dig = _keys(d["digest"], {"weekday", "time"}, "digest")
    if dig["weekday"] not in WEEKDAYS:
        raise ConfigError(f"digest.weekday: expected one of {WEEKDAYS}")
    m = _HHMM.fullmatch(dig["time"]) if isinstance(dig["time"], str) else None
    if not m:
        raise ConfigError('digest.time: expected "HH:MM"')

    return Config(
        alert_types=alert_types,
        digest_types=digest_types,
        area_tiers=tiers,
        stretch_areas=frozenset(stretch),
        rent_max=rent_max,
        rent_stretch=rent_stretch,
        move_in_from=m_from,
        move_in_to=m_to,
        last_call_hours=float(hours),
        digest_weekday=WEEKDAYS.index(dig["weekday"]),
        digest_time=time(int(m.group(1)), int(m.group(2))),
    )


def load_queue_days(env: Mapping[str, str]) -> QueueDays:
    """QUEUE_DAYS="<days>@<YYYY-MM-DD>", QUEUE_DAYS_UNTIL="<YYYY-MM-DD>" (secrets)."""
    m = _QUEUE.fullmatch((env.get("QUEUE_DAYS") or "").strip())
    if not m:
        raise ConfigError('QUEUE_DAYS: missing or not "<days>@<YYYY-MM-DD>"')
    try:
        base_date = date.fromisoformat(m.group(2))
        until = date.fromisoformat((env.get("QUEUE_DAYS_UNTIL") or "").strip())
    except ValueError:
        raise ConfigError("QUEUE_DAYS / QUEUE_DAYS_UNTIL: invalid date") from None
    if until < base_date:
        raise ConfigError("QUEUE_DAYS_UNTIL is before the QUEUE_DAYS date")
    return QueueDays(base=int(m.group(1)), base_date=base_date, until=until)


def load_booked(env: Mapping[str, str]) -> frozenset[str]:
    """BOOKED_IDS: comma-separated objektNr (a GitHub variable). Empty or unset = none."""
    ids = [x.strip() for x in (env.get("BOOKED_IDS") or "").split(",") if x.strip()]
    if not all(_ID.fullmatch(x) for x in ids):
        raise ConfigError("BOOKED_IDS: expected comma-separated objektNr like 5102-3203-028")
    return frozenset(ids)
