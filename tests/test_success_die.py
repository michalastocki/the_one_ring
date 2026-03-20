"""Unit tests for SuccessDie (ST-2)."""

import pytest

from engine.dice import SuccessDie
from engine.dice.models import FaceType, SuccessDieRoll


class FixedRandom:
    def __init__(self, value: int):
        self._value = value

    def randint(self, _a, _b):
        return self._value


class TestSuccessDieFaceClassification:
    """Face type and is_tengwar contracts."""

    die = SuccessDie()

    def _roll(self, raw: int, is_weary: bool = False) -> SuccessDieRoll:
        return self.die.roll(is_weary=is_weary, rng=FixedRandom(raw))

    @pytest.mark.parametrize("face", [1, 2, 3])
    def test_hollow_faces(self, face: int):
        roll = self._roll(face)
        assert roll.face_type == FaceType.HOLLOW
        assert not roll.is_tengwar

    @pytest.mark.parametrize("face", [4, 5])
    def test_filled_faces(self, face: int):
        roll = self._roll(face)
        assert roll.face_type == FaceType.FILLED
        assert not roll.is_tengwar

    def test_face_6_is_tengwar(self):
        roll = self._roll(6)
        assert roll.face_type == FaceType.TENGWAR
        assert roll.is_tengwar

    @pytest.mark.parametrize("face", [1, 2, 3])
    def test_hollow_nominal_value_when_not_weary(self, face: int):
        roll = self._roll(face, is_weary=False)
        assert roll.value == face

    @pytest.mark.parametrize("face", [4, 5])
    def test_filled_value_unchanged(self, face: int):
        roll = self._roll(face)
        assert roll.value == face

    def test_tengwar_value_is_six(self):
        roll = self._roll(6)
        assert roll.value == 6


class TestSuccessDieWearyRule:
    """WEARY rule: HOLLOW faces (1–3) → value 0."""

    die = SuccessDie()

    def _roll(self, raw: int, is_weary: bool = False) -> SuccessDieRoll:
        return self.die.roll(is_weary=is_weary, rng=FixedRandom(raw))

    @pytest.mark.parametrize("face", [1, 2, 3])
    def test_hollow_zero_when_weary(self, face: int):
        roll = self._roll(face, is_weary=True)
        assert roll.value == 0

    @pytest.mark.parametrize("face", [4, 5])
    def test_filled_value_unchanged_when_weary(self, face: int):
        roll = self._roll(face, is_weary=True)
        assert roll.value == face

    def test_tengwar_unaffected_by_weary(self):
        roll = self._roll(6, is_weary=True)
        assert roll.value == 6
        assert roll.is_tengwar

    @pytest.mark.parametrize("face", [1, 2, 3])
    def test_face_type_hollow_still_correct_when_weary(self, face: int):
        roll = self._roll(face, is_weary=True)
        assert roll.face_type == FaceType.HOLLOW
