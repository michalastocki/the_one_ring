"""`tor.rules.journey` — the path, the roles, Marching Tests and the days (spec 10.1-10.3, 10.7).

Event resolution has its own file. What is asserted here is the shape of the trip: that the
path excludes the starting hex, that a Marching Test carries the Company the distance 10.3.1
says it does, that arrival is checked before the event, and that a Perilous Area stops the
march dead.

Every scripted roll ends with `rng.exhausted` asserted, per 19.2.
"""

from __future__ import annotations

import pytest
from conftest import Party, build_hero

from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.conditions import RegionType, Season
from tor.model.ids import HeroId
from tor.rolls import FeatDicePolicy, RollPurpose, Support
from tor.rules.journey import (
    FAILED_MARCH_COLD,
    FAILED_MARCH_WARM,
    MARCHING_BASE,
    MAX_HEXES_PER_LEG,
    REGION_POLICY,
    ROLE_SKILL,
    Hex,
    Journey,
    JourneyRole,
    PerilousArea,
    TerrainKind,
    apply_step,
    began_event,
    event_dice,
    exit_perilous_area,
    forced_march_fatigue,
    journey_day_warnings,
    journey_days,
    marching_distance,
    plan_journey,
    resolve_step,
    roll_marching_test,
    validate_roles,
)


def hexes(
    count: int,
    *,
    terrain: TerrainKind = TerrainKind.EASY,
    region: RegionType = RegionType.WILD,
) -> tuple[Hex, ...]:
    return tuple(Hex(index=i, terrain=terrain, region=region) for i in range(count))


def trek(
    party: Party,
    *,
    count: int = 10,
    season: Season = Season.SUMMER,
    path: tuple[Hex, ...] | None = None,
    **kwargs: object,
) -> Journey:
    journey, _ = plan_journey(
        "Origin",
        "Destination",
        path if path is not None else hexes(count),
        season,
        party.roles,
        ctx=party.ctx,
        **kwargs,  # type: ignore[arg-type]
    )
    return journey


def march(party: Party, journey: Journey, *, feat: object, successes: tuple[int, ...] = ()):
    """One Marching Test by the Guide, asserting the dice queue drains exactly."""
    rng = ScriptedRandomness(feats=[feat], successes=list(successes))
    roll = roll_marching_test(journey, party["guide"], rng, ctx=party.ctx)
    assert rng.exhausted, rng.remaining
    return roll


