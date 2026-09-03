"""Combat Round orchestration for The One Ring RPG dice engine.

Builds on :class:`~engine.dice.resolver.CombatTest` (a single attack roll)
to run a full Combat Round — initiative order, one attack per assigned
combatant, damage application, and the resulting Weary/Miserable/Wounded
state changes — and to run an Encounter across multiple rounds until one
side is defeated.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .action_die import ActionDie
from .models import Stance, TestOutcome, TestResult
from .resolver import CombatTest, Weapon


@dataclass
class Combatant:
    """A participant in a Combat Round.

    Endurance, Hope, Shadow and Wounds are tracked here so the
    Weary / Miserable / Wounded states can be derived automatically each
    round instead of being computed and passed in by hand on every
    CombatTest.

    Parameters
    ----------
    name:
        Display name; used to key attack assignments and results.
    ability_level:
        Rank of the combat skill used when this combatant attacks.
    strength_attribute:
        Governing Strength attribute value (used to derive the attack TN).
    defence:
        This combatant's Defence rating (added to an attacker's TN).
    armor_rating:
        Number of Success Dice rolled in a Break-Defence sub-test against
        this combatant.
    weapon:
        Weapon wielded when this combatant attacks.
    endurance_max, load, hope_max:
        Character-sheet values. *endurance_current* / *hope_current* default
        to their max on creation and are then tracked round to round.
    shadow_points:
        Current Shadow score, compared against *hope_current* for Miserable.
    stance:
        Stance used for this combatant's own attacks.
    wounds:
        Number of Wounds suffered so far.
    """

    name: str
    ability_level: int
    strength_attribute: int
    defence: int
    armor_rating: int
    weapon: Weapon
    endurance_max: int
    load: int = 0
    hope_max: int = 0
    endurance_current: Optional[int] = None
    hope_current: Optional[int] = None
    shadow_points: int = 0
    stance: Stance = Stance.NORMAL
    wounds: int = 0

    def __post_init__(self) -> None:
        if self.endurance_current is None:
            self.endurance_current = self.endurance_max
        if self.hope_current is None:
            self.hope_current = self.hope_max

    @property
    def is_weary(self) -> bool:
        """True once Endurance has dropped to or below Load."""
        return self.endurance_current <= self.load

    @property
    def is_miserable(self) -> bool:
        """True once Shadow has reached or exceeded current Hope."""
        return self.shadow_points >= self.hope_current

    @property
    def is_wounded(self) -> bool:
        return self.wounds > 0

    @property
    def is_out_of_action(self) -> bool:
        """True once Endurance has been reduced to 0 — can't act or be targeted."""
        return self.endurance_current <= 0

    def take_damage(self, amount: int) -> None:
        """Reduce current Endurance by *amount*, floored at 0."""
        self.endurance_current = max(0, self.endurance_current - amount)

    def take_wound(self) -> None:
        """Register a Wound (a Piercing Blow got through the armour)."""
        self.wounds += 1


@dataclass
class AttackOutcome:
    """Result of a single combatant's attack within a Combat Round."""

    attacker: str
    target: str
    result: TestResult
    damage_dealt: int = 0
    target_out_of_action: bool = False


