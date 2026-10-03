"""`tor.rules.endeavour` — the Resistance contest against the clock (spec 09.3, 16.3).

The engine is tested in `test_contest.py` and Risk in `test_injury.py`; nothing here
re-tests either. What is asserted is the adapter: the time limit setting the budget, Risk
grading each failed roll, a Disaster ending the task on the spot, abandonment ending it
*without* a Disaster, and the shared engine's grading left exactly as it is — the mirror of
the one rule the council adapter adds.

Every scripted roll ends with `rng.exhausted` asserted, per 19.2.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import build_hero

from tor.content.pack import ContentPack
from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectBus
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.hero import Hero
from tor.model.ids import AbilityId, HeroId
from tor.rolls import RollPurpose, Support
from tor.rules.contest import ContestOutcome, ResistanceLevel
from tor.rules.context import RulesContext
from tor.rules.endeavour import (
    EXTRA_ATTEMPTS,
    RESISTANCE_FOR_GOAL,
    Endeavour,
    EndeavourGoal,
    EndeavourRoll,
    EndeavourSetup,
    TimeLimit,
    abandon,
    apply_roll,
    attempts_for,
    began_event,
    begin_endeavour,
    end_endeavour,
    resolve_roll,
)
from tor.rules.injury import FailureShape, LossLevel, RiskDeclaration, RiskLevel

SCAN = AbilityId("scan")

#: build_hero gives every Skill rank 2 and WITS 4, so a SCAN roll is two Success dice
#: against TN 16.
SUCCESS_NO_ICON = ([10], [4, 4])
SUCCESS_TWO_ICONS = ([10], [6, 6])
PLAIN_FAILURE = ([1], [3, 3])

HAZARDOUS = RiskDeclaration(RiskLevel.HAZARDOUS, warning="the floor is rotten", acknowledged=True)
FOOLISH = RiskDeclaration(RiskLevel.FOOLISH, warning="the roof is on fire", acknowledged=True)


class Workers:
    """A Company at work on one task: a hero and a bus each, and the context over them."""

    def __init__(self, pack: ContentPack, *names: str) -> None:
        self.pack = pack
        self.heroes: dict[HeroId, Hero] = {}
        self.buses: dict[object, EffectBus] = {}
        for name in names or ("searcher", "helper"):
            hero = build_hero(name)
            self.heroes[hero.id] = hero
            self.buses[hero.id] = EffectBus()

    def __getitem__(self, name: str) -> Hero:
        return self.heroes[HeroId(name)]

    @property
    def ctx(self) -> RulesContext:
        return RulesContext(gear=self.pack, buses=dict(self.buses))  # type: ignore[arg-type]

    def setup(self, **kwargs: object) -> EndeavourSetup:
        fields: dict[str, object] = {
            "task": "search the ruined hall for the map",
            "goal": EndeavourGoal.LABORIOUS,
            "time_limit": TimeLimit.ENOUGH,
            "participants": tuple(self.heroes),
        }
        fields.update(kwargs)
        return EndeavourSetup(**fields)  # type: ignore[arg-type]

    def endeavour(self, **kwargs: object) -> Endeavour:
        return begin_endeavour(self.setup(**kwargs))

    def roll(
        self,
        endeavour: Endeavour,
        *,
        script: tuple[list[object], list[int]] = SUCCESS_NO_ICON,
        hero: str = "searcher",
        risk: RiskDeclaration | None = None,
        **kwargs: object,
    ):
        feats, successes = script
        rng = ScriptedRandomness(feats=feats, successes=successes)
        attempt = (
            EndeavourRoll(hero=HeroId(hero), ability=SCAN, risk=risk, **kwargs)  # type: ignore[arg-type]
            if risk is not None
            else EndeavourRoll(hero=HeroId(hero), ability=SCAN, **kwargs)  # type: ignore[arg-type]
        )
        outcome = resolve_roll(endeavour, self[hero], attempt, rng, ctx=self.ctx)
        assert rng.exhausted, rng.remaining
        return outcome

    def step(self, endeavour: Endeavour, *, take_woe: bool = False, extra: int = 0, **kwargs):
        outcome = self.roll(endeavour, **kwargs)
        events = apply_roll(endeavour, outcome, take_woe=take_woe, extra_successes=extra)
        return outcome, events


@pytest.fixture
def workers(pack: ContentPack) -> Callable[..., Workers]:
    def make(*names: str) -> Workers:
        return Workers(pack, *names)

    return make


class TestTheLadderAndTheClock:
    def test_the_three_goals_are_09_1s_three_numbers(self) -> None:
        assert RESISTANCE_FOR_GOAL[EndeavourGoal.SIMPLE] is ResistanceLevel.LOW
        assert RESISTANCE_FOR_GOAL[EndeavourGoal.LABORIOUS] is ResistanceLevel.MEDIUM
        assert RESISTANCE_FOR_GOAL[EndeavourGoal.DAUNTING] is ResistanceLevel.HIGH

    @pytest.mark.parametrize(
        ("limit", "extra"),
        [(TimeLimit.SHORT, 0), (TimeLimit.ENOUGH, 1), (TimeLimit.PLENTY, 2)],
    )
    def test_the_time_limit_sets_the_budget(self, limit: TimeLimit, extra: int) -> None:
        # 09.3.1's table: Resistance, +1, +2.
        assert EXTRA_ATTEMPTS[limit] == extra
        for goal in EndeavourGoal:
            assert attempts_for(goal, limit) == int(RESISTANCE_FOR_GOAL[goal]) + extra

    def test_no_time_limit_means_no_budget(self) -> None:
        assert attempts_for(EndeavourGoal.DAUNTING, TimeLimit.UNLIMITED) is None


class TestSettingTheTask:
    def test_a_laborious_task_with_enough_time(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        assert endeavour.resistance == 6
        assert endeavour.contest.attempts_allowed == 7
        assert not endeavour.finished

    def test_an_unlimited_task_reports_elapsed_effort(self, workers) -> None:
        # 09.3.1: no time limit, an unbounded contest, and elapsed effort instead.
        w = workers()
        endeavour = w.endeavour(time_limit=TimeLimit.UNLIMITED, roll_interval_minutes=720)
        assert endeavour.contest.attempts_allowed is None
        assert endeavour.elapsed_minutes == 0
        w.step(endeavour, script=PLAIN_FAILURE)
        w.step(endeavour, script=PLAIN_FAILURE)
        assert endeavour.elapsed_minutes == 1440, "twice a day while tracking: one day gone"
        assert endeavour.contest.attempts_remaining is None

    def test_an_unlimited_task_needs_an_interval_to_report_in(self, workers) -> None:
        w = workers()
        with pytest.raises(RuleViolation, match="needs a roll interval"):
            w.setup(time_limit=TimeLimit.UNLIMITED)

    def test_an_interval_must_be_positive(self, workers) -> None:
        w = workers()
        with pytest.raises(RuleViolation, match="must be positive"):
            w.setup(roll_interval_minutes=0)

    def test_a_bounded_task_reports_effort_too_when_given_an_interval(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(roll_interval_minutes=60)
        w.step(endeavour)
        assert endeavour.elapsed_minutes == 60

    def test_without_an_interval_there_is_no_elapsed_effort(self, workers) -> None:
        w = workers()
        assert w.endeavour().elapsed_minutes is None

    def test_somebody_has_to_do_the_work(self, workers) -> None:
        w = workers()
        with pytest.raises(RuleViolation, match="at least one hero"):
            w.setup(participants=())

    def test_the_opening_log_entry_records_the_task(self, workers) -> None:
        w = workers()
        event = began_event(w.endeavour(roll_interval_minutes=60))
        assert event.kind is EventKind.ENDEAVOUR_BEGAN
        assert event.payload["task"] == "search the ruined hall for the map"
        assert event.payload["goal"] == "laborious"
        assert event.payload["resistance"] == 6
        assert event.payload["attempts_allowed"] == 7
        assert event.payload["roll_interval_minutes"] == 60


class TestExecution:
    def test_an_icon_is_worth_an_extra_success(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        outcome, _ = w.step(endeavour, script=SUCCESS_TWO_ICONS)
        assert outcome.successes == 3
        assert endeavour.contest.successes == 3
        assert outcome.loss_level is None, "a success costs nothing"

    def test_the_roll_is_an_endeavour_roll(self, workers) -> None:
        w = workers()
        outcome = w.roll(w.endeavour())
        assert outcome.roll.request.purpose is RollPurpose.ENDEAVOUR
        assert outcome.roll.request.ability == "scan"

    def test_different_heroes_and_skills_serve_one_goal(self, workers) -> None:
        # 09.3.1: different Skills toward the same goal, the same one repeatedly.
        w = workers()
        endeavour = w.endeavour()
        w.step(endeavour, hero="searcher")
        w.step(endeavour, hero="helper")
        assert endeavour.contest.successes == 2

    def test_a_supporter_adds_a_die(self, workers) -> None:
        w = workers()
        outcome = w.roll(
            w.endeavour(),
            script=([10], [4, 4, 4]),
            support=Support(rating=2, approved=True),
        )
        assert outcome.roll.request.dice_count == 3

    def test_a_hero_not_at_work_may_not_roll(self, workers) -> None:
        w = workers("searcher", "helper", "idler")
        endeavour = begin_endeavour(w.setup(participants=(HeroId("searcher"), HeroId("helper"))))
        with pytest.raises(RuleViolation, match="not working at this endeavour"):
            w.roll(endeavour, hero="idler")

    def test_the_attempt_must_name_the_hero_it_is_handed(self, workers) -> None:
        w = workers()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(StateError, match="but was handed"):
            resolve_roll(
                w.endeavour(),
                w["searcher"],
                EndeavourRoll(hero=HeroId("helper"), ability=SCAN),
                rng,
                ctx=w.ctx,
            )

    def test_an_icon_may_be_spent_for_an_extra_success(self, workers) -> None:
        # 02.3.6: costs no attempt.
        w = workers()
        endeavour = w.endeavour()
        w.step(endeavour, script=SUCCESS_TWO_ICONS, extra=1)
        assert endeavour.contest.successes == 4
        assert endeavour.contest.attempts_used == 1

    def test_a_failed_rolls_icons_cannot_be_spent(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        outcome = w.roll(endeavour, script=([1], [6, 6]))
        assert not outcome.succeeded and outcome.roll.icons == 2
        with pytest.raises(RuleViolation, match="the roll offers 0"):
            apply_roll(endeavour, outcome, extra_successes=1)

    def test_a_negative_spend_is_refused(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        outcome = w.roll(endeavour, script=SUCCESS_TWO_ICONS)
        with pytest.raises(RuleViolation, match="cannot spend -1 icons"):
            apply_roll(endeavour, outcome, extra_successes=-1)


class TestRiskGradesEachFailure:
    def test_a_simple_failure_is_only_a_delay(self, workers) -> None:
        # 16.3 row 1: an attempt spent, and the endeavour continues.
        w = workers()
        endeavour = w.endeavour()
        outcome, _ = w.step(endeavour, script=PLAIN_FAILURE)
        assert outcome.shape is FailureShape.WOE_OFFERED, "Standard risk offers the woe"
        assert endeavour.contest.attempts_used == 1
        assert not endeavour.finished

    def test_a_failure_with_woe_hurts_but_the_work_goes_on(self, workers) -> None:
        # 16.3 row 2.
        w = workers()
        endeavour = w.endeavour()
        outcome, _ = w.step(endeavour, script=PLAIN_FAILURE, risk=HAZARDOUS)
        assert outcome.shape is FailureShape.FAILURE_WITH_WOE
        assert outcome.loss_level is LossLevel.SEVERE, "16.4.2: a woe may cost Endurance"
        assert not outcome.woe_available
        assert not endeavour.finished

    def test_a_disaster_ends_the_task_on_the_spot(self, workers) -> None:
        # 16.3 row 3, wired to ResistanceContest.abort as 16.3 asks.
        w = workers()
        endeavour = w.endeavour()
        w.step(endeavour, script=SUCCESS_TWO_ICONS)
        outcome, events = w.step(endeavour, script=PLAIN_FAILURE, risk=FOOLISH)
        assert outcome.disaster
        assert outcome.loss_level is LossLevel.GRIEVOUS
        assert endeavour.finished
        assert endeavour.contest.attempts_remaining == 5, "regardless of attempts remaining"
        assert endeavour.contest.abort_reason == "disaster"
        assert events[0].payload["shape"] == "disaster"

        result = end_endeavour(endeavour)
        assert result.outcome is ContestOutcome.DISASTER
        assert not result.resumable, "09.3.2: it cannot be resumed"

    def test_a_disaster_on_the_last_attempt_is_still_a_disaster(self, workers) -> None:
        # The case the contest's abort had to learn: exhaustion does not downgrade it.
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE, time_limit=TimeLimit.SHORT)
        w.step(endeavour, script=SUCCESS_NO_ICON)
        w.step(endeavour, script=PLAIN_FAILURE)
        w.step(endeavour, script=PLAIN_FAILURE, risk=FOOLISH)
        assert endeavour.contest.exhausted
        assert end_endeavour(endeavour).outcome is ContestOutcome.DISASTER

    def test_a_success_at_foolish_risk_is_simply_a_success(self, workers) -> None:
        # Risk grades the failure; it never touches a success.
        w = workers()
        endeavour = w.endeavour()
        outcome, _ = w.step(endeavour, script=SUCCESS_NO_ICON, risk=FOOLISH)
        assert outcome.succeeded and not outcome.disaster
        assert not endeavour.finished

    def test_an_unwarned_hazard_is_refused_before_any_dice(self, workers) -> None:
        # 16.2: the one fairness check the engine can make, owned by injury.
        w = workers()
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        with pytest.raises(RuleViolation, match="needs a warning"):
            resolve_roll(
                w.endeavour(),
                w["searcher"],
                EndeavourRoll(
                    hero=HeroId("searcher"),
                    ability=SCAN,
                    risk=RiskDeclaration(RiskLevel.HAZARDOUS),
                ),
                rng,
                ctx=w.ctx,
            )
        assert rng.remaining == (1, 2), "refused before a single die was rolled"

    def test_a_miserable_hero_fails_on_the_eye(self, workers) -> None:
        w = workers()
        hero = w["searcher"]
        hero.shadow = hero.hope
        from tor.rules.resources import recompute_conditions

        recompute_conditions(hero, ctx=w.ctx)
        outcome = w.roll(w.endeavour(), script=([FeatFace.EYE], [6, 6]))
        assert not outcome.succeeded


class TestSuccessWithWoe:
    def test_taking_the_woe_counts_one_success(self, workers) -> None:
        # 16.2.1's choice at Standard risk, available inside an endeavour like anywhere.
        w = workers()
        endeavour = w.endeavour()
        outcome, events = w.step(endeavour, script=PLAIN_FAILURE, take_woe=True)
        assert outcome.woe_available
        assert outcome.loss_level is LossLevel.MODERATE
        assert endeavour.contest.successes == 1
        assert endeavour.contest.attempts_used == 1
        assert events[0].payload["took_woe"] and events[0].payload["succeeded"]

    def test_the_woe_is_bare_whatever_icons_the_failure_showed(self, workers) -> None:
        # A failed roll's icons never counted toward its degree.
        w = workers()
        endeavour = w.endeavour()
        w.step(endeavour, script=([1], [6, 6]), take_woe=True)
        assert endeavour.contest.successes == 1

    def test_a_woe_cannot_be_taken_at_hazardous_risk(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        outcome = w.roll(endeavour, script=PLAIN_FAILURE, risk=HAZARDOUS)
        with pytest.raises(RuleViolation, match="offered only on a failed roll at Standard"):
            apply_roll(endeavour, outcome, take_woe=True)

    def test_a_woe_cannot_be_taken_on_a_success(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        outcome = w.roll(endeavour, script=SUCCESS_NO_ICON)
        with pytest.raises(RuleViolation, match="offered only on a failed roll"):
            apply_roll(endeavour, outcome, take_woe=True)


class TestAbandoning:
    def test_walking_away_is_a_failure_not_a_disaster(self, workers) -> None:
        # 09.3.2 names two ways to fail: abandon the task, or run out of time.
        w = workers()
        endeavour = w.endeavour()
        w.step(endeavour, script=SUCCESS_NO_ICON)
        events = abandon(endeavour, "night is falling")
        assert endeavour.finished and endeavour.abandoned
        assert not endeavour.contest.aborted, "abandonment must not reach contest.abort"
        assert [e.kind for e in events] == [EventKind.ENDEAVOUR_ABANDONED]
        assert events[0].payload["reason"] == "night is falling"

        result = end_endeavour(endeavour)
        assert result.outcome is ContestOutcome.PARTIAL
        assert result.abandoned and result.resumable

    def test_walking_away_having_found_nothing_is_a_total_failure(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        abandon(endeavour, "too dangerous")
        assert end_endeavour(endeavour).outcome is ContestOutcome.TOTAL_FAILURE

    def test_an_unlimited_task_ends_only_one_of_three_ways(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(time_limit=TimeLimit.UNLIMITED, roll_interval_minutes=60)
        w.step(endeavour, script=PLAIN_FAILURE)
        with pytest.raises(StateError, match="still running"):
            end_endeavour(endeavour)
        abandon(endeavour, "gave up")
        assert end_endeavour(endeavour).outcome is ContestOutcome.TOTAL_FAILURE

    def test_a_finished_task_cannot_be_abandoned(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE)
        w.step(endeavour, script=SUCCESS_TWO_ICONS)
        with pytest.raises(StateError, match="nothing to abandon"):
            abandon(endeavour, "never mind")

    def test_an_abandoned_task_takes_no_further_roll(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour()
        abandon(endeavour, "gave up")
        with pytest.raises(StateError, match="already finished"):
            w.roll(endeavour)


class TestEndingTheEndeavour:
    def test_meeting_the_resistance_is_a_success(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE)
        w.step(endeavour, script=SUCCESS_TWO_ICONS)
        result = end_endeavour(endeavour)
        assert result.outcome is ContestOutcome.SUCCESS
        assert not result.resumable, "nothing left to resume"

    def test_running_out_of_time_having_scored_is_partial(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE, time_limit=TimeLimit.SHORT)
        w.step(endeavour, script=SUCCESS_NO_ICON)
        w.step(endeavour, script=PLAIN_FAILURE)
        w.step(endeavour, script=PLAIN_FAILURE)
        result = end_endeavour(endeavour)
        assert result.outcome is ContestOutcome.PARTIAL
        assert result.resumable

    def test_running_out_of_time_with_nothing_is_not_a_disaster(self, workers) -> None:
        # The mirror image of the council's one rule: a search that turned up nothing has
        # not made anyone an enemy.
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE, time_limit=TimeLimit.SHORT)
        for _ in range(3):
            w.step(endeavour, script=PLAIN_FAILURE)
        result = end_endeavour(endeavour)
        assert result.outcome is ContestOutcome.TOTAL_FAILURE
        assert result.resumable

    def test_a_finished_task_takes_no_further_roll(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(goal=EndeavourGoal.SIMPLE)
        w.step(endeavour, script=SUCCESS_TWO_ICONS)
        with pytest.raises(StateError, match="already finished"):
            w.roll(endeavour)

    def test_a_running_task_cannot_be_graded(self, workers) -> None:
        w = workers()
        with pytest.raises(StateError, match="still running"):
            end_endeavour(w.endeavour())

    def test_the_closing_log_entry_explains_the_ending(self, workers) -> None:
        w = workers()
        endeavour = w.endeavour(roll_interval_minutes=30)
        w.step(endeavour, script=PLAIN_FAILURE, risk=FOOLISH)
        result = end_endeavour(endeavour)
        (event,) = result.events
        assert event.kind is EventKind.ENDEAVOUR_ENDED
        assert event.payload["outcome"] == "disaster"
        assert event.payload["resumable"] is False
        assert event.payload["elapsed_minutes"] == 30
        assert result.elapsed_minutes == 30


class TestASkillEndeavourAbortedByADisaster:
    """19.8's fifth golden transcript, as an integration test.

    The recorded file waits on the event log at build step 16; this asserts the calls
    compose into the ending 19.8 names.
    """

    def test_the_whole_task_composes(self, workers) -> None:
        w = workers()
        endeavour = begin_endeavour(
            w.setup(
                goal=EndeavourGoal.DAUNTING,
                time_limit=TimeLimit.PLENTY,
                roll_interval_minutes=60,
            )
        )
        log = [began_event(endeavour)]
        assert endeavour.contest.attempts_allowed == 11

        plan = [
            ("searcher", SUCCESS_TWO_ICONS, None),
            ("helper", PLAIN_FAILURE, None),
            ("searcher", SUCCESS_NO_ICON, HAZARDOUS),
            ("helper", PLAIN_FAILURE, HAZARDOUS),
            ("searcher", PLAIN_FAILURE, FOOLISH),
        ]
        for hero, script, risk in plan:
            _, events = w.step(endeavour, hero=hero, script=script, risk=risk)
            log += events

        assert endeavour.finished
        assert endeavour.contest.successes == 4
        assert endeavour.contest.attempts_used == 5
        assert endeavour.elapsed_minutes == 300

        result = end_endeavour(endeavour)
        log += result.events
        assert result.outcome is ContestOutcome.DISASTER
        assert not result.resumable

        kinds = [e.kind for e in log]
        assert kinds[0] is EventKind.ENDEAVOUR_BEGAN
        assert kinds.count(EventKind.ENDEAVOUR_ROLL) == 5
        assert kinds[-1] is EventKind.ENDEAVOUR_ENDED
