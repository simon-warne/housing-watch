"""Which listings match, whether I can win them, and in what order (PRD §3, plan M3).

Pure functions: no DB, no clock. `eligible` means "would alert if winnable": the type alerts,
the move-in date is in the window, and the rent fits (the stretch band needs a stretch area
and a winnable or tied status).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from housing_watch.config import Config
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
