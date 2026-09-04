"""Conditions and shared value objects (``03.8``, ``03.10``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from tor.errors import RuleViolation

__all__ = [
    "Condition",
    "ConditionSet",
    "CreatureType",
    "DriveKind",
    "RegionType",
    "ResourcePool",
    "Season",
    "StandardOfLiving",
    "TreasureHoldings",
    "standard_of_living_for",
]


class Condition(StrEnum):
    WEARY = "weary"
    MISERABLE = "miserable"
    WOUNDED = "wounded"


@dataclass(slots=True)
class ConditionSet:
    """The three condition boxes.

    Weary and Miserable are **derived** — recomputed by
    ``tor.rules.resources.recompute_conditions`` from Endurance vs Load and Shadow vs Hope.
    Nothing else in the codebase may write them. Wounded is event-driven: set when a Wound
    is taken, cleared by recovery.

    Two further states are deliberately *not* flags here: Dying is a separate marker with
    its own clock (``08.9``), and "Ill-favoured on every roll" is derived from
    ``shadow == max_hope`` and implemented as an always-on effect (``11.2``).
    """

    weary: bool = False
    miserable: bool = False
    wounded: bool = False

    def has(self, condition: Condition) -> bool:
        return bool(getattr(self, condition.value))

    def as_set(self) -> frozenset[Condition]:
        return frozenset(c for c in Condition if self.has(c))


@dataclass(slots=True)
class ResourcePool:
    """A spendable pool: Hope, Hate, Resolve, Fellowship (``03.10``).

    Patterns P8 (spend a point for +1d) and P9 (a depleted pool penalises its owner) live
    here rather than being written out per subsystem.
    """

    current: int
    maximum: int

    def __post_init__(self) -> None:
        if self.maximum < 0:
            raise ValueError(f"a pool maximum cannot be negative, got {self.maximum}")
        if not 0 <= self.current <= self.maximum:
            raise ValueError(f"{self.current} is outside the pool range 0..{self.maximum}")

    @property
    def depleted(self) -> bool:
        return self.current == 0

    def spend(self, n: int = 1) -> None:
        if n < 0:
            raise ValueError(f"cannot spend a negative amount, got {n}")
        if n > self.current:
            raise RuleViolation(
                f"cannot spend {n}: only {self.current} left",
                rule_reference="resource_pool_spending",
            )
        self.current -= n

    def restore(self, n: int) -> int:
        """Restore up to ``n``, clamping at the maximum. Returns the amount actually gained."""
        if n < 0:
            raise ValueError(f"cannot restore a negative amount, got {n}")
        before = self.current
        self.current = min(self.maximum, self.current + n)
        return self.current - before

    def set_maximum(self, maximum: int, *, raise_current: bool = True) -> None:
        """Change the ceiling.

        ``raise_current`` reflects the rule that a Virtue raising a maximum raises the
        current value by the same amount at the moment it is acquired (``03.4.2``).
        """
        if maximum < 0:
            raise ValueError(f"a pool maximum cannot be negative, got {maximum}")
        delta = maximum - self.maximum
        self.maximum = maximum
        if raise_current and delta > 0:
            self.current += delta
        self.current = min(self.current, self.maximum)


class DriveKind(StrEnum):
    """Which pool an adversary carries. Mechanically identical, semantically not.

    Killing a Resolve-bearing adversary must always be evaluated as a possible Misdeed;
    fighting minions of the Enemy can hardly call the heroes' integrity into question
    (``12.2``).
    """

    HATE = "hate"
    RESOLVE = "resolve"


class RegionType(StrEnum):
    """Defined once here because both Journey and the Eye of Mordor key off it."""

    BORDER = "border"
    WILD = "wild"
    DARK = "dark"


class CreatureType(StrEnum):
    ORCS = "orcs"
    TROLLS = "trolls"
    SPIDERS = "spiders"
    WOLVES = "wolves"
    EVIL_MEN = "evil_men"
    UNDEAD = "undead"


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class StandardOfLiving(IntEnum):
    """Six tiers. ``IntEnum`` because gear minimums compare them with ``>=`` (``03.7``)."""

    POOR = 0
    FRUGAL = 1
    COMMON = 2
    PROSPEROUS = 3
    RICH = 4
    VERY_RICH = 5


@dataclass(frozen=True, slots=True)
class TreasureHoldings:
    """Where a hero's Treasure is (``07.4``).

    Standard of Living derives from :attr:`total`; Load derives from :attr:`carried`
    alone. Conflating the two is the most likely bug in this area — a hero who caches a
    hoard keeps the wealth but sheds the burden.
    """

    carried: int = 0
    on_mounts: int = 0
    cached: int = 0

    def __post_init__(self) -> None:
        for name in ("carried", "on_mounts", "cached"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} Treasure cannot be negative")

    @property
    def total(self) -> int:
        return self.carried + self.on_mounts + self.cached


def standard_of_living_for(
    treasure: int, ladder: Sequence[tuple[StandardOfLiving, int | None]]
) -> StandardOfLiving:
    """The highest tier whose threshold is met (``03.7``).

    Treasure is the source of truth; the tier is derived from it, never stored
    independently. A ``None`` threshold marks a tier that accumulating Treasure never
    reaches — it is a starting condition only.
    """
    attained = StandardOfLiving.POOR
    for tier, threshold in sorted(ladder, key=lambda row: row[0]):
        if threshold is not None and treasure >= threshold:
            attained = tier
    return attained
