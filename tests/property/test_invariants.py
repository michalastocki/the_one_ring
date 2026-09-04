"""Property tests (spec 19.5).

The invariants that must hold across arbitrary but valid inputs, rather than the handful a
worked example happens to cover.
"""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from tor.dice import FEAT_FACES, ScriptedRandomness, SuccessDie
from tor.effects.bus import EffectBus, EffectKind, EffectSource
from tor.effects.hooks import Hook, HookContext
from tor.effects.library import build_effect
from tor.model.conditions import ResourcePool, StandardOfLiving, standard_of_living_for
from tor.model.derive import DerivedStat, Modifier
from tor.model.ids import EffectId
from tor.rolls import Degree, Outcome, RollRequest, resolve

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
