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
from .models import BattleStance, RangeBand, Stance, TestOutcome, TestResult
from .resolver import CombatTest, Weapon


@dataclass(frozen=True)
class _StanceModifiers:
    """Numeric effect of one BattleStance."""

    attack_dice_modifier: int  # >0 = bonus Success Dice on own attacks, <0 = penalty dice
    parry_multiplier: int      # how many times parry_rating counts toward effective Defence
    defence_bonus: int = 0     # flat Defence bonus on top of the Parry contribution


_BATTLE_STANCE_MODIFIERS: dict[BattleStance, _StanceModifiers] = {
    BattleStance.FORWARD: _StanceModifiers(attack_dice_modifier=1, parry_multiplier=0),
    BattleStance.OPEN: _StanceModifiers(attack_dice_modifier=0, parry_multiplier=1),
    BattleStance.DEFENSIVE: _StanceModifiers(attack_dice_modifier=-1, parry_multiplier=2),
    BattleStance.REARWARD: _StanceModifiers(
        attack_dice_modifier=-1, parry_multiplier=1, defence_bonus=2
    ),
}

# Flat Defence penalty applied while Weary, on top of the Weary rule that
# already zeroes HOLLOW faces on this combatant's own Success Dice
# (forwarded to CombatTest as is_weary — see resolve_test()).
_FATIGUE_DEFENCE_PENALTY = 1


def _is_in_range(weapon: Weapon, attack_range: RangeBand) -> bool:
    """Whether *weapon* can be used at all at *attack_range*.

    Ranged weapons can be used at any RangeBand (with a penalty away from
    their optimal range — see :func:`_range_penalty_dice`). Melee weapons
    only reach an engaged target, i.e. RangeBand.SHORT.
    """
    if weapon.is_ranged:
        return True
    return attack_range == RangeBand.SHORT


def _range_penalty_dice(weapon: Weapon, attack_range: RangeBand) -> int:
    """Success Dice penalty for shooting *weapon* away from its optimal band.

    One penalty die per RangeBand step away from ``weapon.optimal_range``
    (e.g. a bow optimal at MEDIUM shot at LONG loses 1 die, at SHORT also
    loses 1 die — point-blank shooting is awkward too). Melee weapons and
    ranged weapons with no declared optimal range take no penalty.
    """
    if not weapon.is_ranged or weapon.optimal_range is None:
        return 0
    return abs(int(attack_range) - int(weapon.optimal_range))


def _can_fire(attacker: Combatant) -> bool:
    """Whether *attacker* is allowed to use their weapon at all this attack.

    A ranged weapon can only be fired from REARWARD stance — Rearward is
    reserved for making ranged attacks. Melee weapons have no such
    restriction (their reach is governed by RangeBand instead, in
    :func:`_is_in_range`).
    """
    if not attacker.weapon.is_ranged:
        return True
    return attacker.battle_stance == BattleStance.REARWARD


def _is_valid_target(attacker: Combatant, target: Combatant) -> bool:
    """Whether *attacker* is allowed to target *target* at all this attack.

    A combatant holding REARWARD stance is pulled back and covered by
    their allies — only a ranged weapon can reach them; melee attackers
    cannot target them regardless of declared RangeBand. (The tabletop
    rule lets an attacker spend a Hate point to ignore this protection;
    this engine doesn't model a Hate resource, so that override isn't
    available here.)
    """
    if target.battle_stance == BattleStance.REARWARD:
        return attacker.weapon.is_ranged
    return True


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
        This combatant's base Defence rating, before any Parry contribution
        from their current BattleStance (see :attr:`effective_defence`).
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
        Dice Stance (NORMAL / ENHANCED / WEAKENED) used for this combatant's
        own attacks. Not to be confused with *battle_stance*.
    battle_stance:
        Combat stance (FORWARD / OPEN / DEFENSIVE / REARWARD) held this
        round; see :attr:`effective_defence` and :attr:`attack_bonus_dice` /
        :attr:`attack_penalty_dice`. A ranged weapon can only be fired
        while holding REARWARD, and REARWARD in turn can only be reached
        by another ranged attack — see CombatRound._resolve_attack.
    parry_rating:
        This combatant's Parry bonus, counted toward Defence according to
        their current *battle_stance*.
    wounds:
        Number of Wounds suffered so far. Any Wound is serious enough to
        take a combatant out of the fight (see :attr:`is_out_of_action`)
        until it's treated — this engine doesn't model First Aid, so a
        wounded Combatant stays out of action for the rest of the Combat.

    Combat effects modeled here
    ----------------------------
    Wound:
        Incapacitates immediately — see :attr:`is_incapacitated`.
    Fatigue (Weary):
        Zeroes HOLLOW faces on this combatant's own Success Dice rolls
        (via *is_weary* forwarded into every CombatTest), and imposes a
        flat penalty to :attr:`effective_defence` — exhaustion makes you
        both a worse attacker and an easier target.
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
    battle_stance: BattleStance = BattleStance.OPEN
    parry_rating: int = 0
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
    def is_incapacitated(self) -> bool:
        """True once a Wound has taken this combatant out of the fight.

        A single Wound is serious enough to stop a combatant from fighting
        on (or being fought on) until it's treated, independent of any
        Endurance they have left.
        """
        return self.is_wounded

    @property
    def is_out_of_action(self) -> bool:
        """True once Endurance has hit 0 or a Wound has incapacitated this
        combatant — either way they can't act or be targeted."""
        return self.endurance_current <= 0 or self.is_incapacitated

    @property
    def effective_defence(self) -> int:
        """Defence rating after BattleStance Parry and Fatigue modifiers.

        FORWARD drops Parry entirely, OPEN counts it once, DEFENSIVE counts
        it twice, and REARWARD counts it once plus a flat +2 for standing
        back from the melee. Being Weary then subtracts a further flat
        penalty on top of whichever stance is active.
        """
        mods = _BATTLE_STANCE_MODIFIERS[self.battle_stance]
        fatigue_penalty = _FATIGUE_DEFENCE_PENALTY if self.is_weary else 0
        return (
            self.defence
            + mods.parry_multiplier * self.parry_rating
            + mods.defence_bonus
            - fatigue_penalty
        )

    @property
    def attack_bonus_dice(self) -> int:
        """Extra Success Dice this combatant's own attacks gain from their BattleStance."""
        return max(0, _BATTLE_STANCE_MODIFIERS[self.battle_stance].attack_dice_modifier)

    @property
    def attack_penalty_dice(self) -> int:
        """Success Dice removed from this combatant's own attacks by their BattleStance."""
        return max(0, -_BATTLE_STANCE_MODIFIERS[self.battle_stance].attack_dice_modifier)

    def take_damage(self, amount: int) -> None:
        """Reduce current Endurance by *amount*, floored at 0."""
        self.endurance_current = max(0, self.endurance_current - amount)

    def take_wound(self) -> None:
        """Register a Wound (a Piercing Blow got through the armour)."""
        self.wounds += 1


