"""`tor.rolls.resolve` — the mandatory vectors of spec 02.4 (E1-E7) and 19.3 (R1-R13).

Every test scripts its dice exactly and asserts the queue is drained. Rolling too few dice
is as much a bug as rolling too many (19.2).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tor.dice import FeatFace, FeatValue, ScriptedRandomness
from tor.errors import RuleViolation
from tor.model.ids import AbilityId, TaskId
from tor.rolls import (
    AttemptLedger,
    AutoKind,
    Degree,
    FeatDicePolicy,
    IconBudget,
    Outcome,
    RollRequest,
    RollResult,
    attribute_tn,
    policy_sources,
    resolve,
)


def roll(
    req: RollRequest,
    *,
    feats: Sequence[FeatValue] = (),
    successes: Sequence[int] = (),
) -> RollResult:
    """Resolve `req` against an exactly-scripted RNG, asserting it is drained."""
    rng = ScriptedRandomness(feats=feats, successes=successes)
    result = resolve(req, rng)
    assert rng.exhausted, f"engine rolled fewer dice than scripted; {rng.remaining} left"
    return result


# --------------------------------------------------------------------------------------
# 02.4 — the seven worked examples. These are mandatory.
# --------------------------------------------------------------------------------------


class TestWorkedExamples:
    def test_e1_plain_skill_roll(self) -> None:
        result = roll(RollRequest(rating=2, target_number=13), feats=[8], successes=[4, 5])
        assert result.total == 17
        assert result.outcome is Outcome.SUCCESS
        assert result.icons == 0
        assert result.degree is Degree.SUCCESS

    def test_e2_weary_zeroes_an_outlined_die(self) -> None:
        result = roll(
            RollRequest(rating=2, target_number=13, weary=True), feats=[8], successes=[2, 5]
        )
        # The outlined 2 contributed nothing: 8 + 0 + 5 = 13.
        assert result.total == 13
        assert result.outcome is Outcome.SUCCESS

    def test_e3_miserable_eye_fails_despite_two_icons(self) -> None:
        result = roll(
            RollRequest(rating=3, target_number=10, eye_is_auto_failure=True),
            feats=[FeatFace.EYE],
            successes=[6, 6, 5],
        )
        assert result.outcome is Outcome.FAILURE
        assert result.auto is AutoKind.AUTO_FAILURE
        # The icons are still *reported* — they are not zeroed. magnitude() returns 0
        # because the roll failed, not because the icons went away.
        assert result.icons == 2
        assert not result.succeeded
        assert result.magnitude() == 0

    def test_e4_favoured_keeps_the_rune(self) -> None:
        result = roll(
            RollRequest(rating=0, target_number=18, favoured_sources=("skill:favoured",)),
            feats=[3, FeatFace.RUNE],
        )
        assert result.kept_feat is FeatFace.RUNE
        assert result.outcome is Outcome.SUCCESS
        assert result.auto is AutoKind.AUTO_SUCCESS

    def test_e5_favoured_and_ill_favoured_cancel(self) -> None:
        req = RollRequest(
            rating=1,
            target_number=10,
            favoured_sources=("a", "b"),
            ill_favoured_sources=("c",),
        )
        assert req.policy is FeatDicePolicy.NORMAL
        # Exactly one Feat die is consumed.
        roll(req, feats=[7], successes=[4])

    def test_e6_adversary_inversion(self) -> None:
        # Under inversion the rune reads as 0 and is the auto-failure face — but
        # adversaries are never Miserable, so precedence rule 3 does not fire and the
        # comparison proceeds normally.
        result = roll(
            RollRequest(rating=1, target_number=5, icon_inverted=True),
            feats=[FeatFace.RUNE],
            successes=[6],
        )
        assert result.auto is None
        assert result.total == 6  # 0 from the rune, 6 from the die
        assert result.outcome is Outcome.SUCCESS

    def test_e7_dice_floor(self) -> None:
        req = RollRequest(rating=1, penalty_dice=3, target_number=10)
        assert req.dice_count == 0
        result = roll(req, feats=[9])
        assert result.success_dice == ()
        assert result.total == 9


# --------------------------------------------------------------------------------------
# 19.3 — the roll resolution matrix.
# --------------------------------------------------------------------------------------


class TestResolutionMatrix:
    def test_r1_rating_zero_rolls_only_a_feat_die(self) -> None:
        result = roll(RollRequest(rating=0, target_number=10), feats=[6])
        assert len(result.feat_dice) == 1
        assert result.success_dice == ()

    def test_r2_weary_zeroes_every_outlined_die(self) -> None:
        result = roll(
            RollRequest(rating=3, target_number=5, weary=True), feats=[7], successes=[1, 2, 3]
        )
        assert result.total == 7  # the Feat value alone

    def test_r3_weary_six_contributes_and_scores(self) -> None:
        result = roll(RollRequest(rating=1, target_number=5, weary=True), feats=[2], successes=[6])
        assert result.total == 8
        assert result.icons == 1

    def test_r4_auto_success_outranks_auto_failure(self) -> None:
        # Precedence rule 2 beats rule 3.
        result = roll(
            RollRequest(rating=0, target_number=99, eye_is_auto_failure=True),
            feats=[FeatFace.RUNE],
        )
        assert result.outcome is Outcome.SUCCESS
        assert result.auto is AutoKind.AUTO_SUCCESS

    def test_r5_magical_success_ignores_the_tn_but_still_rolls(self) -> None:
        result = roll(
            RollRequest(rating=2, target_number=99, magical_success=True),
            feats=[1],
            successes=[6, 3],
        )
        assert result.outcome is Outcome.SUCCESS
        assert result.auto is AutoKind.MAGICAL
        assert len(result.success_dice) == 2  # degree still matters
        assert result.degree is Degree.GREAT_SUCCESS

    def test_r6_two_favoured_one_ill_favoured_is_normal(self) -> None:
        req = RollRequest(favoured_sources=("a", "b"), ill_favoured_sources=("c",))
        assert req.policy is FeatDicePolicy.NORMAL
        result = roll(req, feats=[5])
        assert len(result.feat_dice) == 1

    def test_r7_three_favoured_sources_still_roll_two_dice(self) -> None:
        req = RollRequest(favoured_sources=("a", "b", "c"))
        assert req.policy is FeatDicePolicy.FAVOURED
        result = roll(req, feats=[5, 9])
        assert len(result.feat_dice) == 2  # no stacking beyond two

    def test_r8_dice_count_floors_at_zero(self) -> None:
        assert RollRequest(rating=1, bonus_dice=2, penalty_dice=5).dice_count == 0

    def test_r9_inverted_eye_is_an_auto_success(self) -> None:
        result = roll(
            RollRequest(rating=0, target_number=99, icon_inverted=True), feats=[FeatFace.EYE]
        )
        assert result.outcome is Outcome.SUCCESS
        assert result.auto is AutoKind.AUTO_SUCCESS

    def test_r10_inverted_rune_contributes_zero_and_compares(self) -> None:
        result = roll(
            RollRequest(rating=1, target_number=4, icon_inverted=True),
            feats=[FeatFace.RUNE],
            successes=[3],
        )
        assert result.total == 3
        assert result.outcome is Outcome.FAILURE
        assert result.auto is None  # not an auto-failure: eye_is_auto_failure is False

    def test_r11_spending_icons_leaves_the_degree_alone(self) -> None:
        result = roll(RollRequest(rating=2, target_number=5), feats=[5], successes=[6, 6])
        assert result.degree is Degree.EXTRAORDINARY_SUCCESS
        budget = IconBudget.from_result(result)
        budget.spend("heavy_blow")
        assert budget.remaining == 1
        assert result.degree is Degree.EXTRAORDINARY_SUCCESS
        assert result.total == 17

    def test_r12_magnitude_on_a_failed_roll_is_zero(self) -> None:
        result = roll(RollRequest(rating=2, target_number=99), feats=[1], successes=[6, 6])
        assert not result.succeeded
        assert result.icons == 2
        assert result.magnitude() == 0
        assert result.magnitude(base=3) == 0

    @pytest.mark.parametrize(("icons", "expected"), [(0, 1), (1, 2), (2, 3), (5, 3)])
    def test_r13_tier_ladder(self, icons: int, expected: int) -> None:
        successes = [6] * icons + [4]
        result = roll(
            RollRequest(rating=len(successes), target_number=5), feats=[5], successes=successes
        )
        assert result.succeeded
        assert result.tier() == expected


class TestTierAndMagnitudeOnFailure:
    def test_tier_is_zero_on_failure(self) -> None:
        result = roll(RollRequest(rating=1, target_number=99), feats=[1], successes=[6])
        assert result.tier() == 0

    def test_magnitude_uses_the_supplied_base(self) -> None:
        # Marching distance is `3 + icons` (10.3.1) — the same pattern with a base of 3.
        result = roll(RollRequest(rating=2, target_number=5), feats=[5], successes=[6, 6])
        assert result.magnitude(3) == 5


class TestPolicyDerivation:
    @pytest.mark.parametrize(
        ("favoured", "ill_favoured", "expected"),
        [
            ((), (), FeatDicePolicy.NORMAL),
            (("a",), (), FeatDicePolicy.FAVOURED),
            ((), ("a",), FeatDicePolicy.ILL_FAVOURED),
            (("a",), ("b",), FeatDicePolicy.NORMAL),
            (("a", "b"), ("c", "d"), FeatDicePolicy.NORMAL),
        ],
    )
    def test_cancellation_table(
        self, favoured: tuple[str, ...], ill_favoured: tuple[str, ...], expected: FeatDicePolicy
    ) -> None:
        req = RollRequest(favoured_sources=favoured, ill_favoured_sources=ill_favoured)
        assert req.policy is expected

    def test_ill_favoured_keeps_the_worse_die(self) -> None:
        result = roll(
            RollRequest(rating=0, target_number=5, ill_favoured_sources=("flaw:idle",)),
            feats=[9, 2],
        )
        assert result.kept_feat == 2

    def test_ill_favoured_under_inversion_keeps_the_rune(self) -> None:
        result = roll(
            RollRequest(target_number=5, icon_inverted=True, ill_favoured_sources=("x",)),
            feats=[FeatFace.RUNE, 4],
        )
        assert result.kept_feat is FeatFace.RUNE

    def test_policy_sources_helper_round_trips(self) -> None:
        for policy in FeatDicePolicy:
            req = RollRequest(**policy_sources(policy))  # type: ignore[arg-type]
            assert req.policy is policy


class TestNoTargetNumber:
    def test_a_table_roll_reports_no_tn(self) -> None:
        result = roll(RollRequest(rating=0, target_number=None), feats=[7])
        assert result.outcome is Outcome.NO_TN
        assert not result.succeeded
        assert result.degree is Degree.NONE

    def test_an_auto_success_face_still_wins_without_a_tn(self) -> None:
        result = roll(RollRequest(target_number=None), feats=[FeatFace.RUNE])
        assert result.outcome is Outcome.SUCCESS


class TestFeatNumericValue:
    def test_shift_applies_to_a_numeric_face(self) -> None:
        # 19.4: Feat 8 with a +3 Pierce reads as an effective 11, clearing a threshold of
        # 10. Deliberately unclamped, unlike a table lookup.
        result = roll(RollRequest(target_number=5), feats=[8])
        assert result.feat_numeric_value(3) == 11

    def test_shift_does_nothing_to_an_icon_face(self) -> None:
        # Spending Pierce on a roll that already shows the rune changes nothing.
        result = roll(RollRequest(target_number=5), feats=[FeatFace.RUNE])
        assert result.feat_numeric_value(3) is FeatFace.RUNE


class TestRollRequestValidation:
    def test_a_negative_rating_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            RollRequest(rating=-1)


class TestAttributeTn:
    def test_derives_from_twenty(self) -> None:
        assert attribute_tn(6) == 14

    def test_short_campaign_derives_from_eighteen(self) -> None:
        assert attribute_tn(6, short_campaign=True) == 12


class TestIconBudget:
    def test_repeated_spends_stack(self) -> None:
        budget = IconBudget(available=3)
        budget.spend("pierce", 2)
        budget.spend("heavy_blow")
        assert budget.remaining == 0
        assert budget.count_of("pierce") == 2

    def test_overspending_is_a_rule_violation(self) -> None:
        budget = IconBudget(available=1)
        with pytest.raises(RuleViolation, match="only 1 left"):
            budget.spend("pierce", 2)

    def test_a_zero_or_negative_spend_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            IconBudget(available=2).spend("pierce", 0)


class TestAttemptLedger:
    def test_a_second_attempt_with_the_same_ability_is_barred(self) -> None:
        ledger = AttemptLedger()
        task, athletics = TaskId("climb"), AbilityId("athletics")
        assert ledger.may_attempt(task, athletics)
        ledger.record(task, athletics)
        assert not ledger.may_attempt(task, athletics)

    def test_a_different_ability_may_still_try(self) -> None:
        ledger = AttemptLedger()
        task = TaskId("climb")
        ledger.record(task, AbilityId("athletics"))
        assert ledger.may_attempt(task, AbilityId("explore"))

    def test_the_ledger_is_scoped_to_a_task(self) -> None:
        ledger = AttemptLedger()
        ledger.record(TaskId("climb"), AbilityId("athletics"))
        assert ledger.may_attempt(TaskId("swim"), AbilityId("athletics"))

    def test_clearing_a_task_forgets_its_attempts(self) -> None:
        ledger = AttemptLedger()
        task, athletics = TaskId("climb"), AbilityId("athletics")
        ledger.record(task, athletics)
        ledger.clear(task)
        assert ledger.may_attempt(task, athletics)
