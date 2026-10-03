"""`tor.rules.council` — the Resistance contest in formal dress (spec 09.2).

The engine itself is tested in `test_contest.py`; nothing here re-tests accumulating
successes. What is asserted is the adapter: the grade ladder, the Introduction's attempt
budget, the audience's attitude reaching every roll, and the one rule the adapter owns —
`TOTAL_FAILURE` promoted to `DISASTER`, because 09.2.5 gives a council no row for "every
attempt failed" short of being seen as a threat.

Every scripted roll ends with `rng.exhausted` asserted, per 19.2.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import build_hero

from tor.content.pack import ContentPack
from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectBus, EffectSource
from tor.effects.library import build_effect
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.hero import Hero
from tor.model.ids import AbilityId, HeroId
from tor.rolls import RollPurpose, Support
from tor.rules.contest import ContestOutcome, ResistanceLevel
from tor.rules.context import RulesContext
from tor.rules.council import (
    ATTITUDE_DICE,
    MAX_ROLEPLAY_BONUS,
    RESISTANCE_FOR_GRADE,
    AudienceAttitude,
    Council,
    CouncilAttempt,
    CouncilGoal,
    CouncilSetup,
    RequestGrade,
    apply_attempt,
    apply_introduction,
    attempt_budget,
    began_event,
    begin_council,
    end_council,
    grade_for,
    resolve_attempt,
    resolve_introduction,
    step_grade,
)

AWE = AbilityId("awe")
PERSUADE = AbilityId("persuade")

#: build_hero gives every Skill rank 2 and WITS 4, so a PERSUADE roll is two Success dice
#: against TN 16. These scripts land on the side each test needs without the arithmetic
#: cluttering the test body.
SUCCESS_NO_ICON = ([10], [4, 4])
SUCCESS_ONE_ICON = ([10], [6, 4])
SUCCESS_TWO_ICONS = ([10], [6, 6])
PLAIN_FAILURE = ([1], [3, 3])


class Gathering:
    """A Company at a council: a hero and a bus each, and the context over them."""

    def __init__(self, pack: ContentPack, *names: str) -> None:
        self.pack = pack
        self.heroes: dict[HeroId, Hero] = {}
        self.buses: dict[object, EffectBus] = {}
        for name in names or ("speaker", "friend"):
            hero = build_hero(name)
            self.heroes[hero.id] = hero
            self.buses[hero.id] = EffectBus()

    def __getitem__(self, name: str) -> Hero:
        return self.heroes[HeroId(name)]

    @property
    def participants(self) -> tuple[HeroId, ...]:
        return tuple(self.heroes)

    def register(self, name: str, effect_id: str, source: object = None) -> None:
        self.buses[HeroId(name)].register(
            self.pack.instantiate(effect_id),
            source or EffectSource.acquired("effect"),  # type: ignore[arg-type]
        )

    def register_built(self, name: str, effect_id: str, factory: str, params: dict) -> None:
        self.buses[HeroId(name)].register(
            build_effect(effect_id, "cultural_virtue", factory, params),
            EffectSource.culture("example_folk"),
        )

    @property
    def ctx(self) -> RulesContext:
        return RulesContext(gear=self.pack, buses=dict(self.buses))  # type: ignore[arg-type]

    def setup(self, **kwargs: object) -> CouncilSetup:
        fields: dict[str, object] = {
            "goal": CouncilGoal("passage through the wood", "a share of the hoard"),
            "grade": RequestGrade.BOLD,
            "audience": "example_folk",
            "participants": self.participants,
        }
        fields.update(kwargs)
        return CouncilSetup(**fields)  # type: ignore[arg-type]

    def council(self, **kwargs: object) -> Council:
        return begin_council(self.setup(**kwargs), ctx=self.ctx)

    def introduce(
        self,
        council: Council,
        *,
        script: tuple[list[object], list[int]] = SUCCESS_NO_ICON,
        speaker: str = "speaker",
        ability: AbilityId = AWE,
        **kwargs: object,
    ):
        feats, successes = script
        rng = ScriptedRandomness(feats=feats, successes=successes)
        outcome = resolve_introduction(
            council,
            self[speaker],
            CouncilAttempt(hero=HeroId(speaker), ability=ability, **kwargs),  # type: ignore[arg-type]
            rng,
            ctx=self.ctx,
        )
        assert rng.exhausted, rng.remaining
        apply_introduction(council, outcome)
        return outcome

    def attempt(
        self,
        council: Council,
        *,
        script: tuple[list[object], list[int]] = SUCCESS_NO_ICON,
        hero: str = "speaker",
        extra_successes: int = 0,
        **kwargs: object,
    ):
        feats, successes = script
        rng = ScriptedRandomness(feats=feats, successes=successes)
        outcome = resolve_attempt(
            council,
            self[hero],
            CouncilAttempt(hero=HeroId(hero), ability=PERSUADE, **kwargs),  # type: ignore[arg-type]
            rng,
            ctx=self.ctx,
        )
        assert rng.exhausted, rng.remaining
        apply_attempt(council, outcome, extra_successes=extra_successes)
        return outcome


@pytest.fixture
def gathering(pack: ContentPack) -> Callable[..., Gathering]:
    def make(*names: str) -> Gathering:
        return Gathering(pack, *names)

    return make


class TestTheGradeLadder:
    def test_the_three_grades_are_09_1s_three_numbers(self) -> None:
        assert RESISTANCE_FOR_GRADE[RequestGrade.REASONABLE] is ResistanceLevel.LOW
        assert RESISTANCE_FOR_GRADE[RequestGrade.BOLD] is ResistanceLevel.MEDIUM
        assert RESISTANCE_FOR_GRADE[RequestGrade.OUTRAGEOUS] is ResistanceLevel.HIGH

    @pytest.mark.parametrize("grade", list(RequestGrade))
    def test_a_grade_round_trips_through_its_number(self, grade: RequestGrade) -> None:
        assert grade_for(int(RESISTANCE_FOR_GRADE[grade])) is grade

    def test_a_resistance_off_the_ladder_is_a_caller_bug(self) -> None:
        with pytest.raises(StateError, match=r"not one of 09\.2\.2's grades"):
            grade_for(5)

    def test_the_eye_steps_a_request_up_the_ladder(self) -> None:
        # 14.6: a reasonable request becomes bold, a bold one outrageous.
        assert step_grade(RequestGrade.REASONABLE) is RequestGrade.BOLD
        assert step_grade(RequestGrade.BOLD) is RequestGrade.OUTRAGEOUS

    def test_stepping_clamps_at_both_ends(self) -> None:
        assert step_grade(RequestGrade.OUTRAGEOUS) is RequestGrade.OUTRAGEOUS
        assert step_grade(RequestGrade.REASONABLE, -1) is RequestGrade.REASONABLE
        assert step_grade(RequestGrade.OUTRAGEOUS, -5) is RequestGrade.REASONABLE


class TestOpeningACouncil:
    def test_the_grade_sets_the_resistance(self, gathering) -> None:
        g = gathering()
        assert g.council(grade=RequestGrade.REASONABLE).resistance == 3
        assert g.council(grade=RequestGrade.BOLD).resistance == 6
        assert g.council(grade=RequestGrade.OUTRAGEOUS).resistance == 9

    def test_a_pending_revelation_makes_the_goal_harder(self, gathering) -> None:
        # 14.6 routes this through Campaign.pending_modifiers, which 01.1 keeps out of a
        # subsystem's imports — so it arrives on the setup.
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE, resistance_step=1)
        assert council.grade is RequestGrade.BOLD
        assert council.resistance == 6
        assert council.resistance_step == 1

    def test_the_interaction_may_not_start_before_the_introduction(self, gathering) -> None:
        g = gathering()
        council = g.council()
        assert not council.begun
        assert council.contest.attempts_allowed == 0
        with pytest.raises(StateError, match="has not been introduced"):
            g.attempt(council)

    def test_a_council_needs_somebody_at_it(self, gathering) -> None:
        g = gathering()
        with pytest.raises(RuleViolation, match="at least one hero"):
            g.setup(participants=())

    def test_the_opening_log_entry_records_what_was_asked(self, gathering) -> None:
        g = gathering()
        event = began_event(g.council())
        assert event.kind is EventKind.COUNCIL_BEGAN
        assert event.payload["goal"] == "passage through the wood"
        assert event.payload["offer"] == "a share of the hoard"
        assert event.payload["grade"] == "bold"
        assert event.payload["resistance"] == 6
        assert event.payload["participants"] == ["speaker", "friend"]


class TestAudienceAttitude:
    def test_open_is_the_default_and_costs_nothing(self, gathering) -> None:
        g = gathering()
        council = g.council()
        assert council.attitude is AudienceAttitude.OPEN
        assert ATTITUDE_DICE[AudienceAttitude.OPEN] == 0

    def test_the_three_attitudes_are_09_2_4s_three_modifiers(self) -> None:
        assert ATTITUDE_DICE[AudienceAttitude.RELUCTANT] == -1
        assert ATTITUDE_DICE[AudienceAttitude.FRIENDLY] == 1

    def test_a_reluctant_audience_costs_every_roll_a_die(self, gathering) -> None:
        g = gathering()
        council = g.council(attitude=AudienceAttitude.RELUCTANT)
        outcome = g.introduce(council, script=([10], [4]))
        assert outcome.roll.request.penalty_dice == 1

    def test_a_friendly_audience_grants_every_roll_a_die(self, gathering) -> None:
        g = gathering()
        council = g.council(attitude=AudienceAttitude.FRIENDLY)
        outcome = g.introduce(council, script=([10], [4, 4, 4]))
        assert outcome.roll.request.bonus_dice == 1

    def test_a_cultural_virtue_makes_a_named_folk_friendly(self, gathering) -> None:
        # 04.3.6's Dwarf-friend shape: a replacement naming the attitude outright.
        g = gathering()
        g.register("friend", "example_cv_old_friends", EffectSource.culture("example_folk"))
        assert g.council().attitude is AudienceAttitude.FRIENDLY

    def test_it_leaves_a_different_folk_alone(self, gathering) -> None:
        g = gathering()
        g.register("friend", "example_cv_old_friends", EffectSource.culture("example_folk"))
        assert g.council(audience="second_folk").attitude is AudienceAttitude.OPEN

    def test_a_numeric_contribution_steps_the_ladder_instead(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "warm_welcome",
            "numeric_modifier",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "delta": 1},
        )
        reluctant = g.council(attitude=AudienceAttitude.RELUCTANT)
        assert reluctant.attitude is AudienceAttitude.OPEN

    def test_stepping_clamps_at_friendly(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "warm_welcome",
            "numeric_modifier",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "delta": 5},
        )
        assert g.council().attitude is AudienceAttitude.FRIENDLY

    def test_stepping_clamps_at_reluctant(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "cold_shoulder",
            "numeric_modifier",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "delta": -5},
        )
        assert g.council().attitude is AudienceAttitude.RELUCTANT

    def test_a_guarantee_beats_a_nudge(self, gathering) -> None:
        # The Virtue belongs to a hero; the attitude belongs to the room. An effect
        # promising "always Friendly" means always, whichever hero is listed first.
        g = gathering()
        g.register_built(
            "speaker",
            "cold_shoulder",
            "numeric_modifier",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "delta": -1},
        )
        g.register("friend", "example_cv_old_friends", EffectSource.culture("example_folk"))
        assert g.council().attitude is AudienceAttitude.FRIENDLY

    def test_the_warmest_of_two_guarantees_wins(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "wary_hosts",
            "replace_value",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "value": "reluctant"},
        )
        g.register_built(
            "friend",
            "old_debts",
            "replace_value",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "value": "friendly"},
        )
        assert g.council().attitude is AudienceAttitude.FRIENDLY

    def test_deltas_from_several_heroes_sum(self, gathering) -> None:
        g = gathering()
        for name in ("speaker", "friend"):
            g.register_built(
                name,
                f"{name}_warmth",
                "numeric_modifier",
                {"hook": "MODIFY_AUDIENCE_ATTITUDE", "delta": 1},
            )
        reluctant = g.council(attitude=AudienceAttitude.RELUCTANT)
        assert reluctant.attitude is AudienceAttitude.FRIENDLY, "two rungs from two heroes"

    def test_a_contribution_of_another_shape_changes_nothing(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "an_offer",
            "offer_action",
            {"hook": "MODIFY_AUDIENCE_ATTITUDE", "action": "send_a_gift"},
        )
        assert g.council().attitude is AudienceAttitude.OPEN


class TestTheIntroduction:
    def test_a_success_buys_resistance_plus_one_per_icon(self, gathering) -> None:
        g = gathering()
        council = g.council()
        outcome = g.introduce(council, script=SUCCESS_TWO_ICONS)
        assert outcome.succeeded and outcome.roll.icons == 2
        assert outcome.attempts == 6 + 2
        assert council.contest.attempts_allowed == 8
        assert not outcome.botched and not council.contest.botched_setup

    def test_a_bare_success_buys_the_resistance_flat(self, gathering) -> None:
        g = gathering()
        council = g.council()
        assert g.introduce(council, script=SUCCESS_NO_ICON).attempts == 6

    def test_a_failure_buys_the_resistance_and_poisons_the_ending(self, gathering) -> None:
        # 09.2.3: a failed Introduction still gives the budget, but sets botched_setup.
        g = gathering()
        council = g.council()
        outcome = g.introduce(council, script=PLAIN_FAILURE)
        assert not outcome.succeeded
        assert outcome.attempts == 6, "the base survives the failure; magnitude would not"
        assert outcome.botched and council.contest.botched_setup

    def test_a_patron_raises_the_budget(self, gathering) -> None:
        g = gathering()
        g.register("speaker", "second_patron_benefit", EffectSource.acquired("patron"))
        council = g.council()
        assert g.introduce(council, script=SUCCESS_NO_ICON).attempts == 7

    def test_two_holders_each_buy_an_attempt(self, gathering) -> None:
        # The budget is the Company's, so a per-hero Virtue shows up as one attempt each.
        g = gathering()
        for name in ("speaker", "friend"):
            g.register(name, "second_patron_benefit", EffectSource.acquired("patron"))
        assert g.introduce(g.council(), script=SUCCESS_NO_ICON).attempts == 8

    def test_the_budget_never_goes_negative(self, gathering) -> None:
        g = gathering()
        g.register_built(
            "speaker",
            "tongue_tied",
            "numeric_modifier",
            {"hook": "MODIFY_COUNCIL_ATTEMPTS", "delta": -20},
        )
        council = g.council()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        outcome = resolve_introduction(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=AWE),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        assert outcome.attempts == 0

    def test_the_budget_can_be_previewed_without_rolling_again(self, gathering) -> None:
        g = gathering()
        council = g.council()
        rng = ScriptedRandomness(feats=[10], successes=[6, 4])
        outcome = resolve_introduction(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=AWE),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        assert attempt_budget(council, outcome.roll, ctx=g.ctx) == outcome.attempts

    def test_the_ability_is_an_input_and_nothing_restricts_it(self, gathering) -> None:
        # 09.2.3 names AWE, COURTESY and RIDDLE as characteristic; the engine takes any.
        g = gathering()
        council = g.council()
        outcome = g.introduce(council, ability=AbilityId("song"))
        assert outcome.ability == "song"
        assert outcome.roll.request.purpose is RollPurpose.COUNCIL

    def test_only_one_introduction_is_allowed(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(StateError, match="already been introduced"):
            resolve_introduction(
                council,
                g["speaker"],
                CouncilAttempt(hero=HeroId("speaker"), ability=AWE),
                rng,
                ctx=g.ctx,
            )

    def test_applying_a_second_introduction_is_also_refused(self, gathering) -> None:
        g = gathering()
        council = g.council()
        outcome = g.introduce(council)
        with pytest.raises(StateError, match="already been introduced"):
            apply_introduction(council, outcome)

    def test_the_log_entry_records_what_it_bought(self, gathering) -> None:
        g = gathering()
        council = g.council()
        council.introduction = None
        rng = ScriptedRandomness(feats=[10], successes=[6, 4])
        outcome = resolve_introduction(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=AWE),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        (event,) = apply_introduction(council, outcome)
        assert event.kind is EventKind.COUNCIL_INTRODUCTION
        assert event.actor == "speaker"
        assert event.payload["attempts"] == 7
        assert event.payload["icons"] == 1
        assert not event.payload["botched"]
        assert event.rolls == (outcome.roll,)


class TestTheInteraction:
    def test_an_icon_is_worth_an_extra_success(self, gathering) -> None:
        # 09.1's single most consequential detail, reached through the adapter.
        g = gathering()
        council = g.council()
        g.introduce(council)
        assert g.attempt(council, script=SUCCESS_TWO_ICONS).successes == 3
        assert council.contest.successes == 3

    def test_a_failure_costs_an_attempt_and_scores_nothing(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        assert g.attempt(council, script=PLAIN_FAILURE).successes == 0
        assert council.contest.successes == 0
        assert council.contest.attempts_used == 1

    def test_the_roleplaying_bonus_is_a_first_class_input(self, gathering) -> None:
        # 09.2.4 mechanises it: a good decision deserves the weight of a good roll.
        g = gathering()
        council = g.council()
        g.introduce(council)
        outcome = g.attempt(council, script=([10], [4, 4, 4, 4]), roleplay_bonus=2)
        assert outcome.roll.request.bonus_dice == 2

    @pytest.mark.parametrize("bonus", [-1, MAX_ROLEPLAY_BONUS + 1])
    def test_a_bonus_outside_09_2_4s_range_is_refused(self, bonus: int) -> None:
        with pytest.raises(RuleViolation, match="roleplaying bonus is 0 to 2"):
            CouncilAttempt(hero=HeroId("speaker"), ability=PERSUADE, roleplay_bonus=bonus)

    def test_a_supporter_adds_a_die(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        outcome = g.attempt(
            council,
            script=([10], [4, 4, 4]),
            support=Support(rating=2, approved=True),
        )
        assert outcome.roll.request.dice_count == 3

    def test_the_ill_omen_curse_costs_a_die(self, gathering) -> None:
        # 04.3.6: MODIFY_COUNCIL_ROLL, folded through build_request like any dice-pool hook.
        g = gathering()
        g.register("speaker", "example_ill_omen", EffectSource.acquired("curse"))
        council = g.council()
        outcome = g.introduce(council, script=([10], [4]))
        assert outcome.roll.request.bonus_dice == -1
        assert outcome.roll.request.dice_count == 1

    def test_a_weary_hero_rolls_weary(self, gathering) -> None:
        g = gathering()
        g["speaker"].conditions.weary = True
        council = g.council()
        assert g.introduce(council).roll.request.weary

    def test_a_lay_lets_the_company_ignore_weariness(self, gathering) -> None:
        # 15: `sing` sets the flag; the council is what honours it.
        g = gathering()
        g["speaker"].conditions.weary = True
        council = g.council()
        council.ignore_weary = True
        assert not g.introduce(council).roll.request.weary

    def test_a_miserable_hero_fails_on_the_eye(self, gathering) -> None:
        g = gathering()
        hero = g["speaker"]
        hero.shadow = hero.hope
        from tor.rules.resources import recompute_conditions

        recompute_conditions(hero, ctx=g.ctx)
        council = g.council()
        outcome = g.introduce(council, script=([FeatFace.EYE], [6, 6]))
        assert not outcome.succeeded and outcome.botched

    def test_a_hero_not_at_the_council_may_not_speak(self, gathering) -> None:
        g = gathering("speaker", "friend")
        outsider = build_hero("stranger")
        g.heroes[outsider.id] = outsider
        g.buses[outsider.id] = EffectBus()
        council = begin_council(
            CouncilSetup(
                goal=CouncilGoal("a boon"),
                grade=RequestGrade.BOLD,
                audience="example_folk",
                participants=(HeroId("speaker"), HeroId("friend")),
            ),
            ctx=g.ctx,
        )
        g.introduce(council)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(RuleViolation, match="not taking part"):
            resolve_attempt(
                council,
                outsider,
                CouncilAttempt(hero=HeroId("stranger"), ability=PERSUADE),
                rng,
                ctx=g.ctx,
            )

    def test_the_attempt_must_name_the_hero_it_is_handed(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(StateError, match="but was handed"):
            resolve_attempt(
                council,
                g["speaker"],
                CouncilAttempt(hero=HeroId("friend"), ability=PERSUADE),
                rng,
                ctx=g.ctx,
            )

    def test_a_finished_council_takes_no_further_attempt(self, gathering) -> None:
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council)
        g.attempt(council, script=SUCCESS_TWO_ICONS)
        assert council.contest.met
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(StateError, match="already finished"):
            resolve_attempt(
                council,
                g["speaker"],
                CouncilAttempt(hero=HeroId("speaker"), ability=PERSUADE),
                rng,
                ctx=g.ctx,
            )

    def test_an_icon_may_also_be_spent_for_an_extra_success(self, gathering) -> None:
        # 02.3.6's Skill Special Success. Spending does not lower the degree, so the icon
        # counts twice by design.
        g = gathering()
        council = g.council()
        g.introduce(council)
        outcome = g.attempt(council, script=SUCCESS_TWO_ICONS, extra_successes=1)
        assert outcome.icons_available == 2
        assert council.contest.successes == 4, "3 from the roll, 1 bought with an icon"
        assert council.contest.attempts_used == 1, "the extra success costs no attempt"

    def test_spending_more_icons_than_the_roll_showed_is_refused(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        rng = ScriptedRandomness(feats=[10], successes=[6, 4])
        outcome = resolve_attempt(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=PERSUADE),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        with pytest.raises(RuleViolation, match="cannot spend 2 icons"):
            apply_attempt(council, outcome, extra_successes=2)
        with pytest.raises(RuleViolation, match="cannot spend -1 icons"):
            apply_attempt(council, outcome, extra_successes=-1)

    def test_the_log_entry_tracks_the_running_total(self, gathering) -> None:
        g = gathering()
        council = g.council()
        g.introduce(council)
        rng = ScriptedRandomness(feats=[10], successes=[6, 6])
        outcome = resolve_attempt(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=PERSUADE, roleplay_bonus=0),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        (event,) = apply_attempt(council, outcome)
        assert event.kind is EventKind.COUNCIL_ATTEMPT
        assert event.actor == "speaker"
        assert event.payload["successes"] == 3
        assert event.payload["total"] == 3
        assert event.payload["resistance"] == 6
        assert event.payload["attempts_remaining"] == 5
        assert not event.payload["finished"]


class TestEndingTheCouncil:
    def test_meeting_the_resistance_is_a_success(self, gathering) -> None:
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council)
        g.attempt(council, script=SUCCESS_TWO_ICONS)
        result = end_council(council)
        assert result.outcome is ContestOutcome.SUCCESS
        assert result.successes == 3 and result.resistance == 3
        assert not result.woe_available

    def test_the_19_4_vector_meets_six_on_the_fourth_attempt(self, gathering) -> None:
        # 19.4: Resistance 6; success+0 (1), success+2 (3), failure (0), success+1 (2) -> 6.
        g = gathering()
        council = g.council(grade=RequestGrade.BOLD)
        g.introduce(council)
        running = []
        for script in (SUCCESS_NO_ICON, SUCCESS_TWO_ICONS, PLAIN_FAILURE, SUCCESS_ONE_ICON):
            g.attempt(council, script=script)
            running.append(council.contest.successes)
        assert running == [1, 4, 4, 6]
        assert council.contest.attempts_used == 4
        assert end_council(council).outcome is ContestOutcome.SUCCESS

    def test_falling_short_offers_the_players_a_choice(self, gathering) -> None:
        # 09.2.5's middle row is a player decision, so both branches come back open.
        g = gathering()
        council = g.council(grade=RequestGrade.BOLD)
        g.introduce(council)
        for _ in range(5):
            g.attempt(council, script=PLAIN_FAILURE)
        g.attempt(council, script=SUCCESS_NO_ICON)
        assert council.contest.exhausted
        result = end_council(council)
        assert result.outcome is ContestOutcome.PARTIAL
        assert result.woe_available
        assert result.successes == 1

    def test_scoring_nothing_is_a_disaster_not_a_total_failure(self, gathering) -> None:
        # The adapter's one rule: 09.2.5 has no row for TOTAL_FAILURE.
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council)
        for _ in range(3):
            g.attempt(council, script=PLAIN_FAILURE)
        result = end_council(council)
        assert result.outcome is ContestOutcome.DISASTER
        assert result.successes == 0
        assert not result.woe_available

    def test_a_botched_introduction_turns_a_shortfall_into_a_disaster(self, gathering) -> None:
        # 19.4's second council vector: DISASTER, not PARTIAL.
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council, script=PLAIN_FAILURE)
        assert council.contest.botched_setup
        g.attempt(council, script=SUCCESS_NO_ICON)
        for _ in range(2):
            g.attempt(council, script=PLAIN_FAILURE)
        result = end_council(council)
        assert result.successes == 1, "it scored, so without the botch this would be PARTIAL"
        assert result.outcome is ContestOutcome.DISASTER
        assert not result.woe_available

    def test_a_botched_introduction_does_not_spoil_a_success(self, gathering) -> None:
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council, script=PLAIN_FAILURE)
        g.attempt(council, script=SUCCESS_TWO_ICONS)
        assert end_council(council).outcome is ContestOutcome.SUCCESS

    def test_the_closing_log_entry_explains_the_refusal(self, gathering) -> None:
        g = gathering()
        council = g.council(grade=RequestGrade.REASONABLE)
        g.introduce(council, script=PLAIN_FAILURE)
        for _ in range(3):
            g.attempt(council, script=PLAIN_FAILURE)
        result = end_council(council)
        (event,) = result.events
        assert event.kind is EventKind.COUNCIL_ENDED
        assert event.payload["outcome"] == "disaster"
        assert event.payload["botched_introduction"]
        assert event.payload["audience"] == "example_folk"
        assert event.payload["grade"] == "reasonable"


class TestACouncilAtResistanceSix:
    """19.8's second golden transcript, as an integration test.

    Not the recorded file — those land with the event log at build step 16. This asserts
    that the four calls of 09.2.1 compose into the ending 19.8 names: Resistance 6, a
    botched Introduction, Disaster.
    """

    def test_a_botched_introduction_ends_in_disaster(self, gathering) -> None:
        g = gathering()
        setup = g.setup(grade=RequestGrade.BOLD)
        council = begin_council(setup, ctx=g.ctx)
        log = [began_event(council)]
        assert council.resistance == 6

        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        intro = resolve_introduction(
            council,
            g["speaker"],
            CouncilAttempt(hero=HeroId("speaker"), ability=AWE),
            rng,
            ctx=g.ctx,
        )
        assert rng.exhausted
        log += apply_introduction(council, intro)
        assert intro.botched
        assert council.contest.attempts_allowed == 6, "a failure still buys the Resistance"

        # Six attempts, scoring four — short of six, so without the botch this is PARTIAL.
        scripts = [
            SUCCESS_NO_ICON,
            PLAIN_FAILURE,
            SUCCESS_ONE_ICON,
            PLAIN_FAILURE,
            SUCCESS_NO_ICON,
            PLAIN_FAILURE,
        ]
        for script in scripts:
            feats, successes = script
            rng = ScriptedRandomness(feats=feats, successes=successes)
            outcome = resolve_attempt(
                council,
                g["speaker"],
                CouncilAttempt(hero=HeroId("speaker"), ability=PERSUADE),
                rng,
                ctx=g.ctx,
            )
            assert rng.exhausted, rng.remaining
            log += apply_attempt(council, outcome)

        assert council.contest.exhausted and not council.contest.met
        assert council.contest.successes == 4

        result = end_council(council)
        assert result.outcome is ContestOutcome.DISASTER
        assert not result.woe_available, "a Disaster offers no price to pay"
        log += result.events

        kinds = [e.kind for e in log]
        assert kinds[0] is EventKind.COUNCIL_BEGAN
        assert kinds[1] is EventKind.COUNCIL_INTRODUCTION
        assert kinds.count(EventKind.COUNCIL_ATTEMPT) == 6
        assert kinds[-1] is EventKind.COUNCIL_ENDED