@dataclass
class AttackOutcome:
    """Result of a single combatant's attack within a Combat Round.

    *result* is None whenever the attack couldn't be made at all — no
    CombatTest is rolled in that case. Exactly one of *out_of_range*,
    *wrong_stance*, or *target_protected* is True when that happens:

    - out_of_range: a melee weapon can't reach beyond RangeBand.SHORT.
    - wrong_stance: a ranged weapon was used outside REARWARD stance.
    - target_protected: the target holds REARWARD stance and this
      attacker's weapon isn't ranged, so it can't reach them.
    """

    attacker: str
    target: str
    result: Optional[TestResult]
    damage_dealt: int = 0
    target_out_of_action: bool = False
    out_of_range: bool = False
    wrong_stance: bool = False
    target_protected: bool = False


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
        self,
        assignments: dict[str, str],
        rng: random.Random | None = None,
        *,
        ranges: Optional[dict[str, RangeBand]] = None,
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
        ranges:
            Maps attacker name -> RangeBand giving the distance to their
            declared target this round. A combatant absent from *ranges*
            is assumed engaged at RangeBand.SHORT. A melee weapon can't
            reach beyond SHORT — see :attr:`AttackOutcome.out_of_range`.

        Returns
        -------
        list[AttackOutcome]
            One entry per attack actually resolved, in initiative order.
        """
        ranges = ranges or {}
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
            attack_range = ranges.get(attacker.name, RangeBand.SHORT)
            outcomes.append(self._resolve_attack(attacker, target, attack_range, rng))
        return outcomes

    @staticmethod
    def _resolve_attack(
        attacker: Combatant,
        target: Combatant,
        attack_range: RangeBand,
        rng: random.Random | None,
    ) -> AttackOutcome:
        if not _can_fire(attacker):
            return AttackOutcome(
                attacker=attacker.name,
                target=target.name,
                result=None,
                target_out_of_action=target.is_out_of_action,
                wrong_stance=True,
            )

        if not _is_valid_target(attacker, target):
            return AttackOutcome(
                attacker=attacker.name,
                target=target.name,
                result=None,
                target_out_of_action=target.is_out_of_action,
                target_protected=True,
            )

        if not _is_in_range(attacker.weapon, attack_range):
            return AttackOutcome(
                attacker=attacker.name,
                target=target.name,
                result=None,
                target_out_of_action=target.is_out_of_action,
                out_of_range=True,
            )

        range_penalty = _range_penalty_dice(attacker.weapon, attack_range)
        test = CombatTest(
            ability_level=attacker.ability_level,
            attacker_strength_attribute=attacker.strength_attribute,
            target_defence=target.effective_defence,
            target_armor_rating=target.armor_rating,
            weapon=attacker.weapon,
            attacker_strength_value=attacker.strength_attribute,
            stance=attacker.stance,
            is_miserable=attacker.is_miserable,
            is_weary=attacker.is_weary,
            bonus_dice=attacker.attack_bonus_dice,
            penalty_dice=attacker.attack_penalty_dice + range_penalty,
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
        self,
        assignments: dict[str, str],
        rng: random.Random | None = None,
        *,
        ranges: Optional[dict[str, RangeBand]] = None,
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
        outcomes = combat_round.resolve(assignments, rng, ranges=ranges)
        self.history.append(outcomes)
        return outcomes