class TestThePath:
    def test_the_path_excludes_the_starting_hex(self, party) -> None:
        # 19.4: a five-hex path is five hexes to cover, and arriving takes exactly five.
        p = party()
        journey = trek(p, count=5)
        assert len(journey.path) == 5
        assert journey.remaining == 5
        assert journey.current_hex is None, "the Company has not left the origin"

        journey.position = 4
        assert journey.remaining == 1
        assert journey.current_hex is not None and journey.current_hex.index == 3

    def test_a_hex_numbered_from_one_is_refused(self, party) -> None:
        p = party()
        misnumbered = tuple(
            Hex(index=i, terrain=TerrainKind.EASY, region=RegionType.WILD) for i in range(1, 6)
        )
        with pytest.raises(RuleViolation, match="the path is 0-based"):
            plan_journey("A", "B", misnumbered, Season.SUMMER, p.roles, ctx=p.ctx)

    def test_an_empty_path_is_not_a_journey(self, party) -> None:
        p = party()
        with pytest.raises(RuleViolation, match="at least one hex"):
            plan_journey("A", "B", (), Season.SUMMER, p.roles, ctx=p.ctx)

    def test_a_long_leg_warns_rather_than_failing(self, party) -> None:
        # 10.1: "enforce with a warning at journey construction, not an error".
        p = party()
        journey, warnings = plan_journey(
            "A", "B", hexes(MAX_HEXES_PER_LEG + 1), Season.SUMMER, p.roles, ctx=p.ctx
        )
        assert len(journey.path) == MAX_HEXES_PER_LEG + 1
        assert [w.code for w in warnings] == ["journey_leg_too_long"]

    def test_a_leg_of_exactly_twenty_hexes_is_unremarkable(self, party) -> None:
        p = party()
        _, warnings = plan_journey(
            "A", "B", hexes(MAX_HEXES_PER_LEG), Season.SUMMER, p.roles, ctx=p.ctx
        )
        assert warnings == []

    def test_hard_going_and_roads_are_read_off_the_hex(self) -> None:
        easy = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.WILD)
        hard = Hex(index=0, terrain=TerrainKind.HARD, region=RegionType.WILD)
        paved = Hex(index=0, terrain=TerrainKind.ROAD, region=RegionType.WILD)
        hard_road = Hex(index=0, terrain=TerrainKind.HARD, region=RegionType.WILD, has_road=True)
        assert (easy.hard, easy.along_road) == (False, False)
        assert (hard.hard, hard.along_road) == (True, False)
        assert (paved.hard, paved.along_road) == (False, True)
        assert (hard_road.hard, hard_road.along_road) == (True, True), (
            "a hill with a road through it is still hard going"
        )

    def test_a_peril_rating_cannot_be_negative(self) -> None:
        with pytest.raises(ValueError, match="Peril rating cannot be negative"):
            PerilousArea(id="marsh", name="Marsh", peril=-1)

    def test_the_opening_log_entry_names_the_roles(self, party) -> None:
        p = party()
        event = began_event(trek(p, count=3))
        assert event.kind is EventKind.JOURNEY_BEGAN
        assert event.payload["hexes"] == 3
        assert event.payload["roles"]["guide"] == ["guide"]


