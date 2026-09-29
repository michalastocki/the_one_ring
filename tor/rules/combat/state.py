"""Combat state: stances, combatants, the round's expiring flags (``08.1``).

Nothing here rolls a die or fires a hook. It is the shape the rest of the package moves
through, and the one place the round boundary is defined.

.. note::
   ``08.1`` gives ``Combatant`` an ``is_hero: bool`` field beside ``ref``. It is derived
   here instead, from the aggregate the combatant wraps, because a stored flag can
   disagree with the thing it describes and nothing would notice. ``08.1`` also keeps the
   hero and adversary aggregates outside the state; they are carried on the combatant, so
   a rule that has the combatant has everything it needs about them and never has to be
   handed a second mapping to look the actor up in.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from tor.effects.hooks import DEFAULT_ENVIRONMENT, Environment
from tor.errors import RuleViolation, StateError
from tor.events import Event
from tor.model.adversary import AdversaryInstance, AdversarySize
from tor.model.hero import Hero
from tor.model.ids import CombatantId

__all__ = [
    "CLOSE_STANCES",
    "STANCE_ORDER",
    "Advantage",
    "CombatPhase",
    "CombatState",
    "Combatant",
    "Complication",
    "Duration",
    "Interference",
    "RoundFlags",
    "Stance",
    "stance_order_key",
]


class Stance(StrEnum):
    """Where a hero stands (``08.2.3``). Adversaries have none — see :class:`Combatant`."""

    FORWARD = "forward"
    OPEN = "open"
    DEFENSIVE = "defensive"
    REARWARD = "rearward"


#: The three that put a hero in close combat. Rearward is ranged only.
CLOSE_STANCES: tuple[Stance, ...] = (Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE)

#: Action resolution order within a side (``08.2.5``).
STANCE_ORDER: tuple[Stance, ...] = (
    Stance.FORWARD,
    Stance.OPEN,
    Stance.DEFENSIVE,
    Stance.REARWARD,
)


def stance_order_key(stance: Stance | None) -> int:
    """Sort key for ``08.2.5``. A combatant with no stance sorts last."""
    return STANCE_ORDER.index(stance) if stance is not None else len(STANCE_ORDER)


class CombatPhase(StrEnum):
    ONSET = "onset"
    VOLLEYS = "volleys"
    ROUND_STANCE = "round_stance"
    ROUND_ENGAGE = "round_engage"
    ROUND_ACTIONS = "round_actions"
    RESOLVED = "resolved"


class Duration(StrEnum):
    """How long a complication or advantage lasts (``08.11``)."""

    NEXT_ATTACK = "next_attack"
    REST_OF_FIGHT = "rest_of_fight"


class Interference(StrEnum):
    """The four levels of ``08.11``, which modify **all rolls made by the heroes**.

    One enum rather than two, because the table is one table: the levels differ only in
    sign and magnitude, and a `Complication` is an `Advantage` with the sign flipped.
    """

    MODERATELY_HINDERED = "moderately_hindered"
    SEVERELY_HINDERED = "severely_hindered"
    MODERATE_ADVANTAGE = "moderate_advantage"
    GREATER_ADVANTAGE = "greater_advantage"

    @property
    def dice(self) -> int:
        """Signed dice: negative hinders, positive helps."""
        return _INTERFERENCE_DICE[self]


_INTERFERENCE_DICE: Mapping[Interference, int] = {
    Interference.MODERATELY_HINDERED: -1,
    Interference.SEVERELY_HINDERED: -2,
    Interference.MODERATE_ADVANTAGE: 1,
    Interference.GREATER_ADVANTAGE: 2,
}


@dataclass(slots=True)
class Complication:
    """Circumstances hindering every hero roll (``08.11``)."""

    level: Interference
    duration: Duration = Duration.REST_OF_FIGHT
    description: str = ""

    def __post_init__(self) -> None:
        if self.level.dice > 0:
            raise StateError(f"{self.level.value!r} helps rather than hinders; it is an Advantage")

    @property
    def dice(self) -> int:
        return self.level.dice


@dataclass(slots=True)
class Advantage:
    """Circumstances helping every hero roll (``08.11``)."""

    level: Interference
    duration: Duration = Duration.REST_OF_FIGHT
    description: str = ""

    def __post_init__(self) -> None:
        if self.level.dice < 0:
            raise StateError(
                f"{self.level.value!r} hinders rather than helps; it is a Complication"
            )

    @property
    def dice(self) -> int:
        return self.level.dice


@dataclass(slots=True)
class RoundFlags:
    """Everything that expires at the end of the round (``08.1``).

    Two of these ride on each combatant. ``current`` is what applies now; ``pending`` is
    what the *next* round will start with, because ``08.10``'s Rally Comrades and
    Intimidate Foe both grant their effect to the following round rather than this one.
    :meth:`Combatant.begin_round` swaps them, which is the **only** place any of this
    expires — no subsystem hand-manages it.
    """

    #: Fend Off (``08.6``): the combatant's own Parry, defensively, for the round.
    parry_bonus: int = 0
    #: Shield Thrust (``08.6``) and Intimidate Foe's Weary, applied to this combatant's
    #: own attack rolls.
    attack_penalty_dice: int = 0
    #: Rally Comrades and Prepare Shot (``08.10``).
    attack_bonus_dice: int = 0
    #: Intimidate Foe (``08.10``) — Weary on the affected creature's next attack roll.
    weary: bool = False
    #: Protect Companion (``08.10``): the *next* attack aimed at this combatant loses this
    #: many dice. Consumed by the first such attack rather than lasting the round.
    incoming_attack_penalty: int = 0
    #: ``08.12``: knockback is once per round, not once per attack.
    knockback_used: bool = False
    #: Prepare Shot spends itself on one ranged attack (``08.10``).
    prepared_shot: int = 0

    def clear(self) -> None:
        for name in self.__slots__:
            setattr(self, name, _BLANK_FLAGS_VALUES[name])


_BLANK_FLAGS_VALUES: Mapping[str, object] = {
    name: getattr(RoundFlags(), name) for name in RoundFlags.__slots__
}


@dataclass(slots=True)
class Combatant:
    """One participant, wrapping the aggregate it acts for (``08.1``)."""

    ref: CombatantId
    actor: Hero | AdversaryInstance
    #: Heroes only. An adversary inherits the stance of the hero it attacks, for ordering
    #: alone (``08.2.3``) — :meth:`inherited_stance` computes it rather than storing it.
    stance: Stance | None = None
    engaged_with: set[CombatantId] = field(default_factory=set)
    surprised: bool = False
    #: Must spend the next main action recovering position (``08.12``).
    knocked_back: bool = False
    fled: bool = False
    out_of_combat: bool = False
    #: ``12.5``: a seized hero fights only in Forward stance, with Brawling attacks.
    seized_by: CombatantId | None = None
    #: The adversary this combatant stood back from close combat to shoot at, or the hero
    #: an adversary is engaged with; used for ordering (``08.2.5``).
    attacking: CombatantId | None = None
    stood_back: bool = False
    round_flags: RoundFlags = field(default_factory=RoundFlags)
    pending_flags: RoundFlags = field(default_factory=RoundFlags)
    main_action_used: bool = False
    secondary_action_used: bool = False

    @property
    def is_hero(self) -> bool:
        return isinstance(self.actor, Hero)

    @property
    def hero(self) -> Hero:
        if not isinstance(self.actor, Hero):
            raise StateError(f"{self.ref} is an adversary, not a hero")
        return self.actor

    @property
    def adversary(self) -> AdversaryInstance:
        if not isinstance(self.actor, AdversaryInstance):
            raise StateError(f"{self.ref} is a hero, not an adversary")
        return self.actor

    @property
    def active(self) -> bool:
        """Still in the fight: not fled, not taken out, and not unconscious at zero."""
        if self.fled or self.out_of_combat:
            return False
        if isinstance(self.actor, AdversaryInstance):
            return self.actor.taken_out is None
        return self.actor.endurance > 0 and not self.actor.dying

    @property
    def size(self) -> AdversarySize:
        """Engagement limits key off this (``08.2.4``). Heroes are human-sized."""
        if isinstance(self.actor, AdversaryInstance):
            return self.actor.template.size
        return AdversarySize.HUMAN

    @property
    def in_close_combat(self) -> bool:
        return self.is_hero and self.stance in CLOSE_STANCES

    def effective_stance(self, state: CombatState) -> Stance | None:
        """The stance this combatant orders by (``08.2.5``).

        A hero's own; an adversary's is inherited from the hero it is attacking. One that
        stood back unengaged with a ranged weapon has none, and so resolves last.
        """
        if self.is_hero:
            return self.stance
        if self.stood_back or self.attacking is None:
            return None
        target = state.combatants.get(self.attacking)
        return None if target is None else target.stance

    def begin_round(self) -> None:
        """Swap the flag buckets and clear the per-round action budget (``08.1``).

        The pending bucket becomes current, so what Rally Comrades granted last round
        applies now, and a fresh bucket starts collecting for the round after.
        """
        self.round_flags, self.pending_flags = self.pending_flags, RoundFlags()
        self.main_action_used = False
        self.secondary_action_used = False
        if isinstance(self.actor, AdversaryInstance):
            self.actor.begin_round()

    def spend_main_action(self) -> None:
        if self.main_action_used:
            raise StateError(f"{self.ref} has already taken a main action this round")
        self.main_action_used = True

    def spend_secondary_action(self) -> None:
        if self.secondary_action_used:
            raise StateError(f"{self.ref} has already taken a secondary action this round")
        self.secondary_action_used = True


@dataclass(slots=True)
class CombatState:
    """One fight (``08.1``)."""

    heroes: dict[CombatantId, Combatant] = field(default_factory=dict)
    adversaries: dict[CombatantId, Combatant] = field(default_factory=dict)
    round_number: int = 0
    phase: CombatPhase = CombatPhase.ONSET
    environment: Environment = DEFAULT_ENVIRONMENT
    complications: list[Complication] = field(default_factory=list)
    advantages: list[Advantage] = field(default_factory=list)
    round_log: list[Event] = field(default_factory=list)
    #: ``08.10``: only one hero may attempt Rally Comrades in a given round.
    rallied_this_round: bool = False
    #: ``08.2.2``: opening volleys the Loremaster allowed. Zero is legal.
    volleys_allowed: int = 0

    @property
    def combatants(self) -> dict[CombatantId, Combatant]:
        return {**self.heroes, **self.adversaries}

    def combatant(self, ref: CombatantId) -> Combatant:
        found = self.combatants.get(ref)
        if found is None:
            raise StateError(f"{ref!r} is not in this fight")
        return found

    def active_heroes(self) -> list[Combatant]:
        return [c for c in self.heroes.values() if c.active]

    def active_adversaries(self) -> list[Combatant]:
        return [c for c in self.adversaries.values() if c.active]

    def all_active(self) -> Iterator[Combatant]:
        yield from self.active_heroes()
        yield from self.active_adversaries()

    @property
    def over(self) -> bool:
        """One side has nobody left standing."""
        return not self.active_heroes() or not self.active_adversaries()

    def hero_dice_modifier(self) -> int:
        """The net of every live complication and advantage (``08.11``).

        They modify **all rolls made by the heroes**, so this is read once per hero roll
        rather than being pushed onto each combatant.
        """
        return sum(c.dice for c in self.complications) + sum(a.dice for a in self.advantages)

    def consume_next_attack_interference(self) -> None:
        """Drop everything that lasted only until the next attack roll (``08.11``)."""
        self.complications = [
            c for c in self.complications if c.duration is not Duration.NEXT_ATTACK
        ]
        self.advantages = [a for a in self.advantages if a.duration is not Duration.NEXT_ATTACK]

    def add_hero(self, hero: Hero, *, stance: Stance | None = None) -> Combatant:
        if hero.id in self.heroes:
            raise RuleViolation(
                f"{hero.id} is already in this fight", rule_reference="combatant_once"
            )
        combatant = Combatant(ref=hero.id, actor=hero, stance=stance)
        self.heroes[hero.id] = combatant
        return combatant

    def add_adversary(self, instance: AdversaryInstance) -> Combatant:
        if instance.instance_id in self.adversaries:
            raise RuleViolation(
                f"{instance.instance_id} is already in this fight",
                rule_reference="combatant_once",
            )
        combatant = Combatant(ref=instance.instance_id, actor=instance)
        self.adversaries[instance.instance_id] = combatant
        return combatant

    def in_environment(self, environment: Environment) -> CombatState:
        return replace(self, environment=environment)
