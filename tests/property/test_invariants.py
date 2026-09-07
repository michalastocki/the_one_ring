"""Property tests (spec 19.5).

The invariants that must hold across arbitrary but valid inputs, rather than the handful a
worked example happens to cover.
"""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from tor.dice import FEAT_FACES, FeatValue, ScriptedRandomness, SuccessDie
from tor.effects.bus import EffectBus, EffectKind, EffectSource
from tor.effects.hooks import Hook, HookContext
from tor.effects.library import build_effect
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS
from tor.model.attributes import AttributeSet
from tor.model.conditions import ResourcePool, StandardOfLiving, standard_of_living_for
from tor.model.derive import DerivedStat, Modifier
from tor.model.gear import ArmourCategory, ArmourInstance, ArmourType, Gear
from tor.model.hero import Hero
from tor.model.ids import CallingId, CultureId, EffectId, HeroId, ItemId
from tor.rolls import Degree, Outcome, RollRequest, resolve
from tor.rules.contest import ContestOutcome, ResistanceContest, evaluate
from tor.rules.context import RulesContext
from tor.rules.resources import (
    ChangeSource,
    change_endurance,
    change_fatigue,
    change_hope,
    change_treasure,
    recompute_conditions,
    recompute_load,
)

LADDER: list[tuple[StandardOfLiving, int | None]] = [
    (StandardOfLiving.POOR, None),
    (StandardOfLiving.FRUGAL, 0),
    (StandardOfLiving.COMMON, 30),
    (StandardOfLiving.PROSPEROUS, 90),
    (StandardOfLiving.RICH, 180),
    (StandardOfLiving.VERY_RICH, 300),
]

ratings = st.integers(min_value=0, max_value=6)
modifiers = st.integers(min_value=-10, max_value=10)


@st.composite
def requests(draw: st.DrawFn) -> RollRequest:
    """An arbitrary well-formed RollRequest."""
    return RollRequest(
        rating=draw(ratings),
        target_number=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=30))),
        favoured_sources=tuple(draw(st.lists(st.just("f"), max_size=3))),
        ill_favoured_sources=tuple(draw(st.lists(st.just("i"), max_size=3))),
        bonus_dice=draw(modifiers),
        penalty_dice=draw(modifiers),
        weary=draw(st.booleans()),
        eye_is_auto_failure=draw(st.booleans()),
        icon_inverted=draw(st.booleans()),
        magical_success=draw(st.booleans()),
    )