class TestRoles:
    def test_the_ordinary_company_covers_one_role_each(self, party) -> None:
        p = party()
        validate_roles(p.roles, ctx=p.ctx)

    def test_every_role_has_its_challenged_skill(self) -> None:
        # 10.2's table, and 10.4.1's note that the Guide is never an event's target.
        assert ROLE_SKILL[JourneyRole.GUIDE] == "travel"
        assert ROLE_SKILL[JourneyRole.HUNTER] == "hunting"
        assert ROLE_SKILL[JourneyRole.LOOKOUT] == "awareness"
        assert ROLE_SKILL[JourneyRole.SCOUT] == "explore"

    def test_an_empty_assignment_is_refused(self, party) -> None:
        with pytest.raises(RuleViolation, match="needs heroes"):
            validate_roles({}, ctx=party().ctx)

    def test_a_journey_needs_a_guide(self, party) -> None:
        p = party()
        roles = dict(p.roles)
        roles[HeroId("guide")] = {JourneyRole.SCOUT}
        with pytest.raises(RuleViolation, match="needs a Guide"):
            validate_roles(roles, ctx=p.ctx)

    def test_two_guides_is_never_allowed(self, party) -> None:
        p = party()
        roles = dict(p.roles)
        roles[HeroId("scout")] = {JourneyRole.GUIDE, JourneyRole.SCOUT}
        with pytest.raises(RuleViolation, match="exactly one Guide"):
            validate_roles(roles, ctx=p.ctx)

    def test_an_uncovered_role_is_refused(self, party) -> None:
        p = party()
        roles = dict(p.roles)
        del roles[HeroId("hunter")]
        with pytest.raises(RuleViolation, match="uncovered: \\['hunter'\\]"):
            validate_roles(roles, ctx=p.ctx)

    def test_a_company_of_three_may_double_up_out_of_necessity(self, party) -> None:
        # 10.2 says a hero holding several roles needs permission, and two lines later says
        # fewer than four heroes *must* double up. Necessity wins where it applies.
        p = party()
        roles = {
            HeroId("guide"): {JourneyRole.GUIDE, JourneyRole.HUNTER},
            HeroId("watcher"): {JourneyRole.LOOKOUT},
            HeroId("scout"): {JourneyRole.SCOUT},
        }
        validate_roles(roles, ctx=p.ctx)

    def test_a_company_of_four_may_not_double_up_unbidden(self, party) -> None:
        p = party()
        roles = dict(p.roles)
        roles[HeroId("scout")] = {JourneyRole.SCOUT, JourneyRole.HUNTER}
        with pytest.raises(RuleViolation, match="holds 2 roles"):
            validate_roles(roles, ctx=p.ctx)

    def test_a_cultural_virtue_lifts_the_one_role_rule(self, party) -> None:
        p = party()
        p.register("scout", "second_cv_ways_of_the_wild", EffectSource.culture("second_folk"))
        roles = dict(p.roles)
        roles[HeroId("scout")] = {JourneyRole.SCOUT, JourneyRole.HUNTER}
        validate_roles(roles, ctx=p.ctx)

    def test_a_numeric_contribution_raises_the_limit_instead(self, party) -> None:
        # JOURNEY_ROLE_LIMITS reads either shape, so a future effect can grant one extra
        # role without granting all four.
        from tor.effects.library import build_effect

        p = party()
        p.buses[HeroId("scout")].register(
            build_effect(
                "two_roles",
                "virtue",
                "numeric_modifier",
                {"hook": "JOURNEY_ROLE_LIMITS", "delta": 1},
            ),
            EffectSource.acquired("virtue"),
        )
        roles = dict(p.roles)
        roles[HeroId("scout")] = {JourneyRole.SCOUT, JourneyRole.HUNTER}
        validate_roles(roles, ctx=p.ctx)

        roles[HeroId("scout")] = {JourneyRole.SCOUT, JourneyRole.HUNTER, JourneyRole.LOOKOUT}
        with pytest.raises(RuleViolation, match="holds 3 roles"):
            validate_roles(roles, ctx=p.ctx)

    def test_a_contribution_of_another_shape_neither_permits_nor_forbids(self, party) -> None:
        # The hook answers a yes/no question with a flag or a limit with a number; anything
        # else on it is somebody else's business and must not accidentally grant permission.
        from tor.effects.library import build_effect

        p = party()
        for effect_id, factory, params in (
            (
                "an_offer",
                "offer_action",
                {"hook": "JOURNEY_ROLE_LIMITS", "action": "swap_roles"},
            ),
            (
                "a_denial",
                "set_flag",
                {"hook": "JOURNEY_ROLE_LIMITS", "flag": "multiple_roles", "value": False},
            ),
        ):
            p.buses[HeroId("scout")].register(
                build_effect(effect_id, "virtue", factory, params),
                EffectSource.acquired("virtue"),
            )
        roles = dict(p.roles)
        roles[HeroId("scout")] = {JourneyRole.SCOUT, JourneyRole.HUNTER}
        with pytest.raises(RuleViolation, match="holds 2 roles"):
            validate_roles(roles, ctx=p.ctx)


class TestMarchingDistance:
    @pytest.mark.parametrize(
        ("feat", "successes", "expected"),
        [
            # 19.4: success with 2 icons -> 5 hexes.
            (9, (6, 6), MARCHING_BASE + 2),
            (9, (6, 3), MARCHING_BASE + 1),
            (9, (3, 3), MARCHING_BASE),
        ],
    )
    def test_a_success_carries_three_hexes_plus_one_per_icon(
        self, party, feat, successes, expected
    ) -> None:
        p = party()
        journey = trek(p)
        roll = march(p, journey, feat=feat, successes=successes)
        assert roll.succeeded
        assert marching_distance(roll, journey.season) == expected

    def test_a_failure_in_spring_or_summer_carries_two_hexes(self, party) -> None:
        p = party()
        journey = trek(p, season=Season.SUMMER)
        roll = march(p, journey, feat=1, successes=(3, 3))
        assert not roll.succeeded
        assert marching_distance(roll, Season.SPRING) == FAILED_MARCH_WARM
        assert marching_distance(roll, Season.SUMMER) == FAILED_MARCH_WARM

    def test_a_failure_in_autumn_or_winter_carries_one(self, party) -> None:
        # 19.4 names Winter and Summer specifically.
        p = party()
        journey = trek(p, season=Season.WINTER)
        roll = march(p, journey, feat=1, successes=(3, 3))
        assert marching_distance(roll, Season.AUTUMN) == FAILED_MARCH_COLD
        assert marching_distance(roll, Season.WINTER) == FAILED_MARCH_COLD

    def test_a_failure_ignores_its_icons(self, party) -> None:
        p = party()
        journey = trek(p)
        roll = march(p, journey, feat=1, successes=(6, 6))
        assert roll.icons == 2
        assert marching_distance(roll, Season.WINTER) == FAILED_MARCH_COLD


