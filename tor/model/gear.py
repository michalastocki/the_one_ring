"""War gear, Useful Items and Mounts (``03.5``).

Types come from a content pack; **instances** carry the per-item state — Rewards applied,
Enchanted Rewards, Blessings, Curses, wielding mode, whether the item is dropped, and for
Famous gear which qualities are active as opposed to dormant.

Effective Damage, Injury, Load and Parry are **derived, never stored**: the base comes
from the type, then the ``MODIFY_DAMAGE_RATING`` / ``MODIFY_INJURY_RATING`` /
``MODIFY_ITEM_LOAD`` / ``MODIFY_SHIELD_PARRY`` hooks fire for each applied Reward,
Enchanted Reward and Curse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from tor.model.conditions import CreatureType, StandardOfLiving
from tor.model.ids import AbilityId, EffectId, ItemId

__all__ = [
    "ArmourCategory",
    "ArmourInstance",
    "ArmourType",
    "Craftsmanship",
    "Gear",
    "Mount",
    "ShieldInstance",
    "ShieldType",
    "Upgrades",
    "UsefulItem",
    "WeaponInstance",
    "WeaponType",
]


class ArmourCategory(StrEnum):
    LEATHER = "leather"
    MAIL = "mail"
    HEADGEAR = "headgear"


class Craftsmanship(StrEnum):
    ELVEN = "elven"
    DWARVEN = "dwarven"
    NUMENOREAN = "numenorean"


@dataclass(frozen=True, slots=True)
class WeaponType:
    """A weapon as the content pack defines it."""

    id: ItemId
    name: str
    damage: int
    #: ``None`` for a grip the weapon cannot be used in. At least one entry — unarmed —
    #: has neither, together with ``can_cause_piercing_blow=False``.
    injury_one_handed: int | None
    injury_two_handed: int | None
    load: int
    proficiency: AbilityId | Literal["brawling"]
    ranged: bool = False
    throwable: bool = False
    two_handed_only: bool = False
    #: ``False`` short-circuits the Piercing Blow check entirely (``08.7``).
    can_cause_piercing_blow: bool = True

    def injury(self, *, two_handed: bool) -> int | None:
        return self.injury_two_handed if two_handed else self.injury_one_handed


@dataclass(frozen=True, slots=True)
class ArmourType:
    id: ItemId
    name: str
    #: Success dice added to a PROTECTION roll. Body armour and helm sum (``08.7``).
    protection: int
    load: int
    category: ArmourCategory
    min_standard_of_living: StandardOfLiving | None = None
    removable_in_combat: bool = True


@dataclass(frozen=True, slots=True)
class ShieldType:
    id: ItemId
    name: str
    parry_bonus: int
    load: int
    min_standard_of_living: StandardOfLiving | None = None


@dataclass(slots=True)
class Upgrades:
    """The qualities an item carries. Composed into every instance, not inherited.

    Rewards and Enchanted Rewards are effects like any other; the ids here are what the
    ``EffectBus`` registers against this item's source.
    """

    rewards: list[EffectId] = field(default_factory=list)
    enchanted: list[EffectId] = field(default_factory=list)
    #: Famous gear reveals only its first quality; the rest wake with VALOUR (``13.7.4``).
    dormant: list[EffectId] = field(default_factory=list)
    curse: EffectId | None = None
    craftsmanship: Craftsmanship | None = None

    @property
    def count(self) -> int:
        """Active qualities. A Famous item may carry at most 3 (``13.7.3``)."""
        return len(self.rewards) + len(self.enchanted)

    @property
    def plot_immune(self) -> bool:
        """Items bearing a Reward can never be lost, broken, or taken (``15.4.1``).

        Two places this bites: Break Shield special damage checks it, and the item-loss
        path refuses to remove such an item. The heirloom mechanism is the sole exception.
        """
        return bool(self.rewards or self.enchanted)


@dataclass(slots=True)
class _Instance:
    """Fields every carried item shares."""

    type_id: ItemId
    name: str | None = None
    dropped: bool = False
    upgrades: Upgrades = field(default_factory=Upgrades)

    @property
    def plot_immune(self) -> bool:
        return self.upgrades.plot_immune


@dataclass(slots=True)
class WeaponInstance(_Instance):
    #: Chosen at runtime; selects which of the type's two Injury ratings applies.
    two_handed: bool = False
    #: Creature types this weapon was wrought against (``13.7.2``).
    banes: frozenset[CreatureType] = frozenset()


@dataclass(slots=True)
class ArmourInstance(_Instance):
    """Body armour or a helm — the same shape, distinguished by the type's category."""


@dataclass(slots=True)
class ShieldInstance(_Instance):
    #: Set by the Break Shield special damage (``12.5``).
    smashed: bool = False

    @property
    def unsmashable(self) -> bool:
        """A shield enhanced by a Reward or a magical quality cannot be smashed."""
        return self.plot_immune


@dataclass(frozen=True, slots=True)
class UsefulItem:
    """Grants +1d on a **non-combat** roll using its Skill (``03.5.2``).

    Subject to the Loremaster's approval, and only one item may benefit a given roll. How
    many a hero may carry is set by Standard of Living. Not Load-rated.
    """

    description: str
    skill: AbilityId


@dataclass(slots=True)
class Mount:
    """Reduces end-of-journey Fatigue by its Vigour (``10.6``).

    Quality normally follows the owner's Standard of Living, but at least one Cultural
    Virtue grants a pony above the usual maximum — so Vigour is instance data, not a
    lookup.
    """

    name: str
    vigour: int
    treasure_capacity: int = 10


@dataclass(slots=True)
class Gear:
    """What a hero carries.

    Body armour and helm are **separate slots** with separate Protection values, because a
    helm may be dropped mid-combat to shed Load. The PROTECTION roll uses their sum.
    """

    weapons: list[WeaponInstance] = field(default_factory=list)
    armour: ArmourInstance | None = None
    helm: ArmourInstance | None = None
    shield: ShieldInstance | None = None

    def carried(self) -> list[_Instance]:
        """Every item currently borne — a dropped item weighs nothing."""
        items: list[_Instance] = [w for w in self.weapons if not w.dropped]
        for slot in (self.armour, self.helm, self.shield):
            if slot is not None and not slot.dropped:
                items.append(slot)
        return items
