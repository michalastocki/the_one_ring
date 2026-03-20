"""Unit tests for resolve_test() and the four test types (ST-4 to ST-8)."""

import pytest

from engine.dice import (
    Stance,
    TestOutcome,
    resolve_test,
    SkillTest,
    ShadowTest,
    MagicalTest,
    CombatTest,
)
from engine.dice.models import DieResult, SuccessQuality
from engine.dice.resolver import Weapon
from engine.dice.models import WeaponType


# ---------------------------------------------------------------------------
# Shared test fixture helpers
# ---------------------------------------------------------------------------

class SequentialRandom:
    """Returns values from a predetermined sequence on each randint() call."""

    def __init__(self, values: list[int]):
        self._values = values
        self._index = 0

    def randint(self, _a: int, _b: int) -> int:
        v = self._values[self._index % len(self._values)]
        self._index += 1
        return v


# ---------------------------------------------------------------------------
# resolve_test — basic contracts
# ---------------------------------------------------------------------------

class TestResolveTestBasicOutcomes:

    def test_success_when_total_meets_tn(self):
        # AD=8, 1×SD=5 → total=13, TN=13 → success
        rng = SequentialRandom([8, 5])
        result = resolve_test(ability_level=1, target_number=13, rng=rng)
        assert result.outcome == TestOutcome.SUCCESS

    def test_failure_when_total_below_tn(self):
        # AD=3, 1×SD=2 → total=5, TN=13 → failure
        rng = SequentialRandom([3, 2])
        result = resolve_test(ability_level=1, target_number=13, rng=rng)
        assert result.outcome == TestOutcome.FAILURE

    def test_success_exactly_at_tn(self):
        rng = SequentialRandom([10, 3])  # 10+3=13, TN=13
        result = resolve_test(ability_level=1, target_number=13, rng=rng)
        assert result.outcome == TestOutcome.SUCCESS

    def test_no_success_dice_uses_only_ad(self):
        rng = SequentialRandom([10])  # AD=10, 0 SD
        result = resolve_test(ability_level=0, target_number=10, rng=rng)
        assert result.outcome == TestOutcome.SUCCESS
        assert result.total == 10

    def test_total_computed_correctly(self):
        # AD=7, SD=[3,4] → total=14
        rng = SequentialRandom([7, 3, 4])
        result = resolve_test(ability_level=2, target_number=1, rng=rng)
        assert result.total == 14


class TestResolveTestGandalfRune:

    def test_gandalf_is_automatic_success(self):
        # AD=12 (GANDALF), SD=1 → automatic success regardless of TN
        rng = SequentialRandom([12, 1])
        result = resolve_test(ability_level=1, target_number=999, rng=rng)
        assert result.outcome == TestOutcome.AUTOMATIC_SUCCESS

    def test_gandalf_counts_tengwar(self):
        # AD=12, SD=[6,6] → 2 tengwar → EXTRAORDINARY_SUCCESS
        rng = SequentialRandom([12, 6, 6])
        result = resolve_test(ability_level=2, target_number=999, rng=rng)
        assert result.tengwar_count == 2
        assert result.success_quality == SuccessQuality.EXTRAORDINARY_SUCCESS

    def test_gandalf_no_tengwar_basic_success(self):
        rng = SequentialRandom([12, 3])
        result = resolve_test(ability_level=1, target_number=999, rng=rng)
        assert result.tengwar_count == 0
        assert result.success_quality == SuccessQuality.BASIC_SUCCESS


class TestResolveTestEyeOfSauron:

    def test_eye_contributes_zero_to_total(self):
        # AD=11 (EYE), 1×SD=5 → total=5
        rng = SequentialRandom([11, 5])
        result = resolve_test(ability_level=1, target_number=5, rng=rng)
        assert result.total == 5
        assert result.outcome == TestOutcome.SUCCESS

    def test_eye_fails_normally_when_total_below_tn(self):
        rng = SequentialRandom([11, 2])  # 0+2=2 < 10
        result = resolve_test(ability_level=1, target_number=10, rng=rng)
        assert result.outcome == TestOutcome.FAILURE

    def test_eye_plus_miserable_is_automatic_failure(self):
        rng = SequentialRandom([11, 6])  # would succeed otherwise
        result = resolve_test(
            ability_level=1, target_number=5, is_miserable=True, rng=rng
        )
        assert result.outcome == TestOutcome.AUTOMATIC_FAILURE
        assert result.tengwar_count == 0

    def test_eye_without_miserable_is_not_automatic_failure(self):
        rng = SequentialRandom([11, 6])  # 0+6=6 >= 5 → success despite EYE
        result = resolve_test(
            ability_level=1, target_number=5, is_miserable=False, rng=rng
        )
        assert result.outcome == TestOutcome.SUCCESS