class TestTheMarchingTest:
    def test_only_the_guide_makes_it(self, party) -> None:
        p = party()
        journey = trek(p)
        rng = ScriptedRandomness(feats=[9], successes=[6, 6])
        with pytest.raises(RuleViolation, match="is not this journey's Guide"):
            roll_marching_test(journey, p["scout"], rng, ctx=p.ctx)

    def test_a_finished_journey_takes_no_further_test(self, party) -> None:
        p = party()
        journey = trek(p)
        journey.finished = True
        rng = ScriptedRandomness(feats=[9])
        with pytest.raises(StateError, match="has finished"):
            roll_marching_test(journey, p["guide"], rng, ctx=p.ctx)

    def test_the_roll_is_a_marching_test_and_carries_the_season(self, party) -> None:
        p = party()
        journey = trek(p, season=Season.WINTER)
        roll = march(p, journey, feat=9, successes=(6, 6))
        assert roll.request.purpose is RollPurpose.MARCHING_TEST
        assert roll.request.ability == "travel"

    def test_a_weary_guide_rolls_weary(self, party) -> None:
        p = party()
        p["guide"].conditions.weary = True
        journey = trek(p)
        roll = march(p, journey, feat=9, successes=(6, 6))
        assert roll.request.weary

    def test_a_journey_with_no_guide_cannot_name_one(self, party) -> None:
        p = party()
        journey = trek(p)
        journey.roles = {HeroId("scout"): {JourneyRole.SCOUT}}
        with pytest.raises(StateError, match="no Guide"):
            _ = journey.guide


class TestStepping:
    def test_arrival_happens_when_the_distance_covers_what_remains(self, party) -> None:
        # 19.4: path of 10, position 6, distance 4 -> arrived, no event.
        p = party()
        journey = trek(p, count=10)
        journey.position = 6
        roll = march(p, journey, feat=9, successes=(6, 3))  # 3 + 1 icon = 4
        step = resolve_step(journey, roll)
        assert (step.distance, step.arrived) == (4, True)
        assert step.event_hex is None, "10.3.2: on arrival no event occurs"
        assert step.events_due == 0

        apply_step(journey, step)
        assert journey.position == 10
        assert journey.finished

    def test_overshooting_the_destination_still_just_arrives(self, party) -> None:
        p = party()
        journey = trek(p, count=10)
        journey.position = 8
        roll = march(p, journey, feat=9, successes=(6, 6))  # 5, far past the two remaining
        step = resolve_step(journey, roll)
        assert step.arrived and step.position == 10

    def test_a_short_distance_puts_the_event_on_the_hex_reached(self, party) -> None:
        # 19.4: distance 3 -> position 9, event at path[8].
        p = party()
        journey = trek(p, count=10)
        journey.position = 6
        roll = march(p, journey, feat=9, successes=(3, 3))  # 3 + 0 icons
        step = resolve_step(journey, roll)
        assert (step.distance, step.position, step.arrived) == (3, 9, False)
        assert step.event_hex is journey.path[8]
        assert step.events_due == 1

        events = apply_step(journey, step)
        assert journey.position == 9
        assert not journey.finished
        assert [e.kind for e in events] == [EventKind.MARCHING_TEST_RESOLVED]
        assert events[0].payload["event_hex"] == 8

    def test_arriving_exactly_takes_covering_every_hex(self, party) -> None:
        p = party()
        journey = trek(p, count=5)
        journey.position = 2
        roll = march(p, journey, feat=9, successes=(3, 3))  # 3, exactly what remains
        step = resolve_step(journey, roll)
        assert step.arrived and step.position == 5

    def test_a_finished_journey_has_no_next_step(self, party) -> None:
        p = party()
        journey = trek(p)
        roll = march(p, journey, feat=9, successes=(3, 3))
        journey.finished = True
        with pytest.raises(StateError, match="no further step"):
            resolve_step(journey, roll)


