"""Which listings match, whether I can win them, and in what order (PRD §3, plan M3).

Pure functions: no DB, no clock. `eligible` means "would alert if winnable": the type alerts,
the move-in date is in the window, and the rent fits (the stretch band needs a stretch area
and a winnable or tied status).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from housing_watch.bf import BfDetail, BfListing
from housing_watch.config import Config
from housing_watch.fv import FvDetail, FvListing
from housing_watch.sssb import Listing


class Status(StrEnum):
    WINNABLE = "winnable"  # more days than the top applicant, or nobody has booked
    TIED = "tied"  # SSSB's tie rule is unknown
    BEHIND = "behind"


def status(top_days: int | None, mine: int) -> Status:
    if top_days is None or mine > top_days:
        return Status.WINNABLE
    return Status.TIED if mine == top_days else Status.BEHIND


@dataclass(frozen=True)
class Verdict:
    listing: Listing
    status: Status
    category: str  # "alert" | "digest" | "other" (by type)
    in_window: bool
    eligible: bool
    over_budget: bool
    tier: int

    @property
    def ahead(self) -> bool:
        return self.status != Status.BEHIND


def _category(type_: str, cfg: Config) -> str:
    t = type_.casefold()
    if t in (x.casefold() for x in cfg.alert_types):
        return "alert"
    if t in (x.casefold() for x in cfg.digest_types):
        return "digest"
    return "other"


def classify(x: Listing, cfg: Config, mine: int) -> Verdict:
    st = status(x.top_days, mine)
    category = _category(x.type, cfg)
    in_window = cfg.move_in_from <= x.move_in <= cfg.move_in_to
    within = x.rent <= cfg.rent_max
    stretch = (
        cfg.rent_max < x.rent <= cfg.rent_stretch and cfg.is_stretch(x.area) and st != Status.BEHIND
    )
    eligible = category == "alert" and in_window and (within or stretch)
    return Verdict(
        listing=x,
        status=st,
        category=category,
        in_window=in_window,
        eligible=eligible,
        over_budget=eligible and stretch,
        tier=cfg.tier(x.area),
    )


def rank_key(v: Verdict) -> tuple:
    x = v.listing
    return (v.tier, x.floor == 0, x.rent, x.id)


def ranked(verdicts: Iterable[Verdict]) -> list[Verdict]:
    """Best first: area tier, then ground floor last, then lower rent."""
    return sorted(verdicts, key=rank_key)


# --- Bostadsförmedlingen (M5 part B) ---
class BfStatus(StrEnum):
    WINNABLE = "winnable"  # my queue start is earlier than the top applicant's
    TIED = "tied"  # same date: whoever applied first wins
    BEHIND = "behind"
    NO_APPLICANTS_SHOWN = "no applicants shown"  # no top-3 block: last call only (Simon)
    FIRST_COME = "first come"  # Bostad snabbt
    PENDING = "no detail yet"
    NO_QUEUE_DATE = "no BF queue date set"


def bf_status(top_dates: tuple[date, ...], since: date | None) -> BfStatus:
    if since is None:
        return BfStatus.NO_QUEUE_DATE
    if not top_dates:
        return BfStatus.NO_APPLICANTS_SHOWN
    top = min(top_dates)
    if since < top:
        return BfStatus.WINNABLE
    return BfStatus.TIED if since == top else BfStatus.BEHIND


CHANCE = {BfStatus.WINNABLE, BfStatus.TIED, BfStatus.NO_APPLICANTS_SHOWN, BfStatus.FIRST_COME}


def bf_category(x: BfListing, cfg: Config) -> str:
    """'alert' (watched type, few rooms), 'digest' (watched, more rooms) or 'other'."""
    watched = (x.bostad_snabbt and cfg.bf.bostad_snabbt) or x.type.casefold() in (
        t.casefold() for t in cfg.bf.types
    )
    if not watched:
        return "other"
    return "alert" if x.rooms <= cfg.bf.alert_max_rooms else "digest"


def bf_worth_details(x: BfListing, cfg: Config) -> bool:
    """Could alert for *someone*: public facts only (type, scope, rooms, rent), never my date."""
    tier = cfg.bf.tier(x.municipality, x.district)
    if tier is None or x.rent is None or bf_category(x, cfg) != "alert":
        return False
    return x.rent <= cfg.rent_max or (tier == 0 and x.rent <= cfg.rent_stretch)


@dataclass(frozen=True)
class BfVerdict:
    listing: BfListing
    detail: BfDetail | None
    status: BfStatus
    category: str
    tier: int | None  # None = out of scope
    in_window: bool | None  # None = move-in not known yet
    eligible: bool
    over_budget: bool


def bf_classify(x: BfListing, d: BfDetail | None, cfg: Config, since: date | None) -> BfVerdict:
    if d is None:
        st = BfStatus.PENDING
    elif x.bostad_snabbt and cfg.bf.bostad_snabbt:
        st = BfStatus.FIRST_COME
    else:
        st = bf_status(d.top_dates, since)
    tier = cfg.bf.tier(x.municipality, x.district)
    category = bf_category(x, cfg)
    in_window = None if d is None else cfg.move_in_from <= d.move_in <= cfg.move_in_to
    within = x.rent is not None and x.rent <= cfg.rent_max
    stretch = (
        x.rent is not None
        and cfg.rent_max < x.rent <= cfg.rent_stretch
        and tier == 0
        and st in CHANCE
    )
    eligible = tier is not None and category == "alert" and bool(in_window) and (within or stretch)
    return BfVerdict(
        listing=x,
        detail=d,
        status=st,
        category=category,
        tier=tier,
        in_window=in_window,
        eligible=eligible,
        over_budget=eligible and stretch,
    )


def bf_rank_key(v: BfVerdict) -> tuple:
    x = v.listing
    tier = v.tier if v.tier is not None else 9
    return (tier, x.floor == 0, x.rent if x.rent is not None else 10**9, x.id)


def bf_ranked(verdicts: Iterable[BfVerdict]) -> list[BfVerdict]:
    return sorted(verdicts, key=bf_rank_key)


# --- Förvaltaren (M6) ---
class FvStatus(StrEnum):
    WINNABLE = "winnable"  # more points than the top applicant
    TIED = "tied"  # Förvaltaren's tie rule is unknown
    BEHIND = "behind"
    NO_APPLICANTS_SHOWN = "no applicants shown"  # no "Högst köpoäng" row: last call only
    PENDING = "no detail yet"
    NO_POINTS = "no FV points set"


def fv_status(top_points: int | None, mine: int | None) -> FvStatus:
    if mine is None:
        return FvStatus.NO_POINTS
    if top_points is None:
        return FvStatus.NO_APPLICANTS_SHOWN
    if mine > top_points:
        return FvStatus.WINNABLE
    return FvStatus.TIED if mine == top_points else FvStatus.BEHIND


FV_CHANCE = {FvStatus.WINNABLE, FvStatus.TIED, FvStatus.NO_APPLICANTS_SHOWN}


def fv_category(x: FvListing, cfg: Config) -> str:
    t = x.type.casefold()
    if t in (a.casefold() for a in cfg.fv.alert_types):
        return "alert"
    if t in (a.casefold() for a in cfg.fv.digest_types):
        return "digest"
    return "other"


def fv_worth_details(x: FvListing, cfg: Config) -> bool:
    """Could alert for *someone*: public facts only (type, rent, special contract), never my
    points. All of Förvaltaren is Sundbyberg (tier 2), so there is no stretch band."""
    return fv_category(x, cfg) == "alert" and not x.special and x.rent <= cfg.rent_max


@dataclass(frozen=True)
class FvVerdict:
    listing: FvListing
    detail: FvDetail | None
    status: FvStatus
    category: str
    in_window: bool | None  # None = move-in not known yet
    eligible: bool


def fv_classify(x: FvListing, d: FvDetail | None, cfg: Config, mine: int | None) -> FvVerdict:
    st = FvStatus.PENDING if d is None else fv_status(d.top_points, mine)
    in_window = None if d is None else cfg.move_in_from <= d.move_in <= cfg.move_in_to
    return FvVerdict(
        listing=x,
        detail=d,
        status=st,
        category=fv_category(x, cfg),
        in_window=in_window,
        eligible=fv_worth_details(x, cfg) and bool(in_window),
    )


def fv_rank_key(v: FvVerdict) -> tuple:
    x = v.listing
    return (x.floor == 0, x.rent, x.id)


def fv_ranked(verdicts: Iterable[FvVerdict]) -> list[FvVerdict]:
    """Best first: ground floor last, then lower rent (one area tier)."""
    return sorted(verdicts, key=fv_rank_key)
