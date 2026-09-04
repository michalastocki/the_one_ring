"""Adversaries (``03.9``, ``12``).

A deliberately simplified sheet: one Attribute Level instead of three Attributes, one
drive pool instead of Hope and Shadow, no stances, no Skills. Several rules depend on that
simplification, so resist giving them a hero sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tor.errors import RuleViolation
from tor.model.conditions import CreatureType, DriveKind, ResourcePool
from tor.model.ids import AdversaryId, AdversaryInstanceId, EffectId

__all__ = [
    "Adversary",
    "AdversaryAttack",
    "AdversaryInstance",
    "AdversarySize",
    "TakenOutReason",
]


class AdversarySize(StrEnum):
    """Drives the engagement limits of ``08.2.4``."""

    HUMAN = "human"
    LARGE = "large"


class TakenOutReason(StrEnum):
    """Kept apart so the Loremaster can rule a creature alive but incapacitated (``12.3``)."""

    ENDURANCE = "endurance"
    SLAIN = "slain"


@dataclass(frozen=True, slots=True)
class AdversaryAttack:
    name: str
    rating: int
    damage: int
    injury: int
    #: Icon-spend options available *with this attack form*. Heavy Blow is added
    #: implicitly by the engine and must never be listed in content (``05.7``).
    special_damage: tuple[str, ...] = ()
    ranged: bool = False


@dataclass(frozen=True, slots=True)
class Adversary:
    """A stat block, as the content pack defines it."""

    id: AdversaryId
    name: str
    #: One value replacing the three hero Attributes. Used by Heavy Blow, Shield Thrust,
    #: the Hammering quality and several Fell Abilities — route every use through here.
    attribute_level: int
    endurance: int
    #: Double duty: Wounds needed to slay the creature, **and** attacks per round (``08.4``).
    might: int
    drive_kind: DriveKind
    drive: int
    #: A modifier added to the attacker's Target Number, not a TN in itself (``08.5``).
    parry: int
    #: PROTECTION dice, rolled when the creature is hit by a Piercing Blow.
    armour: int
    attacks: tuple[AdversaryAttack, ...] = ()
    always_available_special_damage: tuple[str, ...] = ()
    fell_abilities: tuple[EffectId, ...] = ()
    distinctive_features: tuple[EffectId, ...] = ()
    creature_types: frozenset[CreatureType] = frozenset()
    size: AdversarySize = AdversarySize.HUMAN
    #: Whether the two Feat die icons swap for this creature. Set from its category rather
    #: than universally: a Resolve-bearing foe who is no servant of the Shadow may roll
    #: normally, so it is data (``12.4``).
    icon_inverted: bool = True

    @property
    def hate(self) -> int | None:
        return self.drive if self.drive_kind is DriveKind.HATE else None

    @property
    def resolve(self) -> int | None:
        return self.drive if self.drive_kind is DriveKind.RESOLVE else None

    @property
    def misdeed_check_required(self) -> bool:
        """Killing a Resolve-bearing foe must always be weighed as a Misdeed (``12.2``)."""
        return self.drive_kind is DriveKind.RESOLVE


@dataclass(slots=True)
class AdversaryInstance:
    """One creature in a fight. Never share mutable state between copies (``12.8``)."""

    instance_id: AdversaryInstanceId
    template: Adversary
    endurance: int
    drive: ResourcePool
    wounds_taken: int = 0
    #: Granted by a Revelation episode; spent **before** the normal pool (``14.6``).
    bonus_drive: int = 0
    taken_out: TakenOutReason | None = None
    seized: str | None = None
    weary_this_round: bool = False

    @classmethod
    def of(cls, template: Adversary, instance_id: AdversaryInstanceId) -> AdversaryInstance:
        return cls(
            instance_id=instance_id,
            template=template,
            endurance=template.endurance,
            drive=ResourcePool(current=template.drive, maximum=template.drive),
        )

    @property
    def total_drive(self) -> int:
        return self.drive.current + self.bonus_drive

    def spend_drive(self, n: int = 1) -> None:
        """Spend from the bonus pool first, then the creature's own (``14.6``).

        The Loremaster is entitled to spend a creature's **last** point on a Fell Ability;
        there is deliberately no "keep one in reserve" guard (``12.2``).
        """
        if n < 0:
            raise ValueError(f"cannot spend a negative amount, got {n}")
        if n > self.total_drive:
            raise RuleViolation(
                f"cannot spend {n} drive: only {self.total_drive} left",
                rule_reference="adversary_drive_spending",
            )
        from_bonus = min(n, self.bonus_drive)
        self.bonus_drive -= from_bonus
        if remainder := n - from_bonus:
            self.drive.spend(remainder)

    def begin_round(self) -> None:
        """A creature that **begins** a round with zero drive is Weary for it (``12.2``).

        Checked here rather than at the moment the pool empties: spending the last point
        mid-round does not make the creature Weary until the next round starts. A creature
        holding bonus drive is not at zero.
        """
        self.weary_this_round = self.total_drive == 0

    def apply_wound(self) -> bool:
        """Record a Wound. Returns True if the creature is slain (``12.3``).

        Adversaries do not roll on the Wound Severity table and never carry the Wounded
        condition — they are slain once Wounds equal Might.
        """
        self.wounds_taken += 1
        slain = self.wounds_taken >= self.template.might
        if slain:
            self.taken_out = TakenOutReason.SLAIN
        return slain

    def take_endurance_loss(self, amount: int) -> None:
        """Adversaries do **not** become Weary from Endurance loss — only from empty drive.

        A genuine asymmetry with heroes; do not unify the two paths (``12.3``).
        """
        self.endurance = max(0, self.endurance - amount)
        if self.endurance == 0 and self.taken_out is None:
            self.taken_out = TakenOutReason.ENDURANCE