class TestPerilousAreas:
    @staticmethod
    def path_with_marsh(*, at: tuple[int, ...], peril: int = 2) -> tuple[Hex, ...]:
        marsh = PerilousArea(id="marsh", name="The Marsh", peril=peril)
        return tuple(
            Hex(
                index=i,
                terrain=TerrainKind.EASY,
                region=RegionType.WILD,
                perilous_area=marsh if i in at else None,
            )
            for i in range(10)
        )

    def test_the_company_stops_at_the_first_hex_of_the_area(self, party) -> None:
        p = party()
        journey = trek(p, path=self.path_with_marsh(at=(3, 4, 5)))
        roll = march(p, journey, feat=9, successes=(6, 6))  # distance 5, across hex 3
        step = resolve_step(journey, roll)
        assert step.distance == 5, "the roll carried five hexes"
        assert step.position == 4, "but the march stopped on path[3], the marsh's first hex"
        assert step.entered_area is not None and step.entered_area.id == "marsh"
        assert step.events_due == 2, "one Event per point of Peril"

        events = apply_step(journey, step)
        assert journey.position == 4
        assert [e.kind for e in events] == [
            EventKind.MARCHING_TEST_RESOLVED,
            EventKind.PERILOUS_AREA_ENTERED,
        ]
        assert events[1].payload == {
            "area": "marsh",
            "name": "The Marsh",
            "peril": 2,
            "hex": 3,
        }

    def test_an_area_stops_the_company_even_within_reach_of_the_destination(self, party) -> None:
        # 10.3.3: the Company stops "as soon as it enters", which is ahead of arriving.
        p = party()
        journey = trek(p, path=self.path_with_marsh(at=(8, 9)))
        journey.position = 7
        roll = march(p, journey, feat=9, successes=(6, 6))  # 5, past the end
        step = resolve_step(journey, roll)
        assert not step.arrived
        assert step.position == 9, "standing on path[8], the marsh's first hex"
        assert step.entered_area is not None

    def test_leaving_resumes_from_the_first_hex_beyond_the_boundary(self, party) -> None:
        p = party()
        journey = trek(p, path=self.path_with_marsh(at=(3, 4, 5)))
        apply_step(journey, resolve_step(journey, march(p, journey, feat=9, successes=(6, 6))))

        events = exit_perilous_area(journey)
        assert journey.position == 6, "standing on path[5], so path[6] is the next hex"
        assert not journey.finished
        assert [e.kind for e in events] == [EventKind.PERILOUS_AREA_CLEARED]
        assert events[0].payload["remaining"] == 4

    def test_an_area_running_to_the_destination_ends_the_journey_on_leaving(self, party) -> None:
        p = party()
        journey = trek(p, path=self.path_with_marsh(at=(8, 9)))
        journey.position = 7
        apply_step(journey, resolve_step(journey, march(p, journey, feat=9, successes=(3, 3))))
        assert journey.position == 9, "the marsh stopped them one hex short of arriving"

        exit_perilous_area(journey)
        assert journey.position == 10
        assert journey.finished

    def test_two_separate_areas_are_left_one_at_a_time(self, party) -> None:
        marsh = PerilousArea(id="marsh", name="Marsh", peril=1)
        pass_ = PerilousArea(id="pass", name="Pass", peril=1)
        path = tuple(
            Hex(
                index=i,
                terrain=TerrainKind.EASY,
                region=RegionType.WILD,
                perilous_area={2: marsh, 3: pass_}.get(i),
            )
            for i in range(8)
        )
        p = party()
        journey = trek(p, path=path)
        apply_step(journey, resolve_step(journey, march(p, journey, feat=9, successes=(3, 3))))
        assert journey.position == 3, "stopped on the marsh at path[2]"

        exit_perilous_area(journey)
        assert journey.position == 3, "the pass at path[3] is a different area, not more marsh"

    def test_leaving_an_area_the_company_is_not_in_is_a_caller_bug(self, party) -> None:
        p = party()
        journey = trek(p)
        with pytest.raises(StateError, match="not inside a Perilous Area"):
            exit_perilous_area(journey)

    def test_leaving_before_setting_out_is_also_a_caller_bug(self, party) -> None:
        p = party()
        journey = trek(p, path=self.path_with_marsh(at=(0,)))
        with pytest.raises(StateError, match="not inside a Perilous Area"):
            exit_perilous_area(journey)


