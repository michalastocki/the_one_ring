"""`tor.rules.injury` — Risk, failure shapes, and sources of injury (spec 16).

The Endurance Loss table comes from `content/example/`, never from a fixture built here:
19.9 forbids reproducing the book's tables, and the mechanism is what is under test.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tor.content import load_pack
from tor.dice import FeatFace, FeatValue, ScriptedRandomness
from tor.errors import ContentError, RuleViolation
from tor.rolls import FeatDicePolicy, RollRequest
from tor.rules.injury import (
    LOSS_FOR_SHAPE,
    LOSS_POLICY,
    EnduranceLossOutcome,
    FailureShape,
    InjuryContext,
    LossKind,
    LossLevel,
    PoisonState,
    RiskDeclaration,
    RiskLevel,
    daily_poison_check,
    healing_penalty_dice,
    loss_delta,
    resolve_risky_roll,
    roll_endurance_loss,
    should_roll,
    unscathed_face,
)
from tor.tables import LookupTable

EXAMPLE = Path(__file__).resolve().parents[2] / "content" / "example"


@pytest.fixture(scope="module")
def loss_table() -> LookupTable[Any]:
    return load_pack(EXAMPLE).table("endurance_loss")


def scripted(feats: Sequence[FeatValue]) -> ScriptedRandomness:
    return ScriptedRandomness(feats=feats)


class TestMandatoryVectors:
    def test_endurance_loss_direction(self, loss_table: LookupTable[Any]) -> None:
        # 19.4: a *moderate* source rolls Favoured and tends toward Unscathed. Scripted
        # [2, RUNE], the kept face is the rune. This test exists precisely because the
        # direction is counterintuitive — getting it backwards makes minor hazards lethal.
        rng = scripted([2, FeatFace.RUNE])
        outcome = roll_endurance_loss(LossLevel.MODERATE, rng, table=loss_table)

        assert outcome.roll.kept_feat is FeatFace.RUNE
        assert outcome.kind is LossKind.UNSCATHED
        assert loss_delta(outcome, hero_endurance=20) == 0
        assert rng.exhausted

    def test_a_grievous_source_keeps_the_worse_face(self, loss_table: LookupTable[Any]) -> None:
        # The mirror of the above: Grievous is Ill-favoured, so the same pair keeps the 2.
        rng = scripted([2, FeatFace.RUNE])
        outcome = roll_endurance_loss(LossLevel.GRIEVOUS, rng, table=loss_table)

        assert outcome.roll.kept_feat == 2
        assert outcome.kind is LossKind.BRUISED
        assert rng.exhausted


class TestLossPolicy:
    def test_the_policy_ladder_runs_the_counterintuitive_way(self) -> None:
        # 16.4.1 in one assertion.
        assert LOSS_POLICY[LossLevel.MODERATE] is FeatDicePolicy.FAVOURED
        assert LOSS_POLICY[LossLevel.SEVERE] is FeatDicePolicy.NORMAL
        assert LOSS_POLICY[LossLevel.GRIEVOUS] is FeatDicePolicy.ILL_FAVOURED

    def test_a_severe_source_rolls_one_feat_die(self, loss_table: LookupTable[Any]) -> None:
        rng = scripted([7])
        outcome = roll_endurance_loss(LossLevel.SEVERE, rng, table=loss_table)
        assert outcome.roll.feat_dice == (7,)
        assert rng.exhausted


class TestEnduranceLossTable:
    def test_a_numeric_face_bruises_for_its_own_value(self, loss_table: LookupTable[Any]) -> None:
        # The "$roll" sentinel: "the numeric die result itself" (05.6).
        rng = scripted([7])
        outcome = roll_endurance_loss(LossLevel.SEVERE, rng, table=loss_table)
        assert outcome.kind is LossKind.BRUISED
        assert outcome.amount == 7
        assert loss_delta(outcome, hero_endurance=20) == -7
        assert rng.exhausted

    def test_the_eye_knocks_a_hero_out_whatever_they_had(
        self, loss_table: LookupTable[Any]
    ) -> None:
        # "all" is not a number in the row: it is the hero's whole current Endurance, which
        # is why loss_delta needs the hero and the row does not.
        rng = scripted([FeatFace.EYE])
        outcome = roll_endurance_loss(LossLevel.SEVERE, rng, table=loss_table)
        assert outcome.kind is LossKind.KNOCKED_OUT
        assert loss_delta(outcome, hero_endurance=13) == -13
        assert loss_delta(outcome, hero_endurance=4) == -4
        assert rng.exhausted

    def test_the_rune_leaves_a_hero_unscathed(self, loss_table: LookupTable[Any]) -> None:
        rng = scripted([FeatFace.RUNE])
        outcome = roll_endurance_loss(LossLevel.SEVERE, rng, table=loss_table)
        assert outcome.kind is LossKind.UNSCATHED
        assert rng.exhausted

    def test_a_row_that_is_not_an_object_is_a_content_error(self) -> None:
        from tor.tables import DieKind, TableRow

        table: LookupTable[Any] = LookupTable(
            id="broken",
            die=DieKind.FEAT,
            rows=(
                TableRow((1, 10), "nonsense"),
                TableRow(FeatFace.EYE, "x"),
                TableRow(FeatFace.RUNE, "y"),
            ),
        )
        with pytest.raises(ContentError, match="must be an object"):
            roll_endurance_loss(LossLevel.SEVERE, scripted([5]), table=table)

    def test_a_row_with_an_unknown_kind_is_a_content_error(self) -> None:
        from tor.tables import DieKind, TableRow

        table: LookupTable[Any] = LookupTable(
            id="broken",
            die=DieKind.FEAT,
            rows=(
                TableRow((1, 10), {"kind": "vaporised", "loss": 1}),
                TableRow(FeatFace.EYE, {"kind": "knocked_out", "loss": "all"}),
                TableRow(FeatFace.RUNE, {"kind": "unscathed", "loss": 0}),
            ),
        )
        with pytest.raises(ContentError, match="needs a 'kind'"):
            roll_endurance_loss(LossLevel.SEVERE, scripted([5]), table=table)

    def test_a_row_with_a_plain_number_is_read_as_written(self) -> None:
        from tor.tables import DieKind, TableRow

        table: LookupTable[Any] = LookupTable(
            id="flat",
            die=DieKind.FEAT,
            rows=(
                TableRow((1, 10), {"kind": "bruised", "loss": 3}),
                TableRow(FeatFace.EYE, {"kind": "knocked_out", "loss": "all"}),
                TableRow(FeatFace.RUNE, {"kind": "unscathed", "loss": 0}),
            ),
        )
        outcome = roll_endurance_loss(LossLevel.SEVERE, scripted([9]), table=table)
        assert outcome.amount == 3


class TestRiskLevels:
    def test_a_success_is_a_success_at_any_risk(self) -> None:
        # Risk grades the failure, not the roll.
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=10),
            RiskDeclaration(RiskLevel.FOOLISH, warning="the ledge is rotten", acknowledged=True),
            ScriptedRandomness(feats=[10], successes=[4]),
        )
        assert outcome.succeeded
        assert outcome.shape is FailureShape.SIMPLE
        assert not outcome.woe_available

    def test_a_standard_failure_offers_woe_as_a_choice(self) -> None:
        # 16.2.1: Success with Woe is offered, never imposed, so both branches come back.
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=20),
            RiskDeclaration(RiskLevel.STANDARD),
            ScriptedRandomness(feats=[1], successes=[1]),
        )
        assert outcome.shape is FailureShape.WOE_OFFERED
        assert outcome.woe_available

    def test_a_hazardous_failure_carries_woe(self) -> None:
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=20),
            RiskDeclaration(RiskLevel.HAZARDOUS, warning="the guards are close", acknowledged=True),
            ScriptedRandomness(feats=[1], successes=[1]),
        )
        assert outcome.shape is FailureShape.FAILURE_WITH_WOE
        assert not outcome.woe_available

    def test_a_foolish_failure_is_a_disaster(self) -> None:
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=20),
            RiskDeclaration(RiskLevel.FOOLISH, warning="the drop is fatal", acknowledged=True),
            ScriptedRandomness(feats=[1], successes=[1]),
        )
        assert outcome.shape is FailureShape.DISASTER

    def test_a_raised_risk_without_a_warning_is_refused(self) -> None:
        # 16.2: the engine cannot judge fairness, but it can refuse to let the warning step
        # be skipped silently.
        with pytest.raises(RuleViolation, match="warning the players acknowledged"):
            resolve_risky_roll(
                RollRequest(rating=1, target_number=14),
                RiskDeclaration(RiskLevel.HAZARDOUS),
                ScriptedRandomness(feats=[10], successes=[4]),
            )

    def test_a_warning_nobody_acknowledged_is_refused(self) -> None:
        with pytest.raises(RuleViolation, match="warning the players acknowledged"):
            resolve_risky_roll(
                RollRequest(rating=1, target_number=14),
                RiskDeclaration(RiskLevel.FOOLISH, warning="stated", acknowledged=False),
                ScriptedRandomness(feats=[10], successes=[4]),
            )

    def test_the_refusal_happens_before_any_dice_are_rolled(self) -> None:
        # Otherwise a Loremaster could learn the result and then supply the warning.
        rng = ScriptedRandomness(feats=[10], successes=[4])
        with pytest.raises(RuleViolation):
            resolve_risky_roll(
                RollRequest(rating=1, target_number=14),
                RiskDeclaration(RiskLevel.HAZARDOUS),
                rng,
            )
        assert rng.remaining == (1, 1)

    def test_standard_risk_needs_no_warning(self) -> None:
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=14),
            RiskDeclaration(RiskLevel.STANDARD),
            ScriptedRandomness(feats=[10], successes=[4]),
        )
        assert outcome.succeeded


class TestRiskToDamage:
    def test_each_failure_shape_maps_to_a_loss_level(self) -> None:
        # 16.4.2: the mapping that is the whole reason Risk and injury share a module.
        assert LOSS_FOR_SHAPE[FailureShape.SIMPLE] is LossLevel.MODERATE
        assert LOSS_FOR_SHAPE[FailureShape.WOE_OFFERED] is LossLevel.MODERATE
        assert LOSS_FOR_SHAPE[FailureShape.FAILURE_WITH_WOE] is LossLevel.SEVERE
        assert LOSS_FOR_SHAPE[FailureShape.DISASTER] is LossLevel.GRIEVOUS

    def test_every_shape_is_covered(self) -> None:
        assert set(LOSS_FOR_SHAPE) == set(FailureShape)

    def test_a_disaster_leads_to_the_ill_favoured_loss_roll(
        self, loss_table: LookupTable[Any]
    ) -> None:
        # The two systems joined end to end, which is how a subsystem will use them.
        outcome = resolve_risky_roll(
            RollRequest(rating=1, target_number=20),
            RiskDeclaration(RiskLevel.FOOLISH, warning="the drop is fatal", acknowledged=True),
            ScriptedRandomness(feats=[1], successes=[1]),
        )
        level = LOSS_FOR_SHAPE[outcome.shape]
        assert level is LossLevel.GRIEVOUS

        rng = scripted([FeatFace.RUNE, 6])
        loss = roll_endurance_loss(level, rng, table=loss_table)
        # Ill-favoured keeps the worse of the two faces, so the rune is discarded.
        assert loss.roll.kept_feat == 6
        assert loss.kind is LossKind.BRUISED
        assert rng.exhausted


class TestPoison:
    def test_a_rune_cures_the_poison(self, loss_table: LookupTable[Any]) -> None:
        # 16.4.5: no damage, and no longer poisoned. The cure follows from the Unscathed
        # row but is specific to poison — it must not be generalised.
        rng = scripted([FeatFace.RUNE])
        outcome = daily_poison_check(PoisonState(LossLevel.SEVERE), rng, table=loss_table)
        assert outcome.cured
        assert outcome.loss is not None
        assert outcome.loss.kind is LossKind.UNSCATHED
        assert rng.exhausted

    def test_anything_else_leaves_the_hero_poisoned(self, loss_table: LookupTable[Any]) -> None:
        rng = scripted([6])
        outcome = daily_poison_check(PoisonState(LossLevel.SEVERE), rng, table=loss_table)
        assert not outcome.cured
        assert outcome.loss is not None
        assert loss_delta(outcome.loss, hero_endurance=20) == -6
        assert rng.exhausted

    def test_a_moderate_poison_rolls_favoured_like_any_moderate_source(
        self, loss_table: LookupTable[Any]
    ) -> None:
        rng = scripted([3, FeatFace.RUNE])
        outcome = daily_poison_check(PoisonState(LossLevel.MODERATE), rng, table=loss_table)
        assert outcome.cured
        assert rng.exhausted

    def test_a_healing_roll_loses_dice_by_severity(self) -> None:
        # 16.4.5: (1d) if Severe, (2d) if Grievous.
        assert healing_penalty_dice(LossLevel.MODERATE) == 0
        assert healing_penalty_dice(LossLevel.SEVERE) == 1
        assert healing_penalty_dice(LossLevel.GRIEVOUS) == 2


class TestShouldRoll:
    """16.1 — advisory only, and never a gate the engine enforces."""

    def test_an_action_that_cannot_fail_needs_no_roll(self) -> None:
        advice = should_roll(can_fail=False, danger=True)
        assert not advice.roll_called_for
        assert "no doubt" in advice.reason

    @pytest.mark.parametrize("case", ["danger", "knowledge", "manipulation"])
    def test_any_one_of_the_three_cases_calls_for_a_roll(self, case: str) -> None:
        advice = should_roll(can_fail=True, **{case: True})
        assert advice.roll_called_for
        assert case in advice.reason

    def test_a_failure_that_costs_nothing_needs_no_roll(self) -> None:
        advice = should_roll(can_fail=True)
        assert not advice.roll_called_for
        assert "risks nothing" in advice.reason

    def test_several_cases_are_all_named(self) -> None:
        advice = should_roll(can_fail=True, danger=True, knowledge=True)
        assert "danger" in advice.reason
        assert "knowledge" in advice.reason


class TestGuardsAndAccessors:
    """The paths the behavioural tests above do not reach."""

    def test_an_injury_context_records_a_fatal_circumstance(self) -> None:
        # 16.4.4 is a Loremaster's declaration, not a computed property. apply_injury
        # arrives with Wounds at build step 11; the input type is fixed now.
        context = InjuryContext(source="a fall from a great height", level=LossLevel.GRIEVOUS)
        assert not context.fatal
        assert InjuryContext(source="x", level=LossLevel.SEVERE, fatal=True).fatal

    def test_the_unscathed_face_inverts_for_servants_of_the_shadow(self) -> None:
        assert unscathed_face() is FeatFace.RUNE
        assert unscathed_face(inverted=True) is FeatFace.EYE

    def test_an_outcome_carries_the_roll_that_produced_it(
        self, loss_table: LookupTable[Any]
    ) -> None:
        # 01.4: an Outcome carries the RollResults, so a UI can narrate without re-rolling.
        outcome = roll_endurance_loss(LossLevel.SEVERE, scripted([5]), table=loss_table)
        assert isinstance(outcome, EnduranceLossOutcome)
        assert outcome.roll.kept_feat == 5
