"""Criteria from config.yaml and personal values from env (plan M3).

config.yaml is committed, so it holds nothing personal. Queue days and booked listings come
from GitHub secrets via env. Loading is strict: anything unexpected raises `ConfigError`.
Errors about secrets never echo the secret's value.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
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
class BfConfig:
    types: tuple[str, ...]
    bostad_snabbt: bool
    municipalities: tuple[str, ...]
    tier1: tuple[str, ...]
    alert_max_rooms: float

    def tier(self, municipality: str, district: str) -> int | None:
        """0 = inner city, 1 = the rest of the municipalities in scope, None = out of scope."""
        if municipality.casefold() not in (m.casefold() for m in self.municipalities):
            return None
        return 0 if district.casefold() in (d.casefold() for d in self.tier1) else 1


@dataclass(frozen=True)
class FvConfig:
    alert_types: tuple[str, ...]
    digest_types: tuple[str, ...]


@dataclass(frozen=True)
class SbsConfig:
    rooms: tuple[str, ...]  # watched "rooms" values ("1 rum", "Korridor"); others = other type
    every: timedelta  # own guard: SBS is digest only (Simon, 2026-11-26)
    max_pages: int


@dataclass(frozen=True)
class HsConfig:
    district: str  # Heimstaden's district_name in scope (Simon, 2026-11-27)
    rooms: tuple[str, ...]  # watched "rooms" values; others = other type
    every: timedelta  # own guard: Heimstaden is digest only (Simon, 2026-11-26)


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
    bf: BfConfig
    fv: FvConfig
    sbs: SbsConfig
    heimstaden: HsConfig

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
class FvPoints:
    """Förvaltaren queue points read on `base_date`; one point a day from then (Simon)."""

    base: int
    base_date: date

    def on(self, d: date) -> int:
        return self.base + (d - self.base_date).days


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


def _types(v: Any, where: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    types = _keys(v, {"alert", "digest"}, where)
    alert = _names(types["alert"], f"{where}.alert")
    digest = _names(types["digest"], f"{where}.digest", empty_ok=True)
    if {x.casefold() for x in alert} & {x.casefold() for x in digest}:
        raise ConfigError(f"{where}: a type is in both alert and digest")
    return alert, digest


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml: {e}") from None
    top = {
        "types",
        "area_tiers",
        "stretch_areas",
        "rent",
        "move_in",
        "last_call_hours",
        "digest",
        "bf",
        "fv",
        "sbs",
        "heimstaden",
    }
    d = _keys(raw, top, "config")
    alert_types, digest_types = _types(d["types"], "types")

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

    bf = _keys(
        d["bf"], {"types", "bostad_snabbt", "municipalities", "tier1", "alert_max_rooms"}, "bf"
    )
    if not isinstance(bf["bostad_snabbt"], bool):
        raise ConfigError("bf.bostad_snabbt: expected true or false")
    rooms = bf["alert_max_rooms"]
    if isinstance(rooms, bool) or not isinstance(rooms, int | float) or rooms <= 0:
        raise ConfigError("bf.alert_max_rooms: expected a positive number")

    fv_alert, fv_digest = _types(_keys(d["fv"], {"types"}, "fv")["types"], "fv.types")

    sbs = _keys(d["sbs"], {"rooms", "every_hours", "max_pages"}, "sbs")
    every = sbs["every_hours"]
    if isinstance(every, bool) or not isinstance(every, int | float) or not 0.5 <= every <= 24:
        raise ConfigError("sbs.every_hours: expected a number in [0.5, 24]")
    pages = _int(sbs["max_pages"], "sbs.max_pages")
    if pages > 20:
        raise ConfigError("sbs.max_pages: at most 20")

    hs = _keys(d["heimstaden"], {"district", "rooms", "every_hours"}, "heimstaden")
    if not isinstance(hs["district"], str) or not hs["district"].strip():
        raise ConfigError("heimstaden.district: expected a name")
    hs_every = hs["every_hours"]
    if (
        isinstance(hs_every, bool)
        or not isinstance(hs_every, int | float)
        or not 0.5 <= hs_every <= 24
    ):
        raise ConfigError("heimstaden.every_hours: expected a number in [0.5, 24]")

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
        bf=BfConfig(
            types=_names(bf["types"], "bf.types"),
            bostad_snabbt=bf["bostad_snabbt"],
            municipalities=_names(bf["municipalities"], "bf.municipalities"),
            tier1=_names(bf["tier1"], "bf.tier1"),
            alert_max_rooms=float(rooms),
        ),
        fv=FvConfig(alert_types=fv_alert, digest_types=fv_digest),
        sbs=SbsConfig(
            rooms=_names(sbs["rooms"], "sbs.rooms"),
            every=timedelta(hours=every),
            max_pages=pages,
        ),
        heimstaden=HsConfig(
            district=hs["district"].strip(),
            rooms=_names(hs["rooms"], "heimstaden.rooms"),
            every=timedelta(hours=hs_every),
        ),
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


def load_bf_since(env: Mapping[str, str]) -> date | None:
    """BF_QUEUE_SINCE="YYYY-MM-DD" (secret): my BF queue start date. Unset = no BF alerts."""
    raw = (env.get("BF_QUEUE_SINCE") or "").strip()
    if not raw:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ValueError
        return date.fromisoformat(raw)
    except ValueError:
        raise ConfigError('BF_QUEUE_SINCE: not "YYYY-MM-DD"') from None


def load_fv_points(env: Mapping[str, str], today: date) -> FvPoints | None:
    """FV_POINTS="<points>", FV_POINTS_ON="YYYY-MM-DD" (secrets). Both unset = no FV alerts."""
    raw = (env.get("FV_POINTS") or "").strip()
    on = (env.get("FV_POINTS_ON") or "").strip()
    if not raw and not on:
        return None
    if not re.fullmatch(r"\d{1,6}", raw):
        raise ConfigError("FV_POINTS: missing or not a whole number (at most 6 digits)")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", on):
            raise ValueError
        base_date = date.fromisoformat(on)
    except ValueError:
        raise ConfigError('FV_POINTS_ON: missing or not "YYYY-MM-DD"') from None
    if base_date > today:
        raise ConfigError("FV_POINTS_ON is in the future")
    return FvPoints(base=int(raw), base_date=base_date)


def load_booked(env: Mapping[str, str]) -> frozenset[str]:
    """BOOKED_IDS: comma-separated objektNr (a GitHub variable). Empty or unset = none."""
    ids = [x.strip() for x in (env.get("BOOKED_IDS") or "").split(",") if x.strip()]
    if not all(_ID.fullmatch(x) for x in ids):
        raise ConfigError("BOOKED_IDS: expected comma-separated objektNr like 5102-3203-028")
    return frozenset(ids)