class TestInterruptionAndNarration:
    def test_an_interruption_finishes_the_journey_where_it_stands(self, party) -> None:
        p = party()
        journey = trek(p, count=10)
        journey.position = 4
        events = journey.interrupt("drawn into the siege of the Old Ford")
        assert journey.finished
        assert [e.kind for e in events] == [EventKind.JOURNEY_INTERRUPTED]
        assert events[0].payload["remaining"] == 6
        assert events[0].payload["reason"].startswith("drawn into")

    def test_a_finished_journey_cannot_be_interrupted(self, party) -> None:
        p = party()
        journey = trek(p)
        journey.finished = True
        with pytest.raises(StateError, match="already finished"):
            journey.interrupt("too late")

    def test_narrating_skips_the_whole_trip_without_a_roll(self, party) -> None:
        # 10.8: not every trip deserves these rules.
        p = party()
        journey = trek(p, count=6, path=hexes(6, terrain=TerrainKind.HARD))
        events = journey.narrate_only()
        assert journey.position == 6 and journey.finished
        assert journey.events_resolved == []
        assert [e.kind for e in events] == [EventKind.JOURNEY_ENDED]
        assert events[0].payload == {
            "origin": "Origin",
            "destination": "Destination",
            "days": 12,
            "narrated": True,
        }

    def test_a_finished_journey_cannot_be_narrated(self, party) -> None:
        p = party()
        journey = trek(p)
        journey.finished = True
        with pytest.raises(StateError, match="already finished"):
            journey.narrate_only()


class TestDays:
    def test_one_day_per_hex_plus_one_per_hard_hex(self, party) -> None:
        p = party()
        path = (*hexes(4), *(h for h in hexes(2, terrain=TerrainKind.HARD)))
        numbered = tuple(
            Hex(index=i, terrain=h.terrain, region=h.region) for i, h in enumerate(path)
        )
        assert journey_days(trek(p, path=numbered)) == 6 + 2

    def test_a_mounted_company_halves_the_total_rounding_up(self, party) -> None:
        p = party()
        journey = trek(p, count=5, mounted=True)
        assert journey_days(journey) == 3, "ceil(5 / 2)"

    def test_a_forced_march_counts_one_day_per_two_hexes(self, party) -> None:
        p = party()
        path = tuple(
            Hex(
                index=i,
                terrain=TerrainKind.HARD if i < 2 else TerrainKind.EASY,
                region=RegionType.WILD,
            )
            for i in range(9)
        )
        assert journey_days(trek(p, path=path)) == 9 + 2
        assert journey_days(trek(p, path=path, forced_march=True)) == 5 + 2, "ceil(9/2) + hard"

    def test_mounted_and_forced_together_halve_the_forced_rate_and_warn(self, party) -> None:
        # 10.7's code block drops the halving; its prose says to apply it afterwards and to
        # flag the combination.
        p = party()
        journey, warnings = plan_journey(
            "A",
            "B",
            hexes(9),
            Season.SUMMER,
            p.roles,
            ctx=p.ctx,
            mounted=True,
            forced_march=True,
        )
        assert journey_days(journey) == 3, "ceil(ceil(9 / 2) / 2)"
        assert [w.code for w in warnings] == ["mounted_forced_march"]
        assert journey_day_warnings(journey)

    def test_day_adjustments_land_after_the_halving(self, party) -> None:
        p = party()
        journey = trek(p, count=5, mounted=True)
        journey.day_adjustments = 2
        assert journey_days(journey) == 3 + 2

    def test_the_total_never_goes_negative(self, party) -> None:
        p = party()
        journey = trek(p, count=2)
        journey.day_adjustments = -20
        assert journey_days(journey) == 0

    def test_forced_march_costs_one_fatigue_per_day(self, party) -> None:
        p = party()
        assert forced_march_fatigue(trek(p, count=8)) == 0
        forced = trek(p, count=8, forced_march=True)
        assert forced_march_fatigue(forced) == journey_days(forced) == 4