def roller_for(request: RollRequest) -> ScriptedRandomness:
    """Enough dice for any policy, so resolve() never runs dry."""
    return ScriptedRandomness(
        feats=list(FEAT_FACES) * 2,
        successes=[1, 2, 3, 4, 5, 6] * (request.dice_count // 6 + 2),
    )


class TestRollPipeline:
    @given(rating=ratings, bonus=modifiers, penalty=modifiers)
    def test_dice_count_is_never_negative(self, rating: int, bonus: int, penalty: int) -> None:
        request = RollRequest(rating=rating, bonus_dice=bonus, penalty_dice=penalty)
        assert request.dice_count >= 0

    @given(request=requests())
    def test_resolve_never_raises_for_a_well_formed_request(self, request: RollRequest) -> None:
        resolve(request, roller_for(request))

    @given(request=requests())
    def test_degree_and_outcome_never_disagree(self, request: RollRequest) -> None:
        # A result that succeeded must carry a degree, and one that did not must not.
        result = resolve(request, roller_for(request))
        if result.outcome is Outcome.SUCCESS:
            assert result.degree is not Degree.NONE
        else:
            assert result.degree is Degree.NONE

    @given(request=requests())
    def test_the_kept_die_is_one_of_those_rolled(self, request: RollRequest) -> None:
        result = resolve(request, roller_for(request))
        assert result.kept_feat in result.feat_dice
        assert len(result.feat_dice) in (1, 2)

    @given(request=requests())
    def test_exactly_the_requested_success_dice_are_rolled(self, request: RollRequest) -> None:
        result = resolve(request, roller_for(request))
        assert len(result.success_dice) == request.dice_count

    @given(request=requests())
    def test_icons_never_exceed_the_dice_rolled(self, request: RollRequest) -> None:
        result = resolve(request, roller_for(request))
        assert 0 <= result.icons <= len(result.success_dice)

    @given(request=requests(), base=st.integers(min_value=0, max_value=5))
    def test_magnitude_is_monotonic_in_icons_for_a_successful_roll(
        self, request: RollRequest, base: int
    ) -> None:
        result = resolve(request, roller_for(request))
        assume(result.succeeded)
        assert result.magnitude(base) == base + result.icons
        assert result.magnitude(base) >= result.magnitude(base) - 1

    @given(request=requests())
    def test_a_failed_roll_yields_no_magnitude_and_no_tier(self, request: RollRequest) -> None:
        result = resolve(request, roller_for(request))
        assume(not result.succeeded)
        assert result.magnitude() == 0
        assert result.tier() == 0

    @given(request=requests())
    def test_tier_is_between_zero_and_three(self, request: RollRequest) -> None:
        assert 0 <= resolve(request, roller_for(request)).tier() <= 3

    @given(request=requests())
    def test_resolution_is_deterministic_for_the_same_scripted_dice(
        self, request: RollRequest
    ) -> None:
        first = resolve(request, roller_for(request))
        second = resolve(request, roller_for(request))
        assert first == second


class TestSuccessDice:
    @given(face=st.integers(min_value=1, max_value=6))
    def test_only_the_six_is_solid_and_iconed(self, face: int) -> None:
        die = SuccessDie(face)
        assert die.has_icon == (face == 6)
        assert die.is_outlined == (face <= 3)
        # An iconed die is never outlined, which is why Weary cannot suppress an icon.
        assert not (die.has_icon and die.is_outlined)


class TestResourcePool:
    @given(
        maximum=st.integers(min_value=0, max_value=30),
        spends=st.lists(st.integers(min_value=0, max_value=5), max_size=10),
        restores=st.lists(st.integers(min_value=0, max_value=5), max_size=10),
    )
    def test_a_pool_stays_within_its_bounds(
        self, maximum: int, spends: list[int], restores: list[int]
    ) -> None:
        pool = ResourcePool(current=maximum, maximum=maximum)
        for amount in spends:
            if amount <= pool.current:
                pool.spend(amount)
            assert 0 <= pool.current <= pool.maximum
        for amount in restores:
            pool.restore(amount)
            assert 0 <= pool.current <= pool.maximum

    @given(maximum=st.integers(min_value=0, max_value=20), raised=st.integers(0, 10))
    def test_raising_a_maximum_never_lowers_the_current_value(
        self, maximum: int, raised: int
    ) -> None:
        pool = ResourcePool(current=maximum, maximum=maximum)
        before = pool.current
        pool.set_maximum(maximum + raised)
        assert pool.current >= before


class TestDerivedStats:
    @given(base=modifiers, deltas=st.lists(modifiers, max_size=6))
    def test_recomputing_is_idempotent(self, base: int, deltas: list[int]) -> None:
        stat = DerivedStat(
            base=base,
            modifiers=tuple(Modifier(EffectId(f"e{i}"), delta=d) for i, d in enumerate(deltas)),
        )
        assert stat.value == stat.value == base + sum(deltas)

    @given(base=modifiers, deltas=st.lists(modifiers, min_size=1, max_size=5))
    def test_registering_then_unregistering_restores_the_prior_value(
        self, base: int, deltas: list[int]
    ) -> None:
        """19.5 — the property that catches every effect-residue bug.

        A smashed shield, a dropped helm, an expired Strengthen Fellowship bonus: each
        must leave the derived stat exactly as it was before.
        """
        bus = EffectBus()
        ctx = HookContext(hook=Hook.MODIFY_PARRY)
        before = bus.apply_numeric(Hook.MODIFY_PARRY, ctx, base=base).value

        source = EffectSource.item("shield#1")
        for index, delta in enumerate(deltas):
            bus.register(
                build_effect(
                    EffectId(f"quality_{index}"),
                    EffectKind.REWARD,
                    "numeric_modifier",
                    {"hook": "MODIFY_PARRY", "delta": delta},
                ),
                source,
            )
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx, base=base).value == before + sum(deltas)

        bus.unregister_source(source)
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx, base=base).value == before