class TestResolveTestWearyRule:

    def test_weary_hollow_counts_as_zero(self):
        # AD=5, SD=2(HOLLOW → 0 when WEARY) → total=5
        rng = SequentialRandom([5, 2])
        result = resolve_test(ability_level=1, target_number=1, is_weary=True, rng=rng)
        assert result.total == 5

    def test_weary_filled_unchanged(self):
        # AD=5, SD=5(FILLED) → total=10
        rng = SequentialRandom([5, 5])
        result = resolve_test(ability_level=1, target_number=1, is_weary=True, rng=rng)
        assert result.total == 10

    def test_weary_tengwar_unchanged(self):
        rng = SequentialRandom([5, 6])  # SD=6 → TENGWAR, still 6
        result = resolve_test(ability_level=1, target_number=1, is_weary=True, rng=rng)
        assert result.tengwar_count == 1
        assert result.total == 11  # 5 + 6


class TestSuccessQuality:

    def _result_with_tengwar(self, n: int):
        # Use ability_level=n, all SD=6, AD=5, TN=1
        rng = SequentialRandom([5] + [6] * n)
        return resolve_test(ability_level=n, target_number=1, rng=rng)

    def test_zero_tengwar_is_basic_success(self):
        rng = SequentialRandom([5, 3])
        r = resolve_test(ability_level=1, target_number=1, rng=rng)
        assert r.success_quality == SuccessQuality.BASIC_SUCCESS

    def test_one_tengwar_is_great_success(self):
        r = self._result_with_tengwar(1)
        assert r.success_quality == SuccessQuality.GREAT_SUCCESS

    def test_two_tengwar_is_extraordinary_success(self):
        r = self._result_with_tengwar(2)
        assert r.success_quality == SuccessQuality.EXTRAORDINARY_SUCCESS

    def test_three_tengwar_is_extraordinary_success(self):
        r = self._result_with_tengwar(3)
        assert r.success_quality == SuccessQuality.EXTRAORDINARY_SUCCESS

    def test_failure_has_no_quality(self):
        rng = SequentialRandom([1, 1])
        r = resolve_test(ability_level=1, target_number=999, rng=rng)
        assert r.outcome == TestOutcome.FAILURE
        assert r.success_quality is None


class TestStanceModifiersInResolve:

    def test_enhanced_picks_gandalf_over_ten(self):
        # AD1=10, AD2=12 → GANDALF wins
        rng = SequentialRandom([10, 12])
        r = resolve_test(
            ability_level=0, target_number=999, stance=Stance.ENHANCED, rng=rng
        )
        assert r.outcome == TestOutcome.AUTOMATIC_SUCCESS

    def test_weakened_picks_eye(self):
        # AD1=11(EYE), AD2=8; WEAKENED → EYE; MISERABLE → auto-failure
        rng = SequentialRandom([11, 8])
        r = resolve_test(
            ability_level=0,
            target_number=1,
            stance=Stance.WEAKENED,
            is_miserable=True,
            rng=rng,
        )
        assert r.outcome == TestOutcome.AUTOMATIC_FAILURE


class TestBonusPenaltyDice:

    def test_bonus_dice_increase_pool(self):
        rng = SequentialRandom([5, 4, 4])  # AD=5, 2 SD (ability=1 + bonus=1)
        r = resolve_test(ability_level=1, target_number=1, bonus_dice=1, rng=rng)
        assert len(r.success_dice) == 2

    def test_penalty_reduces_pool(self):
        rng = SequentialRandom([5])  # AD=5 only, pool=0
        r = resolve_test(ability_level=1, target_number=1, penalty_dice=1, rng=rng)
        assert len(r.success_dice) == 0

    def test_pool_minimum_zero(self):
        rng = SequentialRandom([5])
        r = resolve_test(ability_level=0, target_number=1, penalty_dice=10, rng=rng)
        assert len(r.success_dice) == 0


# ---------------------------------------------------------------------------
# SkillTest
# ---------------------------------------------------------------------------