class TestEventDice:
    def test_hard_going_costs_a_die_and_a_road_grants_one(self) -> None:
        easy = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.WILD)
        hard = Hex(index=0, terrain=TerrainKind.HARD, region=RegionType.WILD)
        road = Hex(index=0, terrain=TerrainKind.EASY, region=RegionType.WILD, has_road=True)
        hard_road = Hex(index=0, terrain=TerrainKind.HARD, region=RegionType.WILD, has_road=True)
        assert event_dice(easy) == (0, 0)
        assert event_dice(hard) == (0, 1)
        assert event_dice(road) == (1, 0)
        assert event_dice(hard_road) == (1, 1), "they net out, they do not cancel at source"


class TestRegionPolicy:
    def test_the_three_regions_set_the_feat_die_policy(self) -> None:
        assert REGION_POLICY[RegionType.BORDER] is FeatDicePolicy.FAVOURED
        assert REGION_POLICY[RegionType.WILD] is FeatDicePolicy.NORMAL
        assert REGION_POLICY[RegionType.DARK] is FeatDicePolicy.ILL_FAVOURED


class TestSupportPlumbing:
    def test_support_needs_the_loremasters_approval_by_default(self) -> None:
        # The record defaults to unapproved so a caller that forgets is refused.
        assert not Support(rating=3).approved
        assert Support(rating=3, approved=True).approved

    def test_a_supported_marching_test_gains_a_die(self, party) -> None:
        p = party()
        journey = trek(p)
        rng = ScriptedRandomness(feats=[9], successes=[6, 6, 3])
        roll = roll_marching_test(
            journey,
            p["guide"],
            rng,
            ctx=p.ctx,
            support=Support(rating=2, approved=True),
        )
        assert rng.exhausted
        assert roll.request.dice_count == 3


class TestHeroesInRole:
    def test_they_come_back_in_a_fixed_order(self, party) -> None:
        p = party()
        p.add("zealot")
        p.add("archer")
        roles = dict(p.roles)
        roles[HeroId("zealot")] = {JourneyRole.SCOUT}
        roles[HeroId("archer")] = {JourneyRole.SCOUT}
        journey = trek(p)
        journey.roles = roles
        assert journey.heroes_in_role(JourneyRole.SCOUT) == ("archer", "scout", "zealot")
        assert journey.heroes_in_role(JourneyRole.GUIDE) == ("guide",)


class TestMiserableGuide:
    def test_the_eye_fails_a_miserable_guides_march(self, party) -> None:
        p = party()
        hero = p["guide"]
        hero.shadow = hero.hope
        journey = trek(p)
        from tor.rules.resources import recompute_conditions

        recompute_conditions(hero, ctx=p.ctx)
        assert hero.conditions.miserable
        roll = march(p, journey, feat=FeatFace.EYE, successes=(6, 6))
        assert not roll.succeeded
        assert marching_distance(roll, Season.SUMMER) == FAILED_MARCH_WARM


def test_build_hero_is_the_shared_builder() -> None:
    # Guards the conftest contract the Party fixture leans on.
    assert build_hero("x").id == "x"