class TestStandardOfLiving:
    @given(treasure=st.integers(min_value=0, max_value=1000))
    def test_the_tier_never_falls_as_treasure_rises(self, treasure: int) -> None:
        lower = standard_of_living_for(treasure, LADDER)
        higher = standard_of_living_for(treasure + 1, LADDER)
        assert higher >= lower

    @given(treasure=st.integers(min_value=0, max_value=1000))
    def test_the_tier_is_always_a_defined_one(self, treasure: int) -> None:
        assert standard_of_living_for(treasure, LADDER) in set(StandardOfLiving)


MAIL = ItemId("example_mail")


class OneArmour:
    """A `GearIndex` holding a single suit, so Load has something to sum."""

    def __init__(self, load: int) -> None:
        self._armour = ArmourType(
            id=MAIL, name="Example Mail", protection=3, load=load, category=ArmourCategory.MAIL
        )

    def weapon(self, item_id: str) -> object:  # pragma: no cover - never reached
        raise KeyError(item_id)

    def armour_type(self, item_id: str) -> ArmourType:
        return self._armour

    def shield_type(self, item_id: str) -> object:  # pragma: no cover - never reached
        raise KeyError(item_id)


def hero_with(*, endurance: int, hope: int, shadow: int, fatigue: int) -> Hero:
    return Hero(
        id=HeroId("h"),
        name="Testfolk",
        culture=CultureId("example_folk"),
        calling=CallingId("example_calling"),
        age=30,
        attributes=AttributeSet(strength=5, heart=6, wits=3),
        skills=dict.fromkeys(SKILLS, 1),
        proficiencies=dict.fromkeys(COMBAT_PROFICIENCIES, 0),
        max_endurance=DerivedStat(base=30),
        max_hope=DerivedStat(base=20),
        parry=DerivedStat(base=15),
        endurance=endurance,
        hope=hope,
        shadow=shadow,
        fatigue=fatigue,
        gear=Gear(armour=ArmourInstance(type_id=MAIL)),
    )


