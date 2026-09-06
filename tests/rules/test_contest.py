"""`tor.rules.contest` — the shared Resistance machine and opposed actions (spec 09).

Councils and Skill Endeavours are one machine with two sets of names, so the vectors 19.4
lists under "Council" are asserted here at the engine level and will be re-asserted through
the adapter at build step 13.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tor.dice import FeatValue, ScriptedRandomness
from tor.errors import StateError
from tor.model.ids import HeroId
from tor.rolls import RollRequest, RollResult, resolve
from tor.rules.contest import (
    ContestOutcome,
    OpposedEntry,
    ResistanceContest,
    ResistanceLevel,
    evaluate,
    resolve_opposed,
)


def roll(
    req: RollRequest, *, feats: Sequence[FeatValue] = (), successes: Sequence[int] = ()
) -> RollResult:
    """Resolve one request against a scripted RNG, asserting the queue drains exactly.

    19.2: a test that scripts two Success dice and sees the engine ask for three has caught
    a real bug, and rolling too few is as much a bug as too many.
    """
    rng = ScriptedRandomness(feats=feats, successes=successes)
    result = resolve(req, rng)
    assert rng.exhausted, f"engine rolled fewer dice than scripted; {rng.remaining} left"
    return result


def attempt(*, feats: Sequence[FeatValue], successes: Sequence[int] = ()) -> RollResult:
    """One Skill roll against TN 14 with two Success dice."""
    return roll(
        RollRequest(rating=len(successes), target_number=14),
        feats=feats,
        successes=successes,
    )


class TestResistanceLevel:
    def test_the_ladder_is_three_six_nine(self) -> None:
        # 09.1, pattern P6: one enum, two sets of display names.
        assert (ResistanceLevel.LOW, ResistanceLevel.MEDIUM, ResistanceLevel.HIGH) == (3, 6, 9)

    def test_a_level_is_usable_as_a_resistance(self) -> None:
        contest = ResistanceContest(
            resistance=ResistanceLevel.MEDIUM, attempts_allowed=ResistanceLevel.MEDIUM
        )
        assert contest.resistance == 6


class TestMandatoryVectors:
    def test_council_successes(self) -> None:
        # 19.4: Resistance 6. success+0 icons (1), success+2 icons (3), failure (0),
        # success+1 icon (2) -> total 6, met on the fourth attempt.
        contest = ResistanceContest(resistance=6, attempts_allowed=6)

        contest.record(attempt(feats=[10], successes=[4, 4]))  # 14 >= 14, no icons
        assert contest.successes == 1

        contest.record(attempt(feats=[10], successes=[6, 6]))  # two icons
        assert contest.successes == 4

        contest.record(attempt(feats=[1], successes=[1, 1]))  # 3 < 14, a failure
        assert contest.successes == 4

        contest.record(attempt(feats=[10], successes=[6, 4]))  # one icon
        assert contest.successes == 6

        assert contest.met
        assert contest.attempts_used == 4
        assert evaluate(contest) is ContestOutcome.SUCCESS

    def test_council_botched_introduction(self) -> None:
        # 19.4 / 09.2.5: a failed Introduction plus partial successes is a DISASTER, not a
        # PARTIAL. This is the whole reason botched_setup exists.
        contest = ResistanceContest(resistance=6, attempts_allowed=6, botched_setup=True)
        contest.record(attempt(feats=[10], successes=[4, 4]))
        for _ in range(5):
            contest.record(attempt(feats=[1], successes=[1, 1]))

        assert contest.exhausted
        assert not contest.met
        assert 0 < contest.successes < 6
        assert evaluate(contest) is ContestOutcome.DISASTER


class TestRecording:
    def test_each_success_icon_is_an_extra_success(self) -> None:
        # 09.1 calls this the single most consequential detail in both subsystems.
        contest = ResistanceContest(resistance=9, attempts_allowed=9)
        recorded = contest.record(attempt(feats=[10], successes=[6, 6, 6]))
        assert recorded.successes == 4

    def test_a_failed_attempt_still_costs_an_attempt(self) -> None:
        contest = ResistanceContest(resistance=6, attempts_allowed=2)
        contest.record(attempt(feats=[1], successes=[1]))
        assert (contest.successes, contest.attempts_used) == (0, 1)

    def test_the_log_keeps_every_attempt(self) -> None:
        contest = ResistanceContest(resistance=6, attempts_allowed=3)
        contest.record(attempt(feats=[10], successes=[4]))
        contest.record(attempt(feats=[1], successes=[1]))
        assert [a.successes for a in contest.attempt_log] == [1, 0]

    def test_extra_successes_cost_no_attempt(self) -> None:
        # 02.3.6's Skill Special Success scores toward Resistance without an attempt.
        contest = ResistanceContest(resistance=6, attempts_allowed=6)
        contest.add_successes(2)
        assert (contest.successes, contest.attempts_used) == (2, 0)

    def test_negative_extra_successes_are_a_caller_bug(self) -> None:
        contest = ResistanceContest(resistance=6, attempts_allowed=6)
        with pytest.raises(StateError, match="cannot add"):
            contest.add_successes(-1)

    def test_recording_into_a_met_contest_is_a_state_error(self) -> None:
        # 01.6: the engine was driven into an impossible state.
        contest = ResistanceContest(resistance=1, attempts_allowed=6)
        contest.record(attempt(feats=[10], successes=[4]))
        assert contest.finished
        with pytest.raises(StateError, match="already finished"):
            contest.record(attempt(feats=[10], successes=[4]))

    def test_recording_into_an_exhausted_contest_is_a_state_error(self) -> None:
        contest = ResistanceContest(resistance=9, attempts_allowed=1)
        contest.record(attempt(feats=[1], successes=[1]))
        with pytest.raises(StateError, match="already finished"):
            contest.record(attempt(feats=[1], successes=[1]))


class TestAborting:
    def test_an_abort_finishes_the_contest_early(self) -> None:
        # 09.3.2: a Disaster means the endeavour fails completely and cannot be resumed,
        # which is distinct from running out of attempts.
        contest = ResistanceContest(resistance=9, attempts_allowed=9)
        contest.record(attempt(feats=[10], successes=[4]))
        contest.abort("disaster")

        assert contest.finished
        assert not contest.exhausted
        assert contest.abort_reason == "disaster"
        assert evaluate(contest) is ContestOutcome.DISASTER

    def test_aborting_twice_is_a_state_error(self) -> None:
        contest = ResistanceContest(resistance=9, attempts_allowed=9)
        contest.abort("disaster")
        with pytest.raises(StateError, match="already finished"):
            contest.abort("disaster again")


class TestUnboundedContest:
    def test_an_endeavour_with_no_time_limit_never_exhausts(self) -> None:
        # 09.3.1: with no time limit the engine runs the contest unbounded and reports
        # elapsed effort instead.
        contest = ResistanceContest(resistance=6, attempts_allowed=None)
        for _ in range(20):
            contest.record(attempt(feats=[1], successes=[1]))
        assert not contest.exhausted
        assert not contest.finished
        assert contest.attempts_remaining is None

    def test_an_unbounded_contest_still_finishes_on_meeting_the_resistance(self) -> None:
        contest = ResistanceContest(resistance=1, attempts_allowed=None)
        contest.record(attempt(feats=[10], successes=[4]))
        assert contest.finished

    def test_a_bounded_contest_reports_what_is_left(self) -> None:
        contest = ResistanceContest(resistance=6, attempts_allowed=3)
        contest.record(attempt(feats=[1], successes=[1]))
        assert contest.attempts_remaining == 2


class TestEvaluate:
    def test_a_scoreless_contest_is_a_total_failure_not_a_disaster(self) -> None:
        # 09.1's code block and its prose disagree; we follow the prose, so the shared
        # engine returns TOTAL_FAILURE and the council adapter promotes it (09.2.5).
        contest = ResistanceContest(resistance=3, attempts_allowed=1)
        contest.record(attempt(feats=[1], successes=[1]))
        assert evaluate(contest) is ContestOutcome.TOTAL_FAILURE

    def test_some_successes_short_of_the_resistance_is_partial(self) -> None:
        # 09.2.5's Failure-or-Success-with-Woe branch, which the players decide.
        contest = ResistanceContest(resistance=6, attempts_allowed=1)
        contest.record(attempt(feats=[10], successes=[4]))
        assert evaluate(contest) is ContestOutcome.PARTIAL

    def test_meeting_the_resistance_beats_a_botched_setup(self) -> None:
        # A botched Introduction only matters if the council then fails (09.2.3).
        contest = ResistanceContest(resistance=1, attempts_allowed=6, botched_setup=True)
        contest.record(attempt(feats=[10], successes=[4]))
        assert evaluate(contest) is ContestOutcome.SUCCESS

    def test_exceeding_the_resistance_still_succeeds(self) -> None:
        contest = ResistanceContest(resistance=2, attempts_allowed=6)
        contest.record(attempt(feats=[10], successes=[6, 6, 6]))
        assert contest.successes == 4
        assert evaluate(contest) is ContestOutcome.SUCCESS


class TestOpposedActions:
    """09.4 — rare, but specified: one PC acting in direct opposition to another."""

    def test_the_most_icons_among_succeeders_wins(self) -> None:
        rng = ScriptedRandomness(feats=[10, 10], successes=[6, 6, 4])
        result = resolve_opposed(
            [
                OpposedEntry(HeroId("a"), RollRequest(rating=2, target_number=14)),
                OpposedEntry(HeroId("b"), RollRequest(rating=1, target_number=14)),
            ],
            rng,
        )
        assert result.winner == HeroId("a")
        assert result.draw_reason is None
        assert rng.exhausted

    def test_each_rolls_against_their_own_target_number(self) -> None:
        # 09.4: "Each rolls against their own relevant Attribute TN." A low roll that
        # clears an easy TN beats a higher roll that misses a hard one.
        rng = ScriptedRandomness(feats=[5, 9], successes=[4, 4])
        result = resolve_opposed(
            [
                OpposedEntry(HeroId("easy"), RollRequest(rating=1, target_number=8)),
                OpposedEntry(HeroId("hard"), RollRequest(rating=1, target_number=20)),
            ],
            rng,
        )
        assert result.winner == HeroId("easy")
        assert rng.exhausted

    def test_all_failing_is_a_draw(self) -> None:
        rng = ScriptedRandomness(feats=[1, 1], successes=[1, 1])
        result = resolve_opposed(
            [
                OpposedEntry(HeroId("a"), RollRequest(rating=1, target_number=20)),
                OpposedEntry(HeroId("b"), RollRequest(rating=1, target_number=20)),
            ],
            rng,
        )
        assert result.winner is None
        assert result.draw_reason == "all_failed"
        assert rng.exhausted

    def test_tied_icons_is_a_draw(self) -> None:
        rng = ScriptedRandomness(feats=[10, 10], successes=[6, 6])
        result = resolve_opposed(
            [
                OpposedEntry(HeroId("a"), RollRequest(rating=1, target_number=14)),
                OpposedEntry(HeroId("b"), RollRequest(rating=1, target_number=14)),
            ],
            rng,
        )
        assert result.winner is None
        assert result.draw_reason == "tied_icons"
        assert rng.exhausted

    def test_every_roll_is_returned_for_narration(self) -> None:
        rng = ScriptedRandomness(feats=[10, 1], successes=[4, 1])
        result = resolve_opposed(
            [
                OpposedEntry(HeroId("a"), RollRequest(rating=1, target_number=14)),
                OpposedEntry(HeroId("b"), RollRequest(rating=1, target_number=14)),
            ],
            rng,
        )
        assert set(result.rolls) == {HeroId("a"), HeroId("b")}
        assert rng.exhausted

    def test_an_empty_opposed_action_is_a_caller_bug(self) -> None:
        with pytest.raises(StateError, match="at least one contestant"):
            resolve_opposed([], ScriptedRandomness())
