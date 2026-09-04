"""The player-hero aggregate (``03.4``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from tor.errors import RuleViolation, RuleViolationGroup
from tor.model.abilities import (
    BRAWLING,
    COMBAT_PROFICIENCIES,
    SKILLS,
    ability_attribute,
    is_combat_proficiency,
)
from tor.model.attributes import Attribute, AttributeSet
from tor.model.conditions import (
    ConditionSet,
    StandardOfLiving,
    TreasureHoldings,
    standard_of_living_for,
)
from tor.model.derive import DerivedStat
from tor.model.gear import Gear, UsefulItem
from tor.model.ids import AbilityId, CallingId, CultureId, EffectId, HeroId

__all__ = ["Heir", "Hero", "RewardInstance"]

MAX_RATING = 6
MAX_RANK = 6
SHADOW_PATH_STEPS = 4


@dataclass(slots=True)
class RewardInstance:
    """A Reward is always bound to a specific item, never held loose (``15.4.1``)."""

    reward: EffectId
    item: str


@dataclass(slots=True)
class Heir:
    """A successor prepared by the *Raise an Heir* undertaking (``06.12``)."""

    name: str
    reserve: int = 0
    heritage_skill: AbilityId | None = None
    heirlooms: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.reserve >= 10


@dataclass(slots=True)
class Hero:
    """A player-hero.

    Derived stats — the three maxima, Parry, Load, Standard of Living, and the Weary and
    Miserable flags — are **not** independent fields. The maxima and Parry are
    :class:`~tor.model.derive.DerivedStat` so their modifiers stay visible and removable;
    Standard of Living is computed from Treasure; the conditions are recomputed by
    ``tor.rules.resources``.
    """

    id: HeroId
    name: str
    culture: CultureId
    calling: CallingId
    age: int

    attributes: AttributeSet
    #: All 18 Skills present, 0..6.
    skills: dict[AbilityId, int] = field(default_factory=dict)
    favoured_skills: set[AbilityId] = field(default_factory=set)
    #: All 4 Combat Proficiencies present, 0..6.
    proficiencies: dict[AbilityId, int] = field(default_factory=dict)
    valour: int = 1
    wisdom: int = 1

    max_endurance: DerivedStat = field(default_factory=lambda: DerivedStat(base=0))
    max_hope: DerivedStat = field(default_factory=lambda: DerivedStat(base=0))
    parry: DerivedStat = field(default_factory=lambda: DerivedStat(base=0))

    endurance: int = 0
    hope: int = 0
    shadow: int = 0
    shadow_scars: int = 0
    fatigue: int = 0
    #: Days until a Severe injury mends (``07.7``).
    injury_days: int = 0
    dying: bool = False

    conditions: ConditionSet = field(default_factory=ConditionSet)

    distinctive_features: list[EffectId] = field(default_factory=list)
    flaws: list[EffectId] = field(default_factory=list)
    #: Flaws inflicted temporarily by the Curse of Weakness do **not** count toward
    #: succumbing (``11.7``), so they are held apart from the Shadow Path's own.
    temporary_flaws: list[EffectId] = field(default_factory=list)
    virtues: list[EffectId] = field(default_factory=list)
    rewards: list[RewardInstance] = field(default_factory=list)
    shadow_path: EffectId | None = None
    shadow_path_step: int = 0

    gear: Gear = field(default_factory=Gear)
    useful_items: list[UsefulItem] = field(default_factory=list)
    treasure: TreasureHoldings = field(default_factory=TreasureHoldings)
    #: The tier the culture starts the hero at; the played tier derives from Treasure.
    starting_standard_of_living: StandardOfLiving = StandardOfLiving.COMMON

    skill_points: int = 0
    adventure_points: int = 0

    heir: Heir | None = None
    #: Directed, non-reciprocal, non-exclusive. Capped at one, raised to two by one
    #: Cultural Virtue (``03.6``).
    fellowship_focus: list[HeroId] = field(default_factory=list)

    # -- ratings ---------------------------------------------------------------------

    def rating(self, ability: AbilityId) -> int:
        """The number of Success dice this ability rolls.

        Brawling is not stored: it resolves to the **highest** Combat Proficiency, and the
        -1d it takes is a penalty die applied by the attack, not a lower rating (``03.3``).
        """
        if ability == BRAWLING:
            return self.brawling_rating()
        if ability in self.skills:
            return self.skills[ability]
        if ability in self.proficiencies:
            return self.proficiencies[ability]
        raise KeyError(f"{self.id} has no rating for {ability!r}")

    def brawling_rating(self) -> int:
        return max((self.proficiencies.get(p, 0) for p in COMBAT_PROFICIENCIES), default=0)

    def is_favoured(self, ability: AbilityId) -> bool:
        return ability in self.favoured_skills

    def set_favoured(self, ability: AbilityId, favoured: bool = True) -> None:
        """Mark a Skill Favoured.

        Combat Proficiencies **cannot** be Favoured (invariant I7) — one of the three ways
        they are structurally distinct from Skills.
        """
        if favoured and is_combat_proficiency(ability):
            raise RuleViolation(
                f"{ability!r} is a Combat Proficiency and cannot be Favoured",
                rule_reference="combat_proficiency_not_favourable",
            )
        if favoured:
            self.favoured_skills.add(ability)
        else:
            self.favoured_skills.discard(ability)

    def attribute_for(self, ability: AbilityId) -> Attribute:
        return ability_attribute(ability)

    # -- derived ---------------------------------------------------------------------

    def standard_of_living(
        self, ladder: Sequence[tuple[StandardOfLiving, int | None]]
    ) -> StandardOfLiving:
        """Derived from **total** Treasure holdings, cached or not (``03.7``, ``07.4``)."""
        derived = standard_of_living_for(self.treasure.total, ladder)
        return max(derived, self.starting_standard_of_living)

    @property
    def permanent_flaw_count(self) -> int:
        """Flaws that count toward succumbing — temporary ones are excluded (``11.7``)."""
        return len(self.flaws)

    # -- invariants ------------------------------------------------------------------

    def validate(self) -> None:
        """Assert invariants I1-I10 (``03.4.1``). Reports every breach, not just the first."""
        breaches: list[RuleViolation] = []

        def breach(detail: str, rule: str) -> None:
            breaches.append(RuleViolation(detail, rule_reference=rule))

        for ability, value in (*self.skills.items(), *self.proficiencies.items()):
            if not 0 <= value <= MAX_RATING:  # I1
                breach(f"{ability} rating {value} is outside 0..{MAX_RATING}", "rating_range")

        for name, rank in (("valour", self.valour), ("wisdom", self.wisdom)):
            if not 1 <= rank <= MAX_RANK:  # I2
                breach(f"{name} {rank} is outside 1..{MAX_RANK}", "rank_range")

        if not 0 <= self.endurance <= self.max_endurance.value:  # I3
            breach(
                f"Endurance {self.endurance} is outside 0..{self.max_endurance.value}",
                "endurance_range",
            )
        if not 0 <= self.hope <= self.max_hope.value:  # I4
            breach(f"Hope {self.hope} is outside 0..{self.max_hope.value}", "hope_range")

        # I5 — the ceiling that makes "Shadow reaches maximum Hope" reachable but not
        # exceedable. Gains beyond it are discarded, never banked.
        if not 0 <= self.shadow <= self.max_hope.value:
            breach(
                f"Shadow {self.shadow} is outside 0..{self.max_hope.value} (maximum Hope)",
                "shadow_ceiling",
            )
        if self.shadow_scars > self.shadow:  # I6
            breach(
                f"{self.shadow_scars} Shadow Scars exceed a Shadow score of {self.shadow}",
                "shadow_scars_subset",
            )

        if stray := {a for a in self.favoured_skills if a not in SKILLS}:  # I7
            breach(f"only Skills may be Favoured, not {sorted(stray)}", "favoured_skills_only")

        if not 0 <= self.shadow_path_step <= SHADOW_PATH_STEPS:  # I8
            breach(
                f"Shadow Path step {self.shadow_path_step} is outside 0..{SHADOW_PATH_STEPS}",
                "shadow_path_step_range",
            )
        if len(self.flaws) != self.shadow_path_step:  # I8
            breach(
                f"{len(self.flaws)} Flaws recorded but Shadow Path step is {self.shadow_path_step}",
                "flaws_match_path_step",
            )

        # I9 (Treasure is non-negative) needs no check here: TreasureHoldings rejects a
        # negative component at construction, so the total cannot go below zero.

        seen: set[tuple[str, str]] = set()
        for reward in self.rewards:  # I10
            key = (reward.item, str(reward.reward))
            if key in seen:
                breach(
                    f"{reward.reward} is applied twice to {reward.item}",
                    "reward_once_per_item",
                )
            seen.add(key)

        if self.fatigue < 0:
            breach("Fatigue cannot be negative", "fatigue_non_negative")

        if breaches:
            raise RuleViolationGroup(breaches, rule_reference="hero_invariants")