class TestSkillTest:

    def test_target_number_derived_from_attribute(self):
        st = SkillTest(ability_level=3, attribute_value=14)
        assert st.target_number == 6  # 20-14

    def test_success(self):
        # TN = 20 - 10 = 10; AD=8, SD=[3] → 11 >= 10
        rng = SequentialRandom([8, 3])
        st = SkillTest(ability_level=1, attribute_value=10)
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS

    def test_failure(self):
        rng = SequentialRandom([1, 1])
        st = SkillTest(ability_level=1, attribute_value=5)  # TN=15
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.FAILURE

    def test_miserable_flag_forwarded(self):
        rng = SequentialRandom([11])  # EYE
        st = SkillTest(ability_level=0, attribute_value=10, is_miserable=True)
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.AUTOMATIC_FAILURE


# ---------------------------------------------------------------------------
# ShadowTest
# ---------------------------------------------------------------------------

class TestShadowTest:

    def test_target_number(self):
        st = ShadowTest(ability_level=2, attribute_value=12)
        assert st.target_number == 8  # 20-12

    def test_shadow_reduction_on_success(self):
        # AD=9, SD=[6,4] → tengwar=1 → shadow_reduction = 1+1 = 2
        rng = SequentialRandom([9, 6, 4])
        st = ShadowTest(ability_level=2, attribute_value=5)  # TN=15; 9+6+4=19>=15
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS
        assert r.shadow_reduction == 2

    def test_shadow_reduction_zero_on_failure(self):
        rng = SequentialRandom([1, 1])
        st = ShadowTest(ability_level=1, attribute_value=5)  # TN=15
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.FAILURE
        assert r.shadow_reduction == 0

    def test_shadow_reduction_on_gandalf_auto_success(self):
        # AD=12 (GANDALF), SD=[6] → auto-success, tengwar=1 → shadow_reduction=2
        rng = SequentialRandom([12, 6])
        st = ShadowTest(ability_level=1, attribute_value=5)
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.AUTOMATIC_SUCCESS
        assert r.shadow_reduction == 2

    def test_shadow_reduction_no_tengwar(self):
        rng = SequentialRandom([10, 3])  # total=13, TN=10 (attr=10)
        st = ShadowTest(ability_level=1, attribute_value=10)
        r = st.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS
        assert r.shadow_reduction == 1  # 1 + 0 tengwar


# ---------------------------------------------------------------------------
# MagicalTest
# ---------------------------------------------------------------------------

class TestMagicalTest:

    def test_always_automatic_success(self):
        rng = SequentialRandom([1])  # worst success die — still succeeds
        mt = MagicalTest(ability_level=1)
        r = mt.resolve(rng)
        assert r.outcome == TestOutcome.AUTOMATIC_SUCCESS

    def test_no_action_die(self):
        mt = MagicalTest(ability_level=1)
        r = mt.resolve()
        assert r.action_die is None

    def test_tengwar_counted(self):
        rng = SequentialRandom([6, 6])  # 2 tengwar
        mt = MagicalTest(ability_level=2)
        r = mt.resolve(rng)
        assert r.tengwar_count == 2
        assert r.success_quality == SuccessQuality.EXTRAORDINARY_SUCCESS

    def test_weary_applies(self):
        rng = SequentialRandom([2])  # HOLLOW → value 0 when WEARY
        mt = MagicalTest(ability_level=1, is_weary=True)
        r = mt.resolve(rng)
        assert r.success_dice[0].value == 0

    def test_empty_pool_still_succeeds(self):
        mt = MagicalTest(ability_level=0)
        r = mt.resolve()
        assert r.outcome == TestOutcome.AUTOMATIC_SUCCESS
        assert r.success_dice == []
        assert r.tengwar_count == 0


# ---------------------------------------------------------------------------
# CombatTest
# ---------------------------------------------------------------------------

