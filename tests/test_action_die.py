"""Unit tests for ActionDie (ST-1)."""

import random

import pytest

from engine.dice import ActionDie
from engine.dice.models import ActionDieRoll, DieResult


class FixedRandom:
    """Stub that always returns a fixed value from randint."""

    def __init__(self, value: int):
        self._value = value

    def randint(self, _a: int, _b: int) -> int:
        return self._value


class TestActionDieRoll:
    """ActionDie.roll() contracts."""

    die = ActionDie()

    def _roll_fixed(self, raw: int) -> ActionDieRoll:
        return self.die.roll(rng=FixedRandom(raw))

    # --- Special faces ---

    def test_face_12_is_gandalf_rune(self):
        roll = self._roll_fixed(12)
        assert roll.result == DieResult.GANDALF_RUNE
        assert roll.raw == 12

    def test_face_11_is_eye_of_sauron(self):
        roll = self._roll_fixed(11)
        assert roll.result == DieResult.EYE_OF_SAURON
        assert roll.raw == 11

    # --- Normal faces 1–10 ---

    @pytest.mark.parametrize("face", range(1, 11))
    def test_normal_faces_return_integer(self, face: int):
        roll = self._roll_fixed(face)
        assert roll.result == face
        assert roll.raw == face

    # --- numeric_value property ---

    def test_eye_numeric_value_is_zero(self):
        roll = self._roll_fixed(11)
        assert roll.numeric_value == 0

    def test_gandalf_numeric_value_is_12_for_comparison(self):
        roll = self._roll_fixed(12)
        assert roll.numeric_value == 12

    @pytest.mark.parametrize("face", range(1, 11))
    def test_normal_numeric_value_equals_face(self, face: int):
        roll = self._roll_fixed(face)
        assert roll.numeric_value == face

    # --- comparison_key property ---

    def test_eye_comparison_key_is_lowest(self):
        eye = self._roll_fixed(11)
        one = self._roll_fixed(1)
        assert eye.comparison_key < one.comparison_key

    def test_gandalf_comparison_key_is_highest(self):
        gandalf = self._roll_fixed(12)
        ten = self._roll_fixed(10)
        assert gandalf.comparison_key > ten.comparison_key

    @pytest.mark.parametrize("low,high", [(1, 2), (5, 10), (3, 9)])
    def test_comparison_key_ordering_normal_faces(self, low: int, high: int):
        low_roll = self._roll_fixed(low)
        high_roll = self._roll_fixed(high)
        assert low_roll.comparison_key < high_roll.comparison_key

    # --- All 12 faces are reachable ---

    def test_all_faces_reachable(self):
        die = ActionDie()
        rng = random.Random(42)
        results = {die.roll(rng).raw for _ in range(500)}
        assert results == set(range(1, 13))
