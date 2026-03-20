"""Core test-resolution logic for The One Ring RPG dice engine.

Provides:
  - resolve_test()   — the universal six-step algorithm
  - SkillTest        — Skill / attribute test
  - CombatTest       — Attack test including Break-Defence sub-test
  - ShadowTest       — Corruption / Shadow test
  - MagicalTest      — Magic-ability test (no Action Die)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    ActionDieRoll,
    DieResult,
    Stance,
    SuccessDieRoll,
    SuccessQuality,
    TestOutcome,
    TestResult,
    WeaponType,
)
from .roll_pool import RollPool


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _count_tengwar(success_dice: list[SuccessDieRoll]) -> int:
    return sum(1 for d in success_dice if d.is_tengwar)


def _success_quality(tengwar_count: int) -> SuccessQuality:
    if tengwar_count == 0:
        return SuccessQuality.BASIC_SUCCESS
    if tengwar_count == 1:
        return SuccessQuality.GREAT_SUCCESS
    return SuccessQuality.EXTRAORDINARY_SUCCESS


# ---------------------------------------------------------------------------
# Core algorithm — resolve_test()
# ---------------------------------------------------------------------------

def resolve_test(
    ability_level: int,
    target_number: int,
    stance: Stance = Stance.NORMAL,
    is_miserable: bool = False,
    is_weary: bool = False,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
    rng: random.Random | None = None,
) -> TestResult:
    """Resolve a standard test following the six-step algorithm.

    Parameters
    ----------
    ability_level:
        Base number of Success Dice (0–6, the tested skill rank).
    target_number:
        The Difficulty Number that must be met or exceeded.
    stance:
        NORMAL / ENHANCED (keep best AD) / WEAKENED (keep worst AD).
    is_miserable:
        If True, EYE_OF_SAURON on the Action Die → automatic failure.
    is_weary:
        If True, HOLLOW faces (1–3) on Success Dice count as 0.
    bonus_dice:
        Extra Success Dice (Hope, companion support …).
    penalty_dice:
        Success Dice removed from the pool (circumstance penalties …).
    rng:
        Optional seeded :class:`random.Random` for deterministic tests.

    Returns
    -------
    TestResult
        Fully populated result including outcome, quality, and raw dice.
    """
    # Step 1 & 2: Build pool (handles stance, modifiers, WEARY/MISERABLE flags)
    pool = RollPool(
        ability_level=ability_level,
        stance=stance,
        is_miserable=is_miserable,
        is_weary=is_weary,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
    )

    # Step 3: Execute roll
    action_die, success_dice = pool.roll(rng)

    # Step 4a: Immediate GANDALF_RUNE check
    tengwar_count = _count_tengwar(success_dice)
    if action_die.result == DieResult.GANDALF_RUNE:
        return TestResult(
            outcome=TestOutcome.AUTOMATIC_SUCCESS,
            success_quality=_success_quality(tengwar_count),
            action_die=action_die,
            success_dice=success_dice,
            total=0,  # irrelevant for auto-success
            target_number=target_number,
            tengwar_count=tengwar_count,
        )

    # Step 4b: Immediate EYE + MISERABLE check
    if action_die.result == DieResult.EYE_OF_SAURON and is_miserable:
        return TestResult(
            outcome=TestOutcome.AUTOMATIC_FAILURE,
            success_quality=None,
            action_die=action_die,
            success_dice=success_dice,
            total=0,
            target_number=target_number,
            tengwar_count=0,
        )

    # Step 5: Compute total and compare with TN
    ad_value = action_die.numeric_value  # 0 for EYE, 1–10 for normal
    success_total = sum(d.value for d in success_dice)
    total = ad_value + success_total

    outcome = TestOutcome.SUCCESS if total >= target_number else TestOutcome.FAILURE

    # Step 6: Determine quality
    quality = _success_quality(tengwar_count) if outcome == TestOutcome.SUCCESS else None

    return TestResult(
        outcome=outcome,
        success_quality=quality,
        action_die=action_die,
        success_dice=success_dice,
        total=total,
        target_number=target_number,
        tengwar_count=tengwar_count,
    )


# ---------------------------------------------------------------------------
# ST-5: Skill Test
# ---------------------------------------------------------------------------

@dataclass
class SkillTest:
    """Skill (attribute) test.

    The Target Number is derived from the character's attribute value:
        TN = 20 − attribute_value

    Parameters
    ----------
    ability_level:
        Rank of the tested skill (0–6).
    attribute_value:
        The governing attribute (Strength / Heart / Wits).
    stance:
        Stance modifier.
    is_miserable:
        Character state.
    is_weary:
        Character state.
    bonus_dice:
        Extra Success Dice.
    penalty_dice:
        Penalty dice removed from pool.
    """

    ability_level: int
    attribute_value: int
    stance: Stance = Stance.NORMAL
    is_miserable: bool = False
    is_weary: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0

    @property
    def target_number(self) -> int:
        return 20 - self.attribute_value

    def resolve(self, rng: random.Random | None = None) -> TestResult:
        """Execute and return the test result."""
        return resolve_test(
            ability_level=self.ability_level,
            target_number=self.target_number,
            stance=self.stance,
            is_miserable=self.is_miserable,
            is_weary=self.is_weary,
            bonus_dice=self.bonus_dice,
            penalty_dice=self.penalty_dice,
            rng=rng,
        )


# ---------------------------------------------------------------------------
# ST-7: Shadow Test
# ---------------------------------------------------------------------------

@dataclass
class ShadowTest:
    """Corruption / Shadow test.

    Uses Wisdom (governed by Heart TN) or Valour (governed by Strength TN).

    Parameters
    ----------
    ability_level:
        Rank of the Wisdom or Valour skill.
    attribute_value:
        The governing attribute value (Heart for Wisdom, Strength for Valour).
    stance, is_miserable, is_weary, bonus_dice, penalty_dice:
        Standard modifiers (same semantics as :class:`SkillTest`).
    """

    ability_level: int
    attribute_value: int
    stance: Stance = Stance.NORMAL
    is_miserable: bool = False
    is_weary: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0

    @property
    def target_number(self) -> int:
        return 20 - self.attribute_value

    def resolve(self, rng: random.Random | None = None) -> TestResult:
        """Execute and return the test result (including shadow_reduction)."""
        result = resolve_test(
            ability_level=self.ability_level,
            target_number=self.target_number,
            stance=self.stance,
            is_miserable=self.is_miserable,
            is_weary=self.is_weary,
            bonus_dice=self.bonus_dice,
            penalty_dice=self.penalty_dice,
            rng=rng,
        )
        # Successful Shadow test reduces incoming Shadow gain.
        if result.outcome in (TestOutcome.SUCCESS, TestOutcome.AUTOMATIC_SUCCESS):
            result.shadow_reduction = 1 + result.tengwar_count
        return result


# ---------------------------------------------------------------------------
# ST-8: Magical Test
# ---------------------------------------------------------------------------

@dataclass
class MagicalTest:
    """Magical-ability test.

    Rules:
    - Requires spending 1 Hope point **before** the roll.
    - No Action Die is rolled.
    - Outcome is always an automatic success.
    - Tengwar are counted normally for success quality.

    Parameters
    ----------
    ability_level:
        Number of Success Dice rolled (magical skill rank ± modifiers).
    is_weary:
        WEARY rule still applies to Success Dice.
    bonus_dice, penalty_dice:
        Standard pool modifiers (clamped ≥ 0).
    """

    ability_level: int
    is_weary: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0

    def resolve(self, rng: random.Random | None = None) -> TestResult:
        """Execute the magical test.  No Action Die; always succeeds."""
        from .success_die import SuccessDie

        success_count = max(0, self.ability_level + self.bonus_dice - self.penalty_dice)
        die = SuccessDie()
        success_dice = [die.roll(is_weary=self.is_weary, rng=rng) for _ in range(success_count)]
        tengwar_count = _count_tengwar(success_dice)

        return TestResult(
            outcome=TestOutcome.AUTOMATIC_SUCCESS,
            success_quality=_success_quality(tengwar_count),
            action_die=None,  # no Action Die in magical tests
            success_dice=success_dice,
            total=sum(d.value for d in success_dice),
            target_number=0,
            tengwar_count=tengwar_count,
        )


# ---------------------------------------------------------------------------
# ST-6: Combat Test (Attack) + Break-Defence sub-test
# ---------------------------------------------------------------------------

@dataclass
class Weapon:
    """Minimal weapon stat block needed for combat resolution.

    Parameters
    ----------
    damage:
        Endurance points lost by the target on a successful hit.
    piercing_value:
        TN for the Break-Defence sub-test against the target's Armour.
    weapon_type:
        Category used to determine Tengwar spending effects.
    is_two_handed:
        True for two-handed weapons (grants +1 Strength damage on Mighty Blow).
    """

    damage: int
    piercing_value: int
    weapon_type: WeaponType = WeaponType.SWORD
    is_two_handed: bool = False


@dataclass
class CombatTest:
    """Attack test in structured combat.

    Target Number for the attack:
        TN = attacker_strength_tn + target_defence

    where ``attacker_strength_tn = 20 - attacker_strength_attribute``.

    Break-Defence sub-test is triggered when:
    - The attack succeeds (or is an automatic success), AND
    - The Action Die showed 10 or GANDALF_RUNE.

    Parameters
    ----------
    ability_level:
        Rank of the attack skill used.
    attacker_strength_attribute:
        The attacker's Strength attribute value.
    target_defence:
        The target's Defence rating (added to TN).
    target_armor_rating:
        Number of Success Dice rolled in the Break-Defence sub-test.
    weapon:
        Weapon stats.
    attacker_strength_value:
        Raw Strength score (used for Mighty Blow damage).
    stance, is_miserable, is_weary, bonus_dice, penalty_dice:
        Standard modifiers.
    """

    ability_level: int
    attacker_strength_attribute: int
    target_defence: int
    target_armor_rating: int
    weapon: Weapon
    attacker_strength_value: int = 0
    stance: Stance = Stance.NORMAL
    is_miserable: bool = False
    is_weary: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0

    @property
    def target_number(self) -> int:
        strength_tn = 20 - self.attacker_strength_attribute
        return strength_tn + self.target_defence

    def resolve(self, rng: random.Random | None = None) -> TestResult:
        """Execute the attack roll and, if applicable, the Break-Defence roll."""
        result = resolve_test(
            ability_level=self.ability_level,
            target_number=self.target_number,
            stance=self.stance,
            is_miserable=self.is_miserable,
            is_weary=self.is_weary,
            bonus_dice=self.bonus_dice,
            penalty_dice=self.penalty_dice,
            rng=rng,
        )

        if result.outcome in (TestOutcome.SUCCESS, TestOutcome.AUTOMATIC_SUCCESS):
            result = self._check_break_defence(result, rng)

        return result

    def _check_break_defence(
        self, attack_result: TestResult, rng: random.Random | None
    ) -> TestResult:
        """Determine whether Break-Defence is triggered and resolve it."""
        ad = attack_result.action_die
        triggers = (
            ad is not None
            and (
                ad.result == DieResult.GANDALF_RUNE
                or (isinstance(ad.result, int) and ad.result == 10)
            )
        )

        if not triggers:
            return attack_result

        attack_result.break_defense_triggered = True
        attack_result.break_defense_result = self._resolve_break_defence(rng)
        return attack_result

    def _resolve_break_defence(self, rng: random.Random | None) -> TestResult:
        """Run the armour / Break-Defence sub-test.

        The target rolls ``target_armor_rating`` Success Dice against
        ``weapon.piercing_value`` as TN (no Action Die).
        If the armour roll fails, the target receives a Wound.
        """
        from .success_die import SuccessDie

        die = SuccessDie()
        armor_dice = [die.roll(rng=rng) for _ in range(self.target_armor_rating)]
        armor_total = sum(d.value for d in armor_dice)
        tengwar = _count_tengwar(armor_dice)

        if armor_total >= self.weapon.piercing_value:
            outcome = TestOutcome.SUCCESS  # armour held → no Wound
            quality = _success_quality(tengwar)
        else:
            outcome = TestOutcome.FAILURE   # armour failed → Wound inflicted
            quality = None

        return TestResult(
            outcome=outcome,
            success_quality=quality,
            action_die=None,
            success_dice=armor_dice,
            total=armor_total,
            target_number=self.weapon.piercing_value,
            tengwar_count=tengwar,
        )