class TestCombatTestTargetNumber:

    def test_tn_combines_strength_and_defence(self):
        weapon = Weapon(damage=5, piercing_value=3)
        ct = CombatTest(
            ability_level=3,
            attacker_strength_attribute=12,  # strength TN = 20-12 = 8
            target_defence=4,
            target_armor_rating=2,
            weapon=weapon,
        )
        assert ct.target_number == 12  # 8 + 4

    def test_success(self):
        # TN = (20-10) + 2 = 12; AD=8, SD=[5] → 13 >= 12; AD≠10/GANDALF → no break
        weapon = Weapon(damage=5, piercing_value=10)
        rng = SequentialRandom([8, 5])
        ct = CombatTest(
            ability_level=1,
            attacker_strength_attribute=10,
            target_defence=2,
            target_armor_rating=0,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS

    def test_failure(self):
        weapon = Weapon(damage=5, piercing_value=10)
        rng = SequentialRandom([1, 1])
        ct = CombatTest(
            ability_level=1,
            attacker_strength_attribute=5,  # TN = 15 + 5 = 20
            target_defence=5,
            target_armor_rating=0,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.outcome == TestOutcome.FAILURE


class TestCombatTestBreakDefence:

    def _weapon(self, piercing: int) -> Weapon:
        return Weapon(damage=5, piercing_value=piercing)

    def test_break_defence_triggered_on_10(self):
        # AD=10 on successful attack → break defence
        weapon = self._weapon(piercing=1)  # armour will easily fail
        # sequence: AD=10, then armor SD
        rng = SequentialRandom([10])
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=15,  # TN=5+0=5
            target_defence=0,
            target_armor_rating=1,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS
        assert r.break_defense_triggered is True
        assert r.break_defense_result is not None

    def test_break_defence_triggered_on_gandalf(self):
        weapon = self._weapon(piercing=1)
        rng = SequentialRandom([12])  # GANDALF on attack AD
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=15,
            target_defence=0,
            target_armor_rating=1,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.break_defense_triggered is True

    def test_break_defence_not_triggered_on_normal_hit(self):
        weapon = self._weapon(piercing=15)
        rng = SequentialRandom([5, 5])  # AD=5 (hit), no break trigger
        ct = CombatTest(
            ability_level=1,
            attacker_strength_attribute=15,  # TN=5
            target_defence=0,
            target_armor_rating=2,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.outcome == TestOutcome.SUCCESS
        assert r.break_defense_triggered is False

    def test_break_defence_not_triggered_on_failure(self):
        weapon = self._weapon(piercing=1)
        rng = SequentialRandom([1])  # total=1, fails
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=1,  # TN=19
            target_defence=5,
            target_armor_rating=1,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.outcome == TestOutcome.FAILURE
        assert r.break_defense_triggered is False

    def test_break_defence_armour_succeeds(self):
        # AD=10 (trigger), then armour SD=5 >= piercing=3 → armour holds
        weapon = self._weapon(piercing=3)
        rng = SequentialRandom([10, 5])
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=15,
            target_defence=0,
            target_armor_rating=1,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.break_defense_triggered is True
        assert r.break_defense_result.outcome == TestOutcome.SUCCESS  # armour held

    def test_break_defence_armour_fails(self):
        # AD=10, armour SD=2 < piercing=5 → Wound
        weapon = self._weapon(piercing=5)
        rng = SequentialRandom([10, 2])
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=15,
            target_defence=0,
            target_armor_rating=1,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.break_defense_result.outcome == TestOutcome.FAILURE  # Wound

    def test_break_defence_no_armor_fails(self):
        # target_armor_rating=0 → empty pool, total=0 < piercing → Wound
        weapon = self._weapon(piercing=1)
        rng = SequentialRandom([10])
        ct = CombatTest(
            ability_level=0,
            attacker_strength_attribute=15,
            target_defence=0,
            target_armor_rating=0,
            weapon=weapon,
        )
        r = ct.resolve(rng)
        assert r.break_defense_result.outcome == TestOutcome.FAILURE


# ---------------------------------------------------------------------------
# Edge-case / boundary tests
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_success_die_pool(self):
        rng = SequentialRandom([5])
        r = resolve_test(ability_level=0, target_number=5, rng=rng)
        assert r.total == 5
        assert r.success_dice == []
        assert r.tengwar_count == 0

    def test_enhanced_and_weakened_cancel(self):
        # Can't pass both via Stance enum directly; the pool._effective_stance
        # guard handles the impossible case. Verify NORMAL gives 1 AD.
        rng = SequentialRandom([7, 4])
        pool_normal = __import__("engine.dice.roll_pool", fromlist=["RollPool"]).RollPool(
            ability_level=1, stance=Stance.NORMAL
        )
        ad, _ = pool_normal.roll(rng)
        assert ad.result == 7  # first value used, not two dice

    def test_tengwar_does_not_reduce_total(self):
        # ⭐ spending is a narrative choice; tengwar_count is separate from total
        rng = SequentialRandom([5, 6])  # AD=5, SD=6 (TENGWAR, value=6)
        r = resolve_test(ability_level=1, target_number=1, rng=rng)
        assert r.total == 11   # 5 + 6
        assert r.tengwar_count == 1

    def test_miserable_eye_ignores_success_dice(self):
        # Even with 3 tengwar the automatic failure stands
        rng = SequentialRandom([11, 6, 6, 6])
        r = resolve_test(
            ability_level=3, target_number=1, is_miserable=True, rng=rng
        )
        assert r.outcome == TestOutcome.AUTOMATIC_FAILURE
        assert r.tengwar_count == 0
