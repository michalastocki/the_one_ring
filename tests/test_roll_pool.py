"""Unit tests for RollPool (ST-3)."""

import pytest

from engine.dice import RollPool, Stance
from engine.dice.models import ActionDieRoll, DieResult


class SequentialRandom:
    """Cycles through a fixed sequence of values returned from randint()."""

    def __init__(self, values: list[int]):
        self._values = values
        self._index = 0

    def randint(self, _a: int, _b: int) -> int:
        v = self._values[self._index % len(self._values)]
        self._index += 1
        return v


class TestSuccessDiceCount:
    """RollPool.success_dice_count derived from modifiers."""

    def test_base_count(self):
        pool = RollPool(ability_level=3)
        assert pool.success_dice_count == 3

    def test_bonus_dice_added(self):
        pool = RollPool(ability_level=3, bonus_dice=2)
        assert pool.success_dice_count == 5

    def test_penalty_dice_subtracted(self):
        pool = RollPool(ability_level=3, penalty_dice=1)
        assert pool.success_dice_count == 2

    def test_net_modifier_clamped_to_zero(self):
        pool = RollPool(ability_level=1, penalty_dice=5)
        assert pool.success_dice_count == 0

    def test_bonus_and_penalty_cumulative(self):
        pool = RollPool(ability_level=2, bonus_dice=3, penalty_dice=1)
        assert pool.success_dice_count == 4

    def test_zero_ability_level(self):
        pool = RollPool(ability_level=0)
        assert pool.success_dice_count == 0


class TestStanceEnhancedWeakened:
    """ENHANCED keeps best AD; WEAKENED keeps worst AD."""

    def _pool(self, stance: Stance) -> RollPool:
        return RollPool(ability_level=0, stance=stance)

    def test_enhanced_picks_higher_of_two(self):
        # Sequence: AD1=5, AD2=9 (no success dice)
        rng = SequentialRandom([5, 9])
        pool = self._pool(Stance.ENHANCED)
        ad, _ = pool.roll(rng)
        assert ad.result == 9

    def test_weakened_picks_lower_of_two(self):
        rng = SequentialRandom([5, 9])
        pool = self._pool(Stance.WEAKENED)
        ad, _ = pool.roll(rng)
        assert ad.result == 5

    def test_enhanced_prefers_gandalf_over_ten(self):
        # AD1=10, AD2=12(GANDALF)
        rng = SequentialRandom([10, 12])
        pool = self._pool(Stance.ENHANCED)
        ad, _ = pool.roll(rng)
        assert ad.result == DieResult.GANDALF_RUNE

    def test_weakened_prefers_eye_over_one(self):
        # AD1=11(EYE), AD2=1
        rng = SequentialRandom([11, 1])
        pool = self._pool(Stance.WEAKENED)
        ad, _ = pool.roll(rng)
        assert ad.result == DieResult.EYE_OF_SAURON

    def test_normal_stance_rolls_one_action_die(self):
        # NORMAL: single AD, value comes from first randint call
        rng = SequentialRandom([7, 3])  # second value would be for success die
        pool = RollPool(ability_level=1, stance=Stance.NORMAL)
        ad, _ = pool.roll(rng)
        assert ad.result == 7

    def test_enhanced_returns_gandalf_when_both_are_gandalf(self):
        rng = SequentialRandom([12, 12])
        pool = self._pool(Stance.ENHANCED)
        ad, _ = pool.roll(rng)
        assert ad.result == DieResult.GANDALF_RUNE

    def test_weakened_returns_eye_when_both_are_eye(self):
        rng = SequentialRandom([11, 11])
        pool = self._pool(Stance.WEAKENED)
        ad, _ = pool.roll(rng)
        assert ad.result == DieResult.EYE_OF_SAURON


class TestRollPoolSuccessDiceRolled:
    """Correct number of Success Dice are generated."""

    def test_zero_success_dice(self):
        import random as stdlib_random
        rng = stdlib_random.Random(0)
        pool = RollPool(ability_level=0)
        _, sds = pool.roll(rng)
        assert sds == []

    def test_correct_success_dice_count(self):
        import random as stdlib_random
        rng = stdlib_random.Random(0)
        pool = RollPool(ability_level=4)
        _, sds = pool.roll(rng)
        assert len(sds) == 4

    def test_weary_flag_propagates_to_success_dice(self):
        # Force all success dice to roll 1 (HOLLOW) — with WEARY value must be 0
        rng = SequentialRandom([5, 1, 1, 1])  # AD=5, then three SD=1
        pool = RollPool(ability_level=3, is_weary=True)
        _, sds = pool.roll(rng)
        assert all(sd.value == 0 for sd in sds)