class TestResourceInvariants:
    """19.5 — the conditions are a pure function of the numbers, after every change."""

    @given(
        endurance=st.integers(min_value=0, max_value=30),
        hope=st.integers(min_value=0, max_value=20),
        shadow=st.integers(min_value=0, max_value=20),
        fatigue=st.integers(min_value=0, max_value=10),
        armour_load=st.integers(min_value=0, max_value=20),
    )
    def test_weary_and_miserable_are_exactly_their_thresholds(
        self, endurance: int, hope: int, shadow: int, fatigue: int, armour_load: int
    ) -> None:
        assume(shadow <= 20)
        hero = hero_with(endurance=endurance, hope=hope, shadow=shadow, fatigue=fatigue)
        ctx = RulesContext(gear=OneArmour(armour_load), buses={hero.id: EffectBus()})

        recompute_conditions(hero, ctx=ctx)
        load = recompute_load(hero, ctx=ctx)
        assert hero.conditions.weary == (hero.endurance <= load)
        assert hero.conditions.miserable == (hero.shadow >= hero.hope)

    @given(
        deltas=st.lists(
            st.tuples(
                st.sampled_from(["endurance", "hope", "fatigue", "treasure"]),
                st.integers(min_value=-8, max_value=8),
            ),
            max_size=12,
        )
    )
    def test_invariants_hold_after_any_sequence_of_legal_changes(
        self, deltas: list[tuple[str, int]]
    ) -> None:
        hero = hero_with(endurance=30, hope=20, shadow=0, fatigue=0)
        ctx = RulesContext(gear=OneArmour(4), buses={hero.id: EffectBus()})

        for what, delta in deltas:
            if what == "endurance":
                change_endurance(hero, delta, ChangeSource.COMBAT, ctx=ctx)
            elif what == "hope":
                # A spend at zero Hope is illegal by 07.3, so only offer what is legal.
                if delta < 0 and (hero.hope == 0 or -delta > hero.hope):
                    continue
                change_hope(hero, delta, ChangeSource.EFFECT, ctx=ctx)
            elif what == "fatigue":
                change_fatigue(hero, delta, ChangeSource.JOURNEY, ctx=ctx)
            else:
                if delta < 0 and -delta > hero.treasure.carried:
                    continue
                change_treasure(hero, delta, ChangeSource.TREASURE_FOUND, ctx=ctx)

        hero.validate()
        assert 0 <= hero.endurance <= hero.max_endurance.value
        assert 0 <= hero.hope <= hero.max_hope.value
        assert 0 <= hero.shadow <= hero.max_hope.value
        assert hero.fatigue >= 0
        assert hero.treasure.carried >= 0

    @given(armour_load=st.integers(min_value=0, max_value=20))
    def test_recomputing_load_is_idempotent(self, armour_load: int) -> None:
        hero = hero_with(endurance=20, hope=10, shadow=0, fatigue=2)
        ctx = RulesContext(gear=OneArmour(armour_load), buses={hero.id: EffectBus()})
        assert recompute_load(hero, ctx=ctx) == recompute_load(hero, ctx=ctx)

    @given(delta=modifiers)
    def test_registering_then_unregistering_a_load_effect_restores_it_exactly(
        self, delta: int
    ) -> None:
        # 19.5 calls this the property that catches every effect-residue bug — a smashed
        # shield, a dropped helm, an expired bonus.
        hero = hero_with(endurance=20, hope=10, shadow=0, fatigue=0)
        bus = EffectBus()
        ctx = RulesContext(gear=OneArmour(9), buses={hero.id: bus})
        before = recompute_load(hero, ctx=ctx)

        source = EffectSource.item("mail#1")
        bus.register(
            build_effect(
                EffectId("some_reward"),
                EffectKind.REWARD,
                "numeric_modifier",
                {"hook": "MODIFY_ITEM_LOAD", "delta": delta},
            ),
            source,
        )
        recompute_load(hero, ctx=ctx)
        bus.unregister_source(source)
        assert recompute_load(hero, ctx=ctx) == before


class TestContestTermination:
    """19.5 — a ResistanceContest terminates."""

    @given(
        resistance=st.integers(min_value=1, max_value=9),
        allowed=st.integers(min_value=1, max_value=12),
        faces=st.lists(st.sampled_from(list(FEAT_FACES)), min_size=12, max_size=12),
    )
    def test_it_finishes_within_its_attempt_budget(
        self, resistance: int, allowed: int, faces: list[FeatValue]
    ) -> None:
        # 19.5 states the bound as `attempts_allowed + 1`, but `record` raises StateError
        # once the contest is finished (01.6), so the reachable bound is attempts_allowed.
        contest = ResistanceContest(resistance=resistance, attempts_allowed=allowed)
        for face in faces:
            if contest.finished:
                break
            rng = ScriptedRandomness(feats=[face], successes=[4])
            contest.record(resolve(RollRequest(rating=1, target_number=14), rng))

        assert contest.finished
        assert contest.attempts_used <= allowed

    @given(
        resistance=st.integers(min_value=1, max_value=9),
        allowed=st.one_of(st.none(), st.integers(min_value=1, max_value=6)),
    )
    def test_evaluate_always_returns_a_defined_outcome(
        self, resistance: int, allowed: int | None
    ) -> None:
        contest = ResistanceContest(resistance=resistance, attempts_allowed=allowed)
        contest.abort("disaster")
        assert evaluate(contest) in set(ContestOutcome)
