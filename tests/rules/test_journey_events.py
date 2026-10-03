"""`tor.rules.journey` — event resolution and the Fatigue it leaves (spec 10.4-10.6).

The events table is `content/example/tables/journey_events.json`, which is invented: 19.9
forbids asserting a value from the licensed book, so what is asserted here is the *mechanism*
— which op list fires, who pays the Fatigue, how the region reaches the Feat die — using
whatever rows the example pack happens to carry.

Every scripted roll ends with `rng.exhausted` asserted, per 19.2.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Party

from tor.content.ops import OpKind, parse_ops
from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.errors import ContentError, RuleViolation, StateError
from tor.events import EventKind
from tor.model.conditions import RegionType, Season
from tor.model.gear import Mount
from tor.model.ids import HeroId
from tor.rolls import FeatDicePolicy, RollPurpose, Support
from tor.rules.journey import (
    FATIGUE_RELIEF_BASE,
    EventDeclaration,
    Hex,
    Journey,
    JourneyEvent,
    JourneyRole,
    PerilousArea,
    TerrainKind,
    apply_event,
    apply_step,
    determine_event,
    end_journey,
    plan_journey,
    residual_fatigue,
    resolve_event,
    resolve_step,
    roll_marching_test,
    select_target,
)
from tor.tables import DieKind, LookupTable, rows_from_json

EASY_WILD = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.WILD)


def trek(party: Party, *, count: int = 10, season: Season = Season.SUMMER, **kwargs) -> Journey:
    path = tuple(
        Hex(index=i, terrain=TerrainKind.EASY, region=RegionType.WILD) for i in range(count)
    )
    journey, _ = plan_journey(
        "Origin", "Destination", path, season, party.roles, ctx=party.ctx, **kwargs
    )
    return journey


def table_of(
    *rows: dict[str, Any], die: str = DieKind.FEAT, table_id: str = "t"
) -> LookupTable[Any]:
    """A one-off table, so a test can name the event it wants without rolling for it."""
    return LookupTable(id=table_id, die=die, rows=rows_from_json(list(rows), table_id=table_id))


def one_event(event: str, fatigue: int, **branches: list[dict[str, Any]]) -> LookupTable[Any]:
    """A Feat table whose every face gives the same event."""
    value: dict[str, Any] = {"event": event, "fatigue": fatigue, **branches}
    return table_of(
        {"match": [1, 10], "value": value},
        {"match": "eye", "value": value},
        {"match": "rune", "value": value},
    )


def declare(hero_id: str, *, hex_: Hex = EASY_WILD, role=JourneyRole.SCOUT, **kwargs):
    return EventDeclaration(hex=hex_, role=role, skill="explore", target=HeroId(hero_id), **kwargs)


class TestSelectingTargets:
    def test_the_success_die_picks_a_role_and_its_skill(self, pack) -> None:
        # 10.4.1's table, as the example pack writes it.
        seen = {}
        for face in (1, 2, 3, 4, 5, 6):
            rng = ScriptedRandomness(successes=[face])
            target = select_target(rng, table=pack.table("event_target"))
            assert rng.exhausted
            seen[face] = (target.role, target.skill)
        assert {role for role, _ in seen.values()} == {
            JourneyRole.SCOUT,
            JourneyRole.LOOKOUT,
            JourneyRole.HUNTER,
        }
        for role, skill in seen.values():
            assert (
                skill
                == {"scout": "explore", "lookout": "awareness", "hunter": "hunting"}[str(role)]
            )

    def test_the_guide_is_never_a_target(self, pack) -> None:
        # 10.4.1's note: the Guide's contribution is the Marching Test itself.
        bad = table_of(
            {"match": [1, 6], "value": {"role": "guide", "skill": "travel"}}, die=DieKind.SUCCESS
        )
        rng = ScriptedRandomness(successes=[3])
        with pytest.raises(ContentError, match="Guide is never the target"):
            select_target(rng, table=bad)

    def test_a_role_paired_with_the_wrong_skill_is_a_malformed_pack(self) -> None:
        bad = table_of(
            {"match": [1, 6], "value": {"role": "scout", "skill": "hunting"}},
            die=DieKind.SUCCESS,
        )
        rng = ScriptedRandomness(successes=[3])
        with pytest.raises(ContentError, match="challenged on 'explore'"):
            select_target(rng, table=bad)

    def test_a_row_may_omit_the_skill_and_take_the_one_10_2_fixes(self) -> None:
        ok = table_of({"match": [1, 6], "value": {"role": "hunter"}}, die=DieKind.SUCCESS)
        rng = ScriptedRandomness(successes=[3])
        assert select_target(rng, table=ok).skill == "hunting"

    def test_a_row_that_is_not_an_object_is_refused(self) -> None:
        bad = table_of({"match": [1, 6], "value": "scout"}, die=DieKind.SUCCESS)
        rng = ScriptedRandomness(successes=[3])
        with pytest.raises(ContentError, match="must be an object"):
            select_target(rng, table=bad)

    def test_an_unknown_role_is_refused(self) -> None:
        bad = table_of({"match": [1, 6], "value": {"role": "cook"}}, die=DieKind.SUCCESS)
        rng = ScriptedRandomness(successes=[3])
        with pytest.raises(ContentError, match="needs a 'role'"):
            select_target(rng, table=bad)


class TestDeterminingTheEvent:
    @pytest.mark.parametrize(
        ("region", "policy", "feats"),
        [
            (RegionType.BORDER, FeatDicePolicy.FAVOURED, [4, 7]),
            (RegionType.WILD, FeatDicePolicy.NORMAL, [4]),
            (RegionType.DARK, FeatDicePolicy.ILL_FAVOURED, [4, 7]),
        ],
    )
    def test_the_region_sets_the_feat_die_policy(self, party, pack, region, policy, feats) -> None:
        p = party()
        hex_ = Hex(index=0, terrain=TerrainKind.EASY, region=region)
        rng = ScriptedRandomness(feats=feats)
        determination = determine_event(
            p["scout"], hex_, rng, ctx=p.ctx, table=pack.table("journey_events")
        )
        assert rng.exhausted, rng.remaining
        assert determination.policy is policy
        assert determination.roll.request.purpose is RollPurpose.JOURNEY_EVENT

    def test_a_cultural_virtue_resolves_as_if_in_a_border_land(self, party, pack) -> None:
        # 10.4.2: "regardless of the actual region" — the flag *replaces* the policy. Under
        # 02.3.2's cancellation a Dark Land plus this Virtue would give a normal roll.
        p = party()
        p.register("scout", "example_cv_ancient_days", EffectSource.culture("example_folk"))
        dark = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.DARK)
        rng = ScriptedRandomness(feats=[4, 7])
        determination = determine_event(
            p["scout"], dark, rng, ctx=p.ctx, table=pack.table("journey_events")
        )
        assert rng.exhausted
        assert determination.policy is FeatDicePolicy.FAVOURED
        assert determination.roll.kept_feat == 7, "the better of the two, not the worse"

    def test_an_undertaking_shifts_the_result_rather_than_adding_a_die(self, party, pack) -> None:
        # 10.4.2: Ponder Storied and Figured Maps is +1 to the *result*, via 02.2's shift.
        p = party()
        p.register("scout", "example_ponder_maps", EffectSource.acquired("undertaking"))
        rng = ScriptedRandomness(feats=[3])
        shifted = determine_event(
            p["scout"], EASY_WILD, rng, ctx=p.ctx, table=pack.table("journey_events")
        )
        assert rng.exhausted, "a shift is not a Success die"
        assert shifted.shift == 1

        plain = party()
        rng = ScriptedRandomness(feats=[3])
        unshifted = determine_event(
            plain["scout"], EASY_WILD, rng, ctx=plain.ctx, table=pack.table("journey_events")
        )
        assert shifted.event.event != unshifted.event.event, (
            "a Feat 3 and a shifted Feat 4 fall in different rows of the example table"
        )

    def test_the_shift_leaves_an_icon_face_alone(self, party, pack) -> None:
        # 02.2: a shift applies to numeric faces only.
        p = party()
        p.register("scout", "example_ponder_maps", EffectSource.acquired("undertaking"))
        rng = ScriptedRandomness(feats=[FeatFace.EYE])
        determination = determine_event(
            p["scout"], EASY_WILD, rng, ctx=p.ctx, table=pack.table("journey_events")
        )
        assert rng.exhausted
        plain = party()
        rng = ScriptedRandomness(feats=[FeatFace.EYE])
        assert (
            determination.event.event
            == determine_event(
                plain["scout"], EASY_WILD, rng, ctx=plain.ctx, table=pack.table("journey_events")
            ).event.event
        )

    def test_two_effects_pulling_opposite_ways_cancel(self, party, pack) -> None:
        # The one case 02.3.2 still governs on this hook.
        from tor.effects.library import build_effect

        p = party()
        p.register("scout", "example_cv_ancient_days", EffectSource.culture("example_folk"))
        p.buses[HeroId("scout")].register(
            build_effect(
                "shadowed_paths",
                "curse",
                "set_flag",
                {
                    "hook": "MODIFY_JOURNEY_EVENT_ROLL",
                    "flag": "ill_favoured",
                    "value": "shadowed_paths",
                },
            ),
            EffectSource.acquired("curse"),
        )
        rng = ScriptedRandomness(feats=[4])
        determination = determine_event(
            p["scout"],
            Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.DARK),
            rng,
            ctx=p.ctx,
            table=pack.table("journey_events"),
        )
        assert rng.exhausted, "one Feat die, not two"
        assert determination.policy is FeatDicePolicy.NORMAL

    def test_an_ill_favouring_effect_alone_overrides_a_border_land(self, party, pack) -> None:
        from tor.effects.library import build_effect

        p = party()
        p.buses[HeroId("scout")].register(
            build_effect(
                "shadowed_paths",
                "curse",
                "set_flag",
                {
                    "hook": "MODIFY_JOURNEY_EVENT_ROLL",
                    "flag": "ill_favoured",
                    "value": "shadowed_paths",
                },
            ),
            EffectSource.acquired("curse"),
        )
        rng = ScriptedRandomness(feats=[4, 7])
        determination = determine_event(
            p["scout"],
            Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.BORDER),
            rng,
            ctx=p.ctx,
            table=pack.table("journey_events"),
        )
        assert rng.exhausted
        assert determination.policy is FeatDicePolicy.ILL_FAVOURED
        assert determination.roll.kept_feat == 4, "the worse of the two"

    def test_contributions_of_other_shapes_leave_the_region_policy_alone(self, party, pack) -> None:
        # The hook carries exactly two meanings (02.2's shift and 10.4.2's replacement);
        # anything else on it must not quietly become one of them.
        from tor.effects.library import build_effect

        p = party()
        for effect_id, factory, params in (
            (
                "an_offer",
                "offer_action",
                {"hook": "MODIFY_JOURNEY_EVENT_ROLL", "action": "reroll"},
            ),
            (
                "a_note",
                "set_flag",
                {"hook": "MODIFY_JOURNEY_EVENT_ROLL", "flag": "well_mapped"},
            ),
        ):
            p.buses[HeroId("scout")].register(
                build_effect(effect_id, "virtue", factory, params),
                EffectSource.acquired("virtue"),
            )
        rng = ScriptedRandomness(feats=[4])
        determination = determine_event(
            p["scout"], EASY_WILD, rng, ctx=p.ctx, table=pack.table("journey_events")
        )
        assert rng.exhausted, "neither shape is a Feat die"
        assert determination.policy is FeatDicePolicy.NORMAL
        assert determination.shift == 0

    def test_a_row_that_is_not_an_object_is_refused(self, party) -> None:
        p = party()
        rng = ScriptedRandomness(feats=[4])
        with pytest.raises(ContentError, match="must be an object"):
            determine_event(p["scout"], EASY_WILD, rng, ctx=p.ctx, table=one_of_value("despair"))

    def test_a_row_without_an_event_name_is_refused(self, party) -> None:
        p = party()
        rng = ScriptedRandomness(feats=[4])
        with pytest.raises(ContentError, match="needs an 'event'"):
            determine_event(p["scout"], EASY_WILD, rng, ctx=p.ctx, table=one_event("", 2))

    def test_an_op_belonging_to_another_subsystem_is_refused(self, party) -> None:
        # 05.6's vocabulary is shared; a journey table may use only the journey half.
        p = party()
        rng = ScriptedRandomness(feats=[4])
        bad = one_event(
            "unseen_council", 1, on_failure=[{"op": "council_resistance_step", "steps": 1}]
        )
        with pytest.raises(ContentError, match="not one a journey event may carry"):
            determine_event(p["scout"], EASY_WILD, rng, ctx=p.ctx, table=bad)


def one_of_value(value: str) -> LookupTable[Any]:
    return table_of(
        {"match": [1, 10], "value": value},
        {"match": "eye", "value": value},
        {"match": "rune", "value": value},
    )


class TestResolvingTheEvent:
    def test_hard_terrain_costs_the_roll_a_die_and_a_road_grants_one(self, party) -> None:
        p = party()
        determination = _determination("mishap", 2)
        hard = Hex(index=0, terrain=TerrainKind.HARD, region=RegionType.WILD)
        rng = ScriptedRandomness(feats=[9], successes=[6])
        outcome = resolve_event(
            p["scout"], determination, declare("scout", hex_=hard), rng, ctx=p.ctx
        )
        assert rng.exhausted, "rating 2 less one penalty die"
        assert outcome.roll.request.penalty_dice == 1

        road = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.WILD, has_road=True)
        rng = ScriptedRandomness(feats=[9], successes=[6, 6, 6])
        outcome = resolve_event(
            party()["scout"], determination, declare("scout", hex_=road), rng, ctx=p.ctx
        )
        assert rng.exhausted, "rating 2 plus one bonus die"
        assert outcome.roll.request.bonus_dice == 1

    def test_a_failure_fires_the_on_failure_list(self, party) -> None:
        p = party()
        determination = _determination(
            "ill_choices", 2, on_failure=[{"op": "shadow", "who": "target", "points": 1}]
        )
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = resolve_event(p["scout"], determination, declare("scout"), rng, ctx=p.ctx)
        assert rng.exhausted
        assert not outcome.succeeded
        assert [op.kind for op in outcome.ops] == [OpKind.SHADOW]
        assert outcome.fatigue == 2, "10.4.3: paid regardless of the roll"

    def test_a_success_fires_the_on_success_list(self, party) -> None:
        p = party()
        determination = _determination(
            "short_cut", 1, on_success=[{"op": "journey_days", "delta": -1}]
        )
        rng = ScriptedRandomness(feats=[10], successes=[6, 6])
        outcome = resolve_event(p["scout"], determination, declare("scout"), rng, ctx=p.ctx)
        assert rng.exhausted
        assert outcome.succeeded
        assert [op.kind for op in outcome.ops] == [OpKind.JOURNEY_DAYS]

    def test_a_row_may_suppress_its_own_fatigue(self, party) -> None:
        # Chance-meeting on a success: 10.4.3's one exception to "paid regardless".
        p = party()
        determination = _determination(
            "chance_meeting",
            1,
            on_success=[{"op": "suppress_fatigue"}, {"op": "narrative", "tag": "friendly"}],
        )
        rng = ScriptedRandomness(feats=[10], successes=[6, 6])
        outcome = resolve_event(p["scout"], determination, declare("scout"), rng, ctx=p.ctx)
        assert rng.exhausted
        assert outcome.suppressed and outcome.fatigue == 0
        assert outcome.narrative == ("friendly",)

    def test_the_same_row_failed_still_charges_its_fatigue(self, party) -> None:
        p = party()
        determination = _determination("chance_meeting", 1, on_success=[{"op": "suppress_fatigue"}])
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = resolve_event(p["scout"], determination, declare("scout"), rng, ctx=p.ctx)
        assert rng.exhausted
        assert not outcome.suppressed and outcome.fatigue == 1

    def test_one_companion_in_the_same_role_may_support(self, party) -> None:
        p = party()
        p.add("tracker")
        journey = trek(p)
        journey.roles[HeroId("tracker")] = {JourneyRole.SCOUT}
        rng = ScriptedRandomness(feats=[9], successes=[6, 6, 3])
        outcome = resolve_event(
            p["scout"],
            _determination("mishap", 2),
            declare(
                "scout",
                supporter=HeroId("tracker"),
                support=Support(rating=2, approved=True),
            ),
            rng,
            ctx=p.ctx,
            journey=journey,
        )
        assert rng.exhausted
        assert outcome.roll.request.dice_count == 3

    def test_a_companion_outside_the_role_may_not(self, party) -> None:
        p = party()
        journey = trek(p)
        rng = ScriptedRandomness(feats=[9])
        with pytest.raises(RuleViolation, match="does not cover the scout role"):
            resolve_event(
                p["scout"],
                _determination("mishap", 2),
                declare(
                    "scout",
                    supporter=HeroId("hunter"),
                    support=Support(rating=2, approved=True),
                ),
                rng,
                ctx=p.ctx,
                journey=journey,
            )

    def test_a_hero_cannot_support_their_own_roll(self, party) -> None:
        p = party()
        rng = ScriptedRandomness(feats=[9])
        with pytest.raises(RuleViolation, match="support their own roll"):
            resolve_event(
                p["scout"],
                _determination("mishap", 2),
                declare("scout", supporter=HeroId("scout")),
                rng,
                ctx=p.ctx,
            )

    def test_naming_a_supporter_without_offering_support_is_refused(self, party) -> None:
        p = party()
        rng = ScriptedRandomness(feats=[9])
        with pytest.raises(RuleViolation, match="no support was offered"):
            resolve_event(
                p["scout"],
                _determination("mishap", 2),
                declare("scout", supporter=HeroId("hunter")),
                rng,
                ctx=p.ctx,
            )

    def test_without_the_journey_the_role_check_is_skipped(self, party) -> None:
        # The journey argument is optional; only the role check needs it.
        p = party()
        rng = ScriptedRandomness(feats=[9], successes=[6, 6, 3])
        outcome = resolve_event(
            p["scout"],
            _determination("mishap", 2),
            declare(
                "scout",
                supporter=HeroId("hunter"),
                support=Support(rating=2, approved=True),
            ),
            rng,
            ctx=p.ctx,
        )
        assert rng.exhausted and outcome.roll.request.dice_count == 3


def _determination(event: str, fatigue: int, **branches: list[dict[str, Any]]):
    """An EventDetermination built without rolling, so a test can name the event it wants."""
    from tor.rolls import Degree, Outcome, RollRequest, RollResult
    from tor.rules.journey import EventDetermination

    roll = RollResult(
        request=RollRequest(purpose=RollPurpose.JOURNEY_EVENT),
        feat_dice=(4,),
        kept_feat=4,
        success_dice=(),
        total=4,
        outcome=Outcome.NO_TN,
        degree=Degree.NONE,
        icons=0,
    )
    return EventDetermination(
        event=JourneyEvent(
            event=event,
            fatigue=fatigue,
            on_failure=parse_ops(branches.get("on_failure", ()), entity_id=event),
            on_success=parse_ops(branches.get("on_success", ()), entity_id=event),
        ),
        roll=roll,
        policy=FeatDicePolicy.NORMAL,
        shift=0,
    )


class TestApplyingTheEvent:
    def test_the_whole_company_pays_the_fatigue(self, party) -> None:
        # 10.4.3: the Fatigue cost is paid by everyone in the Company.
        p = party()
        journey = trek(p)
        outcome = _resolved(p, journey, "ill_choices", 2, feat=1)
        events = apply_event(journey, outcome, p.heroes, ctx=p.ctx)

        assert {hero.fatigue for hero in p.heroes.values()} == {2}
        assert sum(1 for e in events if e.kind is EventKind.FATIGUE_CHANGED) == 4
        assert events[-1].kind is EventKind.JOURNEY_EVENT_RESOLVED
        assert events[-1].payload["fatigue"] == 2
        assert events[-1].rolls == (outcome.determination.roll, outcome.roll)

    def test_a_suppressed_event_charges_nobody(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p, journey, "chance_meeting", 1, feat=10, on_success=[{"op": "suppress_fatigue"}]
        )
        events = apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert {hero.fatigue for hero in p.heroes.values()} == {0}
        assert not any(e.kind is EventKind.FATIGUE_CHANGED for e in events)

    def test_despair_gives_the_whole_company_shadow(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "despair",
            2,
            feat=1,
            on_failure=[{"op": "shadow", "who": "company", "points": 1, "source": "dread"}],
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert {hero.shadow for hero in p.heroes.values()} == {1}

    def test_mishap_adds_a_day_and_one_extra_fatigue_to_the_target(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "mishap",
            2,
            feat=1,
            on_failure=[
                {"op": "journey_days", "delta": 1},
                {"op": "fatigue", "who": "target", "points": 1},
            ],
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert journey.day_adjustments == 1
        assert p["scout"].fatigue == 3, "two from the event, one extra from the op"
        assert p["hunter"].fatigue == 2

    def test_a_short_cut_takes_a_day_off(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p, journey, "short_cut", 1, feat=10, on_success=[{"op": "journey_days", "delta": -1}]
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert journey.day_adjustments == -1

    def test_a_joyful_sight_restores_the_companys_hope(self, party) -> None:
        p = party()
        for hero in p.heroes.values():
            hero.hope = 5
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "joyful_sight",
            0,
            feat=10,
            on_success=[{"op": "hope", "who": "company", "points": 1}],
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert {hero.hope for hero in p.heroes.values()} == {6}

    def test_a_terrible_misfortune_wounds_the_target(self, party) -> None:
        # 10.4.3, and 01.1's rule that journey reaches a Wound through injury.
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p, journey, "terrible_misfortune", 3, feat=1, on_failure=[{"op": "wound"}]
        )
        events = apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert p["scout"].conditions.wounded
        assert not p["hunter"].conditions.wounded
        wound = next(e for e in events if e.kind is EventKind.WOUND_RECEIVED)
        assert wound.payload["event"] == "terrible_misfortune"
        assert wound.payload["injury_days"] == 0, "10.4.3 names no severity roll"
        assert not wound.payload["dying"]

    def test_a_second_wound_on_the_road_follows_8_8(self, party) -> None:
        p = party()
        p["scout"].conditions.wounded = True
        journey = trek(p)
        outcome = _resolved(
            p, journey, "terrible_misfortune", 3, feat=1, on_failure=[{"op": "wound"}]
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        wounded = p["scout"]
        assert wounded.conditions.wounded
        assert not wounded.dying, "10.4.3 inflicts a Wound, not the Dying condition"

    def test_the_supporter_shares_the_consequence_only_when_the_lm_says_so(self, party) -> None:
        # 10.4.4: "and to the supporting hero as well, where relevant" — a ruling, not a rule.
        p = party()
        p.add("tracker")
        journey = trek(p)
        journey.roles[HeroId("tracker")] = {JourneyRole.SCOUT}
        outcome = _resolved(
            p,
            journey,
            "ill_choices",
            2,
            feat=1,
            on_failure=[{"op": "shadow", "who": "target", "points": 1}],
            supporter=HeroId("tracker"),
            support=Support(rating=2, approved=True),
            extra_successes=(3,),
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert p["scout"].shadow == 1
        assert p["tracker"].shadow == 0

        shared = party()
        shared.add("tracker")
        journey2 = trek(shared)
        journey2.roles[HeroId("tracker")] = {JourneyRole.SCOUT}
        outcome2 = _resolved(
            shared,
            journey2,
            "ill_choices",
            2,
            feat=1,
            on_failure=[{"op": "shadow", "who": "target", "points": 1}],
            supporter=HeroId("tracker"),
            support=Support(rating=2, approved=True),
            supporter_shares=True,
            extra_successes=(3,),
        )
        apply_event(journey2, outcome2, shared.heroes, ctx=shared.ctx)
        assert shared["scout"].shadow == 1 and shared["tracker"].shadow == 1

    def test_an_actor_op_reaches_only_the_hero_who_rolled(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "ill_choices",
            0,
            feat=1,
            on_failure=[{"op": "endurance", "who": "actor", "points": -3}],
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert p["scout"].endurance == 21
        assert p["hunter"].endurance == 24

    def test_a_chosen_op_needs_somebody_chosen(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "ill_choices",
            0,
            feat=1,
            on_failure=[{"op": "fatigue", "who": "chosen", "points": 1}],
        )
        with pytest.raises(RuleViolation, match="falls on a chosen hero"):
            apply_event(journey, outcome, p.heroes, ctx=p.ctx)

    def test_a_chosen_op_falls_on_whoever_was_named(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "ill_choices",
            0,
            feat=1,
            on_failure=[{"op": "fatigue", "who": "chosen", "points": 1}],
            chosen=(HeroId("watcher"),),
        )
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert p["watcher"].fatigue == 1 and p["scout"].fatigue == 0

    def test_a_condition_op_may_only_set_wounded(self, party) -> None:
        p = party()
        journey = trek(p)
        wounds = _resolved(
            p,
            journey,
            "falling",
            0,
            feat=1,
            on_failure=[{"op": "condition", "condition": "wounded"}],
        )
        apply_event(journey, wounds, p.heroes, ctx=p.ctx)
        assert p["scout"].conditions.wounded

        other = party()
        journey2 = trek(other)
        derived = _resolved(
            other,
            journey2,
            "exhaustion",
            0,
            feat=1,
            on_failure=[{"op": "condition", "condition": "weary"}],
        )
        with pytest.raises(ContentError, match="only 'wounded' may be set directly"):
            apply_event(journey2, derived, other.heroes, ctx=other.ctx)

    def test_an_unknown_shadow_source_is_a_malformed_pack(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "ill_choices",
            0,
            feat=1,
            on_failure=[{"op": "shadow", "who": "target", "points": 1, "source": "ennui"}],
        )
        with pytest.raises(ContentError, match="unknown Shadow source"):
            apply_event(journey, outcome, p.heroes, ctx=p.ctx)

    def test_a_narrative_tag_changes_nothing_but_the_log(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(
            p,
            journey,
            "chance_meeting",
            0,
            feat=10,
            on_success=[{"op": "narrative", "tag": "favourable_encounter"}],
        )
        events = apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert events[-1].payload["narrative"] == ["favourable_encounter"]
        assert {hero.fatigue for hero in p.heroes.values()} == {0}

    def test_the_event_is_recorded_on_the_journey(self, party) -> None:
        p = party()
        marsh = PerilousArea(id="marsh", name="Marsh", peril=1)
        journey = trek(p)
        in_marsh = Hex(
            index=2, terrain=TerrainKind.EASY, region=RegionType.WILD, perilous_area=marsh
        )
        outcome = _resolved(p, journey, "ill_choices", 2, feat=1, hex_=in_marsh)
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        (record,) = journey.events_resolved
        assert record.hex_index == 2
        assert record.event == "ill_choices"
        assert record.role is JourneyRole.SCOUT
        assert record.target == "scout"
        assert not record.succeeded
        assert record.fatigue == 2
        assert record.perilous_area == "marsh"

    def test_a_finished_journey_resolves_no_further_event(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(p, journey, "ill_choices", 2, feat=1)
        journey.finished = True
        with pytest.raises(StateError, match="already resolved"):
            apply_event(journey, outcome, p.heroes, ctx=p.ctx)

    def test_a_hero_holding_a_role_must_be_supplied(self, party) -> None:
        p = party()
        journey = trek(p)
        outcome = _resolved(p, journey, "ill_choices", 2, feat=1)
        short = {k: v for k, v in p.heroes.items() if k != HeroId("hunter")}
        with pytest.raises(StateError, match="were not supplied"):
            apply_event(journey, outcome, short, ctx=p.ctx)


def _resolved(
    party: Party,
    journey: Journey,
    event: str,
    fatigue: int,
    *,
    feat: object,
    hex_: Hex = EASY_WILD,
    extra_successes: tuple[int, ...] = (),
    **kwargs,
):
    """Resolve one event on the scout, scripting a roll that lands the branch asked for."""
    branches = {k: kwargs.pop(k) for k in ("on_failure", "on_success") if k in kwargs}
    determination = _determination(event, fatigue, **branches)
    successes = ((6, 6) if feat == 10 else (3, 3)) + extra_successes
    rng = ScriptedRandomness(feats=[feat], successes=list(successes))
    outcome = resolve_event(
        party.heroes[HeroId("scout")],
        determination,
        declare("scout", hex_=hex_, **kwargs),
        rng,
        ctx=party.ctx,
        journey=journey,
    )
    assert rng.exhausted, rng.remaining
    return outcome


class TestEndingTheJourney:
    @staticmethod
    def finish(journey: Journey) -> Journey:
        journey.position = len(journey.path)
        journey.finished = True
        return journey

    def test_an_unfinished_journey_is_not_settled(self, party) -> None:
        p = party()
        rng = ScriptedRandomness()
        with pytest.raises(StateError, match="has not finished"):
            end_journey(trek(p), list(p.heroes.values()), rng, ctx=p.ctx)

    def test_the_travel_roll_sheds_one_plus_one_per_icon(self, party) -> None:
        # 10.6 step 2, pattern P3.
        p = party()
        for hero in p.heroes.values():
            hero.fatigue = 6
        journey = self.finish(trek(p, count=4))
        rng = ScriptedRandomness(feats=[9] * 4, successes=[6, 6] * 4)
        outcome = end_journey(journey, list(p.heroes.values()), rng, ctx=p.ctx)
        assert rng.exhausted, rng.remaining
        for relief in outcome.relief:
            assert relief.from_roll == FATIGUE_RELIEF_BASE + 2
            assert relief.residual == 6 - 3
        assert {hero.fatigue for hero in p.heroes.values()} == {3}

    def test_a_failed_travel_roll_sheds_nothing(self, party) -> None:
        p = party()
        for hero in p.heroes.values():
            hero.fatigue = 4
        journey = self.finish(trek(p, count=4))
        rng = ScriptedRandomness(feats=[1] * 4, successes=[3, 3] * 4)
        outcome = end_journey(journey, list(p.heroes.values()), rng, ctx=p.ctx)
        assert rng.exhausted
        assert all(r.from_roll == 0 and r.residual == 4 for r in outcome.relief)

    def test_a_mount_sheds_its_vigour_first(self, party) -> None:
        p = party()
        for hero in p.heroes.values():
            hero.fatigue = 5
        journey = self.finish(trek(p, count=4))
        journey.mounts[HeroId("guide")] = Mount(name="Pony", vigour=2)
        rng = ScriptedRandomness(feats=[1] * 4, successes=[3, 3] * 4)
        outcome = end_journey(journey, list(p.heroes.values()), rng, ctx=p.ctx)
        assert rng.exhausted
        mounted = next(r for r in outcome.relief if r.hero == "guide")
        assert (mounted.from_mount, mounted.residual) == (2, 3)
        assert all(r.from_mount == 0 for r in outcome.relief if r.hero != "guide")

    def test_a_cultural_virtue_raises_the_mounts_vigour(self, party) -> None:
        p = party()
        p["guide"].fatigue = 5
        p.register("guide", "second_cv_hardy_pony", EffectSource.culture("second_folk"))
        journey = self.finish(trek(p, count=4))
        journey.mounts[HeroId("guide")] = Mount(name="Pony", vigour=1)
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        assert outcome.relief[0].from_mount == 2

    def test_a_mount_never_sheds_more_than_the_hero_carries(self, party) -> None:
        p = party()
        p["guide"].fatigue = 1
        journey = self.finish(trek(p, count=4))
        journey.mounts[HeroId("guide")] = Mount(name="Pony", vigour=4)
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        assert outcome.relief[0].from_mount == 1 and outcome.relief[0].residual == 0

    def test_an_effect_sheds_a_point_whatever_the_roll_did(self, party) -> None:
        # ON_JOURNEY_END, which 04.3.4 lists with no example of its own.
        p = party()
        p["guide"].fatigue = 3
        p.register("guide", "example_road_hardened", EffectSource.acquired("virtue"))
        journey = self.finish(trek(p, count=4))
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        assert (outcome.relief[0].from_roll, outcome.relief[0].from_effects) == (0, 1)
        assert p["guide"].fatigue == 2

    def test_a_forced_march_charges_its_days_before_any_relief(self, party) -> None:
        # 10.7: 1 additional Fatigue per day of forced march.
        p = party()
        journey = self.finish(trek(p, count=6, forced_march=True))
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        relief = outcome.relief[0]
        assert outcome.days == 3
        assert relief.forced_march == 3
        assert relief.before == 3, "charged before the relief, so the roll cannot shed it early"
        assert p["guide"].fatigue == 3

    def test_the_closing_event_records_the_residual(self, party) -> None:
        p = party()
        p["guide"].fatigue = 4
        journey = self.finish(trek(p, count=4))
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        closing = outcome.events[-1]
        assert closing.kind is EventKind.JOURNEY_ENDED
        assert closing.payload["narrated"] is False
        assert closing.payload["arrived"] is True
        assert closing.payload["residual_fatigue"] == {"guide": 4}
        assert residual_fatigue(outcome.relief) == {"guide": 4}

    def test_an_interrupted_journey_still_gets_its_relief(self, party) -> None:
        p = party()
        p["guide"].fatigue = 2
        journey = trek(p, count=10)
        journey.position = 3
        journey.interrupt("called away")
        rng = ScriptedRandomness(feats=[9], successes=[6, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        assert not outcome.arrived
        assert outcome.relief[0].residual == 0

    def test_the_mounted_forced_march_warning_rides_on_the_outcome(self, party) -> None:
        p = party()
        journey = self.finish(trek(p, count=4, mounted=True, forced_march=True))
        rng = ScriptedRandomness(feats=[1], successes=[3, 3])
        outcome = end_journey(journey, [p["guide"]], rng, ctx=p.ctx)
        assert rng.exhausted
        assert [w.code for w in outcome.warnings] == ["mounted_forced_march"]


class TestFatigueAndWeariness:
    def test_fatigue_raises_load_and_tips_a_hero_weary(self, party) -> None:
        # 19.4's Fatigue-and-Load vector, reached the way a journey reaches it.
        p = party()
        hero = p["scout"]
        hero.endurance = 10
        journey = trek(p)
        before = [h.conditions.weary for h in p.heroes.values()]
        assert not any(before)

        from tor.rules.resources import recompute_load

        baseline = recompute_load(hero, ctx=p.ctx)
        outcome = _resolved(p, journey, "ill_choices", 2, feat=1)
        apply_event(journey, outcome, p.heroes, ctx=p.ctx)
        assert recompute_load(hero, ctx=p.ctx) == baseline + 2
        assert hero.conditions.weary == (hero.endurance <= baseline + 2)

    def test_fatigue_cannot_be_shed_on_the_road(self, party) -> None:
        # 10.5, which is already resources': 07.6 sheds a point only when sheltered.
        from tor.rules.resources import RestKind, apply_rest, resolve_rest

        p = party()
        hero = p["guide"]
        hero.fatigue = 3
        hero.endurance = 4

        rough = resolve_rest(RestKind.PROLONGED, [hero], ctx=p.ctx, sheltered=False)
        apply_rest(RestKind.PROLONGED, [hero], rough, ctx=p.ctx)
        assert hero.fatigue == 3, "a camp on the road is not a safe refuge"

        sheltered = resolve_rest(RestKind.PROLONGED, [hero], ctx=p.ctx, sheltered=True)
        apply_rest(RestKind.PROLONGED, [hero], sheltered, ctx=p.ctx)
        assert hero.fatigue == 2


class TestATwelveHexJourney:
    """The shape of 19.8's third golden transcript, as an integration test.

    Not the golden file itself — recorded transcripts land with the session layer at build
    step 16. What this asserts is that the five calls compose: a twelve-hex path through two
    region types, four events including a Terrible Misfortune, and Fatigue relief at the end.
    """

    PATH = tuple(
        Hex(
            index=i,
            terrain=TerrainKind.HARD if i in (4, 5) else TerrainKind.EASY,
            region=RegionType.BORDER if i < 6 else RegionType.DARK,
        )
        for i in range(12)
    )

    def test_the_whole_trip_composes(self, party, pack) -> None:
        p = party()
        journey, warnings = plan_journey(
            "Rivendell", "The Carrock", self.PATH, Season.AUTUMN, p.roles, ctx=p.ctx
        )
        assert warnings == []
        log = []

        # Four marches of three hexes each: the first three draw an event, the fourth arrives.
        for leg in range(4):
            rng = ScriptedRandomness(feats=[9], successes=[3, 3])
            roll = roll_marching_test(journey, p["guide"], rng, ctx=p.ctx)
            assert rng.exhausted
            step = resolve_step(journey, roll)
            log += apply_step(journey, step)
            if step.arrived:
                assert leg == 3 and step.events_due == 0
                break

            assert step.event_hex is not None
            rng = ScriptedRandomness(successes=[1])
            target = select_target(rng, table=pack.table("event_target"))
            assert rng.exhausted and target.role is JourneyRole.SCOUT

            # The first two legs are Border Land (Favoured), the third is Dark (Ill-favoured).
            region = step.event_hex.region
            rng = ScriptedRandomness(feats=[FeatFace.EYE, FeatFace.EYE])
            determination = determine_event(
                p["scout"], step.event_hex, rng, ctx=p.ctx, table=pack.table("journey_events")
            )
            assert rng.exhausted, "two Feat dice, whichever way the region leans"
            assert determination.policy is (
                FeatDicePolicy.FAVOURED
                if region is RegionType.BORDER
                else FeatDicePolicy.ILL_FAVOURED
            )
            assert determination.event.event == "terrible_misfortune"

            bonus, penalty = (0, 1) if step.event_hex.hard else (0, 0)
            rng = ScriptedRandomness(feats=[1], successes=[3] * (2 - penalty + bonus))
            outcome = resolve_event(
                p["scout"],
                determination,
                EventDeclaration(
                    hex=step.event_hex,
                    role=target.role,
                    skill=target.skill,
                    target=HeroId("scout"),
                ),
                rng,
                ctx=p.ctx,
                journey=journey,
            )
            assert rng.exhausted, rng.remaining
            assert not outcome.succeeded
            log += apply_event(journey, outcome, p.heroes, ctx=p.ctx)

        assert journey.finished and journey.position == 12
        assert len(journey.events_resolved) == 3
        assert {r.event for r in journey.events_resolved} == {"terrible_misfortune"}

        # Three events at 3 Fatigue each, paid by the whole Company (10.4.3).
        assert {hero.fatigue for hero in p.heroes.values()} == {9}
        assert p["scout"].conditions.wounded
        assert not p["guide"].conditions.wounded

        rng = ScriptedRandomness(feats=[9] * 4, successes=[6, 6] * 4)
        end = end_journey(journey, list(p.heroes.values()), rng, ctx=p.ctx)
        assert rng.exhausted, rng.remaining
        assert end.arrived
        assert end.days == 12 + 2, "one day per hex, plus one for each of the two hard hexes"
        assert all(r.from_roll == FATIGUE_RELIEF_BASE + 2 for r in end.relief)
        assert {hero.fatigue for hero in p.heroes.values()} == {6}
        assert [e.kind for e in log].count(EventKind.JOURNEY_EVENT_RESOLVED) == 3
        assert end.events[-1].kind is EventKind.JOURNEY_ENDED