@dataclass
class CombatRound:
    """A single Combat Round: one attack per assigned attacker, in initiative order.

    Parameters
    ----------
    round_number:
        1-based round index, for logging/display.
    combatants:
        Every combatant who might act or be targeted this round (both sides).
    """

    round_number: int
    combatants: list[Combatant]

    def roll_initiative(self, rng: random.Random | None = None) -> list[Combatant]:
        """Roll one Action Die per active combatant and sort best-first.

        GANDALF_RUNE acts first and EYE_OF_SAURON acts last, using the same
        comparison used for ENHANCED/WEAKENED action die selection. Ties keep
        input order (stable sort). Combatants already out of action are
        excluded.
        """
        action_die = ActionDie()
        active = [c for c in self.combatants if not c.is_out_of_action]
        rolled = [(c, action_die.roll(rng)) for c in active]
        rolled.sort(key=lambda pair: pair[1].comparison_key, reverse=True)
        return [c for c, _ in rolled]

    def resolve(
        self, assignments: dict[str, str], rng: random.Random | None = None
    ) -> list[AttackOutcome]:
        """Resolve every declared attack for this round, in initiative order.

        Parameters
        ----------
        assignments:
            Maps attacker name -> target name for every combatant attacking
            this round. A combatant absent from *assignments* holds their
            action. An attacker or target that is out of action by the time
            its turn comes up is skipped (e.g. felled earlier this round).
        rng:
            Optional seeded RNG for deterministic resolution.

        Returns
        -------
        list[AttackOutcome]
            One entry per attack actually resolved, in initiative order.
        """
        by_name = {c.name: c for c in self.combatants}
        order = self.roll_initiative(rng)

        outcomes: list[AttackOutcome] = []
        for attacker in order:
            target_name = assignments.get(attacker.name)
            if target_name is None:
                continue
            target = by_name.get(target_name)
            if target is None or attacker.is_out_of_action or target.is_out_of_action:
                continue
            outcomes.append(self._resolve_attack(attacker, target, rng))
        return outcomes

    @staticmethod
    def _resolve_attack(
        attacker: Combatant, target: Combatant, rng: random.Random | None
    ) -> AttackOutcome:
        test = CombatTest(
            ability_level=attacker.ability_level,
            attacker_strength_attribute=attacker.strength_attribute,
            target_defence=target.defence,
            target_armor_rating=target.armor_rating,
            weapon=attacker.weapon,
            attacker_strength_value=attacker.strength_attribute,
            stance=attacker.stance,
            is_miserable=attacker.is_miserable,
            is_weary=attacker.is_weary,
        )
        result = test.resolve(rng)

        damage_dealt = 0
        if result.outcome in (TestOutcome.SUCCESS, TestOutcome.AUTOMATIC_SUCCESS):
            damage_dealt = attacker.weapon.damage
            target.take_damage(damage_dealt)
            if (
                result.break_defense_triggered
                and result.break_defense_result is not None
                and result.break_defense_result.outcome == TestOutcome.FAILURE
            ):
                target.take_wound()

        return AttackOutcome(
            attacker=attacker.name,
            target=target.name,
            result=result,
            damage_dealt=damage_dealt,
            target_out_of_action=target.is_out_of_action,
        )


@dataclass
class CombatEncounter:
    """Runs a Combat across multiple rounds until one side is defeated.

    Parameters
    ----------
    heroes, enemies:
        The two sides of the encounter.
    max_rounds:
        Safety cap so a stalemate (e.g. every attacker missing forever)
        can't loop forever; :meth:`run_round` beyond this raises
        RuntimeError.
    """

    heroes: list[Combatant]
    enemies: list[Combatant]
    max_rounds: int = 20
    history: list[list[AttackOutcome]] = field(default_factory=list)

    @property
    def round_number(self) -> int:
        """Number of the round about to be (or just) played, 1-based."""
        return len(self.history) + 1

    @property
    def is_over(self) -> bool:
        return self._side_defeated(self.heroes) or self._side_defeated(self.enemies)

    @property
    def winner(self) -> Optional[str]:
        """``"heroes"``, ``"enemies"``, or None while undecided (or a draw)."""
        heroes_down = self._side_defeated(self.heroes)
        enemies_down = self._side_defeated(self.enemies)
        if heroes_down and enemies_down:
            return None  # mutual defeat
        if enemies_down:
            return "heroes"
        if heroes_down:
            return "enemies"
        return None

    @staticmethod
    def _side_defeated(side: list[Combatant]) -> bool:
        return all(c.is_out_of_action for c in side)

    def run_round(
        self, assignments: dict[str, str], rng: random.Random | None = None
    ) -> list[AttackOutcome]:
        """Play one Combat Round and append it to the encounter history.

        Raises
        ------
        RuntimeError:
            If the encounter is already over, or *max_rounds* is exceeded.
        """
        if self.is_over:
            raise RuntimeError("Cannot run a round: the encounter is already over.")
        if self.round_number > self.max_rounds:
            raise RuntimeError(
                f"Combat exceeded max_rounds ({self.max_rounds}) without a resolution."
            )

        combat_round = CombatRound(
            round_number=self.round_number, combatants=self.heroes + self.enemies
        )
        outcomes = combat_round.resolve(assignments, rng)
        self.history.append(outcomes)
        return outcomes
