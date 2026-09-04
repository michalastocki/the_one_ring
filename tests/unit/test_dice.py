"""`tor.dice` — the two dice and the Randomness protocol (spec 02.1)."""

from __future__ import annotations

import pytest

from tor.dice import (
    FEAT_FACES,
    FeatFace,
    ScriptedRandomness,
    SuccessDie,
    SystemRandomness,
    auto_failure_face,
    auto_success_face,
    feat_numeric,
    feat_ordering_key,
)
from tor.errors import StateError


class TestSuccessDie:
    @pytest.mark.parametrize("face", [1, 2, 3])
    def test_low_faces_are_outlined(self, face: int) -> None:
        assert SuccessDie(face).is_outlined

    @pytest.mark.parametrize("face", [4, 5, 6])
    def test_high_faces_are_solid(self, face: int) -> None:
        assert not SuccessDie(face).is_outlined

    @pytest.mark.parametrize("face", [1, 2, 3, 4, 5])
    def test_only_the_six_carries_an_icon(self, face: int) -> None:
        assert not SuccessDie(face).has_icon

    def test_the_six_carries_an_icon(self) -> None:
        assert SuccessDie(6).has_icon

    def test_the_six_is_solid_so_weary_never_suppresses_an_icon(self) -> None:
        # The reason a Weary hero still scores icons: 6 is a solid face (02.3.4 step 3).
        die = SuccessDie(6)
        assert die.has_icon and not die.is_outlined

    @pytest.mark.parametrize("face", [0, 7, -1])
    def test_impossible_faces_are_rejected(self, face: int) -> None:
        with pytest.raises(ValueError, match=r"1\.\.6"):
            SuccessDie(face)


class TestFeatFaces:
    def test_twelve_outcomes(self) -> None:
        assert len(FEAT_FACES) == 12
        assert set(FEAT_FACES) == {*range(1, 11), FeatFace.EYE, FeatFace.RUNE}

    def test_rune_succeeds_for_heroes_eye_for_the_shadow(self) -> None:
        assert auto_success_face(inverted=False) is FeatFace.RUNE
        assert auto_success_face(inverted=True) is FeatFace.EYE

    def test_auto_failure_is_the_opposite_face(self) -> None:
        assert auto_failure_face(inverted=False) is FeatFace.EYE
        assert auto_failure_face(inverted=True) is FeatFace.RUNE

    @pytest.mark.parametrize("inverted", [False, True])
    def test_both_icons_contribute_zero(self, inverted: bool) -> None:
        # 02.1.2: the auto-success face short-circuits the TN comparison, so it needs no
        # numeric value. This is the single most consequential correction to the old code,
        # which scored the rune as 12.
        assert feat_numeric(FeatFace.RUNE, inverted=inverted) == 0
        assert feat_numeric(FeatFace.EYE, inverted=inverted) == 0

    @pytest.mark.parametrize("face", range(1, 11))
    @pytest.mark.parametrize("inverted", [False, True])
    def test_numeric_faces_are_themselves(self, face: int, inverted: bool) -> None:
        assert feat_numeric(face, inverted=inverted) == face


class TestFeatOrdering:
    def test_normal_ordering_eye_lowest_rune_highest(self) -> None:
        keys = [feat_ordering_key(v, inverted=False) for v in (FeatFace.EYE, 1, 10, FeatFace.RUNE)]
        assert keys == sorted(keys) and keys[0] < keys[-1]

    def test_inversion_swaps_the_two_icons(self) -> None:
        # 12.4: for servants of the Shadow the eye becomes the best result and the rune
        # the worst. One boolean, one code path.
        assert feat_ordering_key(FeatFace.EYE, inverted=True) > feat_ordering_key(10, inverted=True)
        assert feat_ordering_key(FeatFace.RUNE, inverted=True) < feat_ordering_key(1, inverted=True)

    def test_favoured_keeps_the_rune_over_a_number(self) -> None:
        pair = [3, FeatFace.RUNE]
        assert max(pair, key=lambda v: feat_ordering_key(v, inverted=False)) is FeatFace.RUNE

    def test_ill_favoured_keeps_the_eye_over_a_number(self) -> None:
        pair = [3, FeatFace.EYE]
        assert min(pair, key=lambda v: feat_ordering_key(v, inverted=False)) is FeatFace.EYE


class TestScriptedRandomness:
    def test_hands_out_dice_in_order(self) -> None:
        rng = ScriptedRandomness(feats=[8, FeatFace.EYE], successes=[4, 5])
        assert rng.feat() == 8
        assert rng.feat() is FeatFace.EYE
        assert rng.success() == SuccessDie(4)
        assert rng.success() == SuccessDie(5)
        assert rng.exhausted

    def test_raises_when_the_feat_queue_runs_dry(self) -> None:
        # 19.2: never refill, never cycle. Over-roll detection is the point.
        rng = ScriptedRandomness(feats=[3])
        rng.feat()
        with pytest.raises(StateError, match="more Feat dice"):
            rng.feat()

    def test_raises_when_the_success_queue_runs_dry(self) -> None:
        rng = ScriptedRandomness(successes=[3])
        rng.success()
        with pytest.raises(StateError, match="more Success dice"):
            rng.success()

    def test_exhausted_is_false_while_dice_remain(self) -> None:
        rng = ScriptedRandomness(feats=[1], successes=[1, 2])
        assert not rng.exhausted
        assert rng.remaining == (1, 2)
        rng.feat()
        rng.success()
        assert rng.remaining == (0, 1)
        assert not rng.exhausted

    def test_a_fresh_empty_roller_is_exhausted(self) -> None:
        assert ScriptedRandomness().exhausted


class TestSystemRandomness:
    def test_is_reproducible_from_a_seed(self) -> None:
        a = SystemRandomness(seed=1234)
        b = SystemRandomness(seed=1234)
        assert [a.feat() for _ in range(20)] == [b.feat() for _ in range(20)]
        assert [a.success() for _ in range(20)] == [b.success() for _ in range(20)]

    def test_reaches_every_feat_face(self) -> None:
        rng = SystemRandomness(seed=7)
        seen = {rng.feat() for _ in range(500)}
        assert seen == set(FEAT_FACES)

    def test_reaches_every_success_face(self) -> None:
        rng = SystemRandomness(seed=7)
        seen = {rng.success().face for _ in range(300)}
        assert seen == {1, 2, 3, 4, 5, 6}
