"""`tor.rules.shadow` — gaining, resisting, shedding and succumbing to Shadow (spec 11).

Heroes are built by hand rather than loaded, the way the other shared-leaf tests do it: a
Shadow Test needs a rank, an Attribute and a bus, and nothing else. The three effects that
reach these hooks are built with `build_effect` from the same declarations
`content/example/` carries, so what is asserted is the mechanism, never a value from the
book (19.9).

Every test that rolls asserts `rng.exhausted` (19.2). The functions that do not roll —
`harden_will`, `heal_scar`, `remove_shadow`, `bout_of_madness` — script no dice at all.
"""

from __future__ import annotations

import pytest

from tor.dice import FeatFace, FeatValue, ScriptedRandomness
from tor.effects.bus import EffectBus, EffectKind, EffectSource
from tor.effects.hooks import Hook, HookContext
from tor.effects.library import build_effect
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.abilities import SKILLS, VALOUR, WISDOM
from tor.model.attributes import AttributeSet
from tor.model.company import Company
from tor.model.conditions import ConditionSet
from tor.model.derive import DerivedStat
from tor.model.gear import Gear
from tor.model.hero import Hero
from tor.model.ids import AbilityId, CallingId, CultureId, EffectId, HeroId
from tor.rolls import RollPurpose, RollRequest, build_request, resolve
from tor.rules.context import RulesContext
from tor.rules.shadow import (
    OVERBURDENED_ID,
    RESISTED_WITH,
    MisdeedCost,
    RemovalReason,
    ShadowSource,
    ShadowTestInput,
    apply_shadow_test,
    bout_of_madness,
    check_succumb,
    gain_shadow,
    graded_dread,
    harden_will,
    harm_to_focus,
    heal_scar,
    preview_misdeed,
    register_shadow_conditions,
    remove_shadow,
    resisting_ability,
    resolve_shadow_test,
    shadow_path_flaw,
)
from tor.tables import DieKind, LookupTable, TableRow

PATH: tuple[EffectId, ...] = tuple(
    EffectId(f) for f in ("idle", "forgetful", "uncaring", "cowardly")
)

#: 11.4.4's grades, as content supplies them. Invented figures, not the book's.
MISDEEDS: LookupTable[object] = LookupTable(
    id="misdeeds",
    die=DieKind.SUCCESS,
    rows=(
        TableRow(1, {"points": 1, "scar": False}),
        TableRow(2, {"points": 2, "scar": False}),
        TableRow((3, 4), {"points": 3, "scar": False}),
        TableRow(5, {"points": 4, "scar": False}),
        TableRow(6, {"points": 4, "scar": True}),
    ),
)

DREAD: LookupTable[object] = LookupTable(
    id="sources_of_dread",
    die=DieKind.SUCCESS,
    rows=(
        TableRow((1, 2), {"points": 1}),
        TableRow((3, 4), {"points": 2}),
        TableRow(5, {"points": 3}),
        TableRow(6, {"points": 4}),
    ),
)


class NoGear:
    """The `GearIndex` a Shadow-only hero needs: none of it, because Load never comes up."""

    def weapon(self, item_id):  # pragma: no cover - a hero with no gear asks for none
        raise AssertionError(item_id)

    armour_type = weapon
    shield_type = weapon


def hero_with(
    *,
    shadow: int = 0,
    scars: int = 0,
    max_hope: int = 14,
    hope: int | None = None,
    valour: int = 2,
    wisdom: int = 3,
    flaws: int = 0,
    step: int = 0,
    hero_id: str = "h",
) -> Hero:
    hero = Hero(
        id=HeroId(hero_id),
        name=hero_id,
        culture=CultureId("example_folk"),
        calling=CallingId("example_calling"),
        age=30,
        attributes=AttributeSet(strength=4, heart=6, wits=5),
        valour=valour,
        wisdom=wisdom,
        max_endurance=DerivedStat(base=24),
        max_hope=DerivedStat(base=max_hope),
        parry=DerivedStat(base=16),
        endurance=24,
        hope=max_hope if hope is None else hope,
        shadow=shadow,
        shadow_scars=scars,
        conditions=ConditionSet(),
        # A flat grid, so an ordinary roll has something to ask for; nothing here depends
        # on the values.
        skills=dict.fromkeys(SKILLS, 1),
        shadow_path=EffectId("example_path"),
        shadow_path_step=step,
        flaws=[PATH[i] for i in range(flaws)],
        gear=Gear(),
    )
    hero.conditions.miserable = hero.shadow >= hero.hope
    return hero


def context(hero: Hero, bus: EffectBus | None = None, **extra) -> RulesContext:
    return RulesContext.single(hero.id, bus=bus or EffectBus(), gear=NoGear(), **extra)


def pair(a: Hero, b: Hero) -> tuple[RulesContext, EffectBus, EffectBus]:
    bus_a, bus_b = EffectBus(), EffectBus()
    return RulesContext(gear=NoGear(), buses={a.id: bus_a, b.id: bus_b}), bus_a, bus_b


def effect(effect_id: str, kind: EffectKind, factory: str, **params):
    return build_effect(EffectId(effect_id), kind, factory, params)


# -- the source decides how it is resisted ---------------------------------------------


class TestSources:
    def test_the_resisting_ability_is_a_property_of_the_source(self) -> None:
        # 11.1: encode the mapping once; no caller supplies it.
        assert resisting_ability(ShadowSource.DREAD) == VALOUR
        assert resisting_ability(ShadowSource.SORCERY) == WISDOM
        assert resisting_ability(ShadowSource.GREED) == WISDOM

    def test_every_source_is_mapped(self) -> None:
        assert set(RESISTED_WITH) == set(ShadowSource)

    def test_the_unresistible_sources_map_to_nothing(self) -> None:
        # 11.3: Misdeeds and harm to your Fellowship Focus permit no test at all.
        assert resisting_ability(ShadowSource.MISDEED) is None
        assert resisting_ability(ShadowSource.FOCUS) is None
        assert resisting_ability(ShadowSource.OTHER) is None


# -- 11.3, the test itself --------------------------------------------------------------


class TestShadowTest:
    def test_a_passed_test_reduces_by_one_plus_one_per_icon(self) -> None:
        # Pattern P3, which RollResult.magnitude owns.
        rng = ScriptedRandomness(feats=[10], successes=[6, 6])
        roll = resolve(RollRequest(rating=2, target_number=14), rng)
        assert rng.exhausted
        assert apply_shadow_test(4, roll) == 1  # 4 - (1 + 2 icons)

    def test_a_failed_test_reduces_nothing(self) -> None:
        rng = ScriptedRandomness(feats=[1], successes=[6, 6])
        roll = resolve(RollRequest(rating=2, target_number=14), rng)
        assert rng.exhausted
        assert roll.icons == 2, "icons on a failed roll still do not reduce anything"
        assert apply_shadow_test(4, roll) == 4

    def test_a_test_never_reduces_below_zero(self) -> None:
        rng = ScriptedRandomness(feats=[10], successes=[6, 6, 6])
        roll = resolve(RollRequest(rating=3, target_number=14), rng)
        assert rng.exhausted
        assert apply_shadow_test(1, roll) == 0

    def test_dread_rolls_valour_against_the_heart_tn(self) -> None:
        hero = hero_with()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        roll = resolve_shadow_test(hero, ShadowSource.DREAD, rng, ctx=context(hero))
        assert rng.exhausted  # one Feat die plus VALOUR 2 Success dice
        assert roll.request.ability == VALOUR
        assert roll.request.target_number == 20 - hero.attributes.heart
        assert roll.request.rating == hero.valour

    def test_sorcery_rolls_wisdom_against_the_wits_tn(self) -> None:
        hero = hero_with()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4, 4])
        roll = resolve_shadow_test(hero, ShadowSource.SORCERY, rng, ctx=context(hero))
        assert rng.exhausted
        assert roll.request.ability == WISDOM
        assert roll.request.target_number == 20 - hero.attributes.wits
        assert roll.request.rating == hero.wisdom

    def test_a_short_campaign_derives_the_tn_from_eighteen(self) -> None:
        # 02.3.8: a campaign-level setting, applied where the TN is computed.
        hero = hero_with()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        roll = resolve_shadow_test(
            hero, ShadowSource.DREAD, rng, ctx=context(hero, short_campaign=True)
        )
        assert rng.exhausted
        assert roll.request.target_number == 18 - hero.attributes.heart

    def test_weary_and_miserable_reach_the_test_like_any_other_roll(self) -> None:
        hero = hero_with(shadow=5, hope=4)
        hero.conditions.weary = True
        assert hero.conditions.miserable
        rng = ScriptedRandomness(feats=[FeatFace.EYE], successes=[6, 6])
        roll = resolve_shadow_test(hero, ShadowSource.DREAD, rng, ctx=context(hero))
        assert rng.exhausted
        assert roll.request.weary and roll.request.eye_is_auto_failure
        assert not roll.succeeded, "an eye is automatic failure for a Miserable hero (11.2)"

    def test_an_unresistible_source_refuses_a_test(self) -> None:
        hero = hero_with()
        with pytest.raises(RuleViolation, match="cannot be resisted"):
            resolve_shadow_test(hero, ShadowSource.FOCUS, ScriptedRandomness(), ctx=context(hero))

    def test_a_misdeed_met_with_reparations_permits_a_wisdom_test(self) -> None:
        # 11.4.4 rule 2 — the one exception, and it is spelled explicitly.
        hero = hero_with()
        rng = ScriptedRandomness(feats=[10], successes=[4, 4, 4])
        roll = resolve_shadow_test(
            hero,
            ShadowSource.MISDEED,
            rng,
            ctx=context(hero),
            test=ShadowTestInput(reparation_allowed=True),
        )
        assert rng.exhausted
        assert roll.request.ability == WISDOM

    def test_a_misdeed_without_reparations_is_refused_and_says_how(self) -> None:
        hero = hero_with()
        with pytest.raises(RuleViolation, match="reparation_allowed") as excinfo:
            resolve_shadow_test(hero, ShadowSource.MISDEED, ScriptedRandomness(), ctx=context(hero))
        assert excinfo.value.rule_reference == "shadow_test_not_permitted"


class TestShadowTestHook:
    """04.3.5 puts the effects that shape these tests on MODIFY_SHADOW_TEST."""

    def test_a_virtue_adds_dice_against_one_source_only(
        self,
    ) -> None:
        hero = hero_with()
        bus = EffectBus()
        bus.register(
            effect(
                "untameable_spirit",
                EffectKind.CULTURAL_VIRTUE,
                "bonus_dice",
                hook="MODIFY_SHADOW_TEST",
                source="sorcery",
                dice=1,
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        ctx = context(hero, bus)

        rng = ScriptedRandomness(feats=[10], successes=[4, 4, 4, 4])
        sorcery = resolve_shadow_test(hero, ShadowSource.SORCERY, rng, ctx=ctx)
        assert rng.exhausted, "WISDOM 3 plus the virtue's die"
        assert sorcery.request.bonus_dice == 1

        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        dread = resolve_shadow_test(hero, ShadowSource.DREAD, rng, ctx=ctx)
        assert rng.exhausted, "VALOUR 2, and the virtue does not apply to Dread"
        assert dread.request.bonus_dice == 0

    def test_a_patron_benefit_makes_the_test_favoured(self) -> None:
        hero = hero_with()
        bus = EffectBus()
        bus.register(
            effect(
                "patrons_counsel",
                EffectKind.PATRON_BENEFIT,
                "favour_rolls",
                hook="MODIFY_SHADOW_TEST",
            ),
            EffectSource.acquired("patron"),
        )
        rng = ScriptedRandomness(feats=[2, 9], successes=[4, 4])
        roll = resolve_shadow_test(hero, ShadowSource.DREAD, rng, ctx=context(hero, bus))
        assert rng.exhausted, "two Feat dice, because the test is Favoured"
        assert roll.request.favoured_sources == ("patrons_counsel",)

    def test_the_hook_does_not_reach_an_ordinary_roll(self) -> None:
        # The whole reason it is a hook of its own rather than a purpose string.
        hero = hero_with()
        bus = EffectBus()
        bus.register(
            effect(
                "patrons_counsel",
                EffectKind.PATRON_BENEFIT,
                "favour_rolls",
                hook="MODIFY_SHADOW_TEST",
            ),
            EffectSource.acquired("patron"),
        )
        request = build_request(
            hero, AbilityId("hunting"), bus=bus, target_number=14, purpose=RollPurpose.SKILL
        )
        assert request.favoured_sources == ()


# -- 11.1 / 11.2, gaining ----------------------------------------------------------------


class TestGainShadow:
    def test_an_unresisted_gain_lands_whole(self) -> None:
        hero = hero_with()
        outcome = gain_shadow(hero, 3, ShadowSource.MISDEED, ctx=context(hero))
        assert (outcome.gained, outcome.discarded) == (3, 0)
        assert hero.shadow == 3
        assert outcome.roll is None

    def test_a_resisted_gain_is_reduced_by_the_test(self) -> None:
        hero = hero_with()
        bus = EffectBus()
        rng = ScriptedRandomness(feats=[10], successes=[6, 4])
        outcome = gain_shadow(
            hero,
            3,
            ShadowSource.DREAD,
            ctx=context(hero, bus),
            test=ShadowTestInput(),
            rng=rng,
        )
        assert rng.exhausted
        assert outcome.gained == 1  # 3 - (1 + one icon)
        assert hero.shadow == 1
        assert outcome.roll is not None

    def test_a_test_needs_a_randomness(self) -> None:
        hero = hero_with()
        with pytest.raises(StateError, match="needs a Randomness"):
            gain_shadow(hero, 2, ShadowSource.DREAD, ctx=context(hero), test=ShadowTestInput())

    def test_supplying_a_test_for_an_unresistible_source_raises(self) -> None:
        # 11.3: silent ignoring hides caller bugs.
        hero = hero_with()
        with pytest.raises(RuleViolation, match="cannot be resisted"):
            gain_shadow(
                hero,
                2,
                ShadowSource.FOCUS,
                ctx=context(hero),
                test=ShadowTestInput(),
                rng=ScriptedRandomness(),
            )

    def test_the_hope_that_pushes_a_test_is_spent_before_the_dice(self) -> None:
        hero = hero_with(hope=5)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4, 4])
        outcome = gain_shadow(
            hero,
            2,
            ShadowSource.DREAD,
            ctx=context(hero),
            test=ShadowTestInput(spend_hope=True),
            rng=rng,
        )
        assert rng.exhausted, "VALOUR 2 plus the Hope die"
        assert hero.hope == 4
        assert EventKind.HOPE_CHANGED in {e.kind for e in outcome.events}

    def test_a_hero_at_zero_hope_cannot_push_and_never_rolls(self) -> None:
        hero = hero_with(hope=0, max_hope=14)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        with pytest.raises(RuleViolation, match="zero Hope"):
            gain_shadow(
                hero,
                2,
                ShadowSource.DREAD,
                ctx=context(hero),
                test=ShadowTestInput(spend_hope=True),
                rng=rng,
            )
        assert hero.shadow == 0
        assert rng.remaining == (1, 2), "the refusal came before any die was asked for"

    def test_a_negative_gain_is_a_caller_bug(self) -> None:
        hero = hero_with()
        with pytest.raises(StateError, match="use remove_shadow"):
            gain_shadow(hero, -1, ShadowSource.OTHER, ctx=context(hero))

    def test_points_beyond_maximum_hope_are_discarded_not_banked(self) -> None:
        # Invariant I5. The ceiling is maximum Hope, and the excess is simply thrown away.
        hero = hero_with(shadow=12, max_hope=14)
        outcome = gain_shadow(hero, 5, ShadowSource.MISDEED, ctx=context(hero))
        assert (outcome.gained, outcome.discarded) == (2, 3)
        assert hero.shadow == 14
        assert outcome.at_maximum
        hero.validate()

    def test_a_gain_at_the_ceiling_adds_nothing(self) -> None:
        hero = hero_with(shadow=14, max_hope=14)
        outcome = gain_shadow(hero, 3, ShadowSource.MISDEED, ctx=context(hero))
        assert (outcome.gained, outcome.discarded) == (0, 3)
        assert hero.shadow == 14

    def test_the_gain_hook_can_raise_what_a_source_inflicts(self) -> None:
        hero = hero_with()
        bus = EffectBus()
        bus.register(
            effect(
                "dread_whispers",
                EffectKind.CURSE,
                "numeric_modifier",
                hook="MODIFY_SHADOW_GAIN",
                delta=1,
                predicate={"flag": "source_dread"},
            ),
            EffectSource.item("cursed_ring"),
        )
        ctx = context(hero, bus)
        assert gain_shadow(hero, 2, ShadowSource.DREAD, ctx=ctx).gained == 3
        assert gain_shadow(hero, 2, ShadowSource.GREED, ctx=ctx).gained == 2

    def test_the_gain_hook_cannot_push_a_gain_below_zero(self) -> None:
        hero = hero_with()
        bus = EffectBus()
        bus.register(
            effect(
                "unshakeable",
                EffectKind.VIRTUE,
                "numeric_modifier",
                hook="MODIFY_SHADOW_GAIN",
                delta=-5,
            ),
            EffectSource.acquired("virtue"),
        )
        assert gain_shadow(hero, 2, ShadowSource.MISDEED, ctx=context(hero, bus)).gained == 0
        assert hero.shadow == 0

    def test_miserable_is_recomputed_rather_than_written_here(self) -> None:
        # 03.8 reserves the flag to resources.recompute_conditions, and every function
        # here ends by calling it.
        hero = hero_with(hope=4, max_hope=14)
        assert not hero.conditions.miserable
        outcome = gain_shadow(hero, 4, ShadowSource.MISDEED, ctx=context(hero))
        assert hero.conditions.miserable
        assert EventKind.CONDITION_CHANGED in {e.kind for e in outcome.events}

    def test_a_misdeed_may_carry_a_scar(self) -> None:
        # 11.4.4's top row: "4 plus 1 Shadow Scar" — the scar is a further point.
        hero = hero_with()
        outcome = gain_shadow(hero, 4, ShadowSource.MISDEED, ctx=context(hero), add_scar=True)
        assert (outcome.gained, outcome.scar_gained) == (5, True)
        assert (hero.shadow, hero.shadow_scars) == (5, 1)
        hero.validate()

    def test_a_scar_the_ceiling_discards_is_never_gained(self) -> None:
        # Invariant I6 would break if the scar were recorded while its point was not.
        hero = hero_with(shadow=13, max_hope=14)
        outcome = gain_shadow(hero, 4, ShadowSource.MISDEED, ctx=context(hero), add_scar=True)
        assert not outcome.scar_gained
        assert (hero.shadow, hero.shadow_scars) == (14, 0)
        hero.validate()


class TestOverburdened:
    """11.2 — Ill-favoured on every roll at maximum Shadow, as an always-on effect."""

    def test_the_predicate_is_silent_below_the_maximum(self) -> None:
        hero = hero_with(shadow=13, max_hope=14)
        bus = EffectBus()
        register_shadow_conditions(bus)
        request = build_request(hero, AbilityId("hunting"), bus=bus, target_number=14)
        assert request.ill_favoured_sources == ()

    def test_it_ill_favours_everything_at_the_maximum(self) -> None:
        hero = hero_with(shadow=14, max_hope=14)
        bus = EffectBus()
        register_shadow_conditions(bus)
        request = build_request(hero, AbilityId("hunting"), bus=bus, target_number=14)
        assert request.ill_favoured_sources == (str(OVERBURDENED_ID),)

    def test_it_turns_itself_off_again(self) -> None:
        # The reason it is registered permanently: nothing has to remember to detach it.
        hero = hero_with(shadow=14, max_hope=14)
        bus = EffectBus()
        register_shadow_conditions(bus)
        remove_shadow(hero, 3, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero, bus))
        request = build_request(hero, AbilityId("hunting"), bus=bus, target_number=14)
        assert request.ill_favoured_sources == ()

    def test_registering_it_twice_is_a_caller_bug(self) -> None:
        bus = EffectBus()
        register_shadow_conditions(bus)
        with pytest.raises(RuleViolation, match="already registered"):
            register_shadow_conditions(bus)

    def test_it_is_silent_for_an_actor_that_has_no_shadow(self) -> None:
        # The bus dispatches for adversaries too, and they have neither field.
        bus = EffectBus()
        register_shadow_conditions(bus)
        ctx = HookContext(hook=Hook.MODIFY_ROLL_REQUEST, actor=object())
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx) == []


# -- 11.5 / 11.9, shedding ---------------------------------------------------------------


class TestHardenWill:
    def test_the_mandatory_vector(self) -> None:
        # 19.4: Shadow 7, scars 0 -> Shadow becomes 1, scars 1. Not 0.
        hero = hero_with(shadow=7, scars=0)
        events = harden_will(hero, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (1, 1)
        assert {e.kind for e in events} >= {
            EventKind.WILL_HARDENED,
            EventKind.SHADOW_SCAR_GAINED,
        }
        hero.validate()

    def test_shadow_becomes_the_scar_count_not_one(self) -> None:
        # 11.5's code block sets Shadow to 1; its own note three lines down says the scar
        # count. A hero with two scars would otherwise end with three scars and Shadow 1,
        # breaching invariant I6.
        hero = hero_with(shadow=9, scars=2)
        harden_will(hero, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (3, 3)
        hero.validate()

    def test_it_is_refused_at_maximum_hope(self) -> None:
        hero = hero_with(shadow=14, max_hope=14)
        with pytest.raises(RuleViolation, match="below maximum Hope"):
            harden_will(hero, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (14, 0)


class TestRemoveShadow:
    def test_the_loremasters_figure_is_removed(self) -> None:
        hero = hero_with(shadow=8)
        events = remove_shadow(hero, 3, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero))
        assert hero.shadow == 5
        assert events[0].kind is EventKind.SHADOW_REMOVED
        assert events[0].payload["removed"] == 3

    def test_removal_stops_at_the_scars(self) -> None:
        # 11.5: a Scar is permanent and only Heal Scars removes one.
        hero = hero_with(shadow=4, scars=2)
        remove_shadow(hero, 10, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (2, 2)
        hero.validate()

    def test_the_cap_applies_after_the_loremasters_figure(self) -> None:
        # 11.9's one procedural instruction, and the culture weakness that needs it.
        hero = hero_with(shadow=8)
        bus = EffectBus()
        bus.register(
            effect(
                "the_long_defeat",
                EffectKind.CULTURAL_BLESSING,
                "numeric_modifier",
                hook="MODIFY_SHADOW_REMOVAL_CAP",
                delta=-2,
            ),
            EffectSource.culture("second_folk"),
        )
        events = remove_shadow(hero, 3, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero, bus))
        assert hero.shadow == 7
        assert events[0].payload == {
            "reason": "fellowship_phase",
            "requested": 3,
            "allowed": 1,
            "removed": 1,
            "shadow": 7,
        }

    def test_a_hard_cap_replaces_the_figure_outright(self) -> None:
        # The Long Defeat is really a ceiling of 1 whatever the Loremaster allowed, which
        # a replacement expresses and apply_numeric folds (04.2.2).
        hero = hero_with(shadow=8)
        bus = EffectBus()
        bus.register(
            effect(
                "the_long_defeat",
                EffectKind.CULTURAL_BLESSING,
                "replace_value",
                hook="MODIFY_SHADOW_REMOVAL_CAP",
                value=1,
            ),
            EffectSource.culture("second_folk"),
        )
        remove_shadow(hero, 3, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero, bus))
        assert hero.shadow == 7

    def test_the_cap_may_only_restrict_never_raise(self) -> None:
        hero = hero_with(shadow=8)
        bus = EffectBus()
        bus.register(
            effect(
                "generous",
                EffectKind.VIRTUE,
                "numeric_modifier",
                hook="MODIFY_SHADOW_REMOVAL_CAP",
                delta=5,
            ),
            EffectSource.acquired("virtue"),
        )
        remove_shadow(hero, 2, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero, bus))
        assert hero.shadow == 6, "the Loremaster allowed 2, so 2 is the most that goes"

    def test_a_negative_removal_is_a_caller_bug(self) -> None:
        hero = hero_with(shadow=4)
        with pytest.raises(StateError, match="use gain_shadow"):
            remove_shadow(hero, -1, RemovalReason.LOREMASTER, ctx=context(hero))

    def test_a_shadow_floor_holds_removal_back(self) -> None:
        # 13.8's Shadow Taint sets a floor on the score rather than inflicting a gain.
        hero = hero_with(shadow=6)
        bus = EffectBus()
        bus.register(
            effect(
                "shadow_taint",
                EffectKind.CURSE,
                "numeric_modifier",
                hook="MODIFY_SHADOW_FLOOR",
                delta=2,
            ),
            EffectSource.item("cursed_ring"),
        )
        remove_shadow(hero, 6, RemovalReason.FELLOWSHIP_PHASE, ctx=context(hero, bus))
        assert hero.shadow == 2


class TestHealScar:
    def test_one_scar_and_its_point_go(self) -> None:
        hero = hero_with(shadow=5, scars=2)
        events = heal_scar(hero, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (4, 1)
        assert events[0].kind is EventKind.SHADOW_SCAR_HEALED
        hero.validate()

    def test_it_removes_exactly_one(self) -> None:
        hero = hero_with(shadow=3, scars=3)
        heal_scar(hero, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (2, 2)

    def test_a_hero_with_no_scars_is_refused(self) -> None:
        hero = hero_with(shadow=3)
        with pytest.raises(RuleViolation, match="no Shadow Scars"):
            heal_scar(hero, ctx=context(hero))


# -- 11.6 / 11.7 / 11.8 ------------------------------------------------------------------


class TestShadowPaths:
    def test_a_step_reads_the_paths_flaw(self) -> None:
        assert shadow_path_flaw(PATH, 1) == "idle"
        assert shadow_path_flaw(PATH, 4) == "cowardly"

    @pytest.mark.parametrize("step", [0, 5])
    def test_a_step_outside_the_path_is_a_caller_bug(self, step: int) -> None:
        with pytest.raises(StateError, match="no step"):
            shadow_path_flaw(PATH, step)


class TestBoutOfMadness:
    def test_the_mandatory_vector(self) -> None:
        # 19.4: Shadow 14 = max Hope 14, scars 2 -> Shadow becomes 2, one Flaw added,
        # path step 1.
        hero = hero_with(shadow=14, scars=2, max_hope=14)
        outcome = bout_of_madness(
            hero, "he turns on his companions", path_steps=PATH, ctx=context(hero)
        )
        assert hero.shadow == 2
        assert hero.shadow_path_step == 1
        assert hero.flaws == ["idle"]
        assert outcome.flaw == "idle"
        assert not outcome.at_final_step
        hero.validate()

    def test_the_scars_survive_the_bout(self) -> None:
        hero = hero_with(shadow=14, scars=3, max_hope=14)
        bout_of_madness(hero, "he flees", path_steps=PATH, ctx=context(hero))
        assert (hero.shadow, hero.shadow_scars) == (3, 3)

    def test_the_hero_is_no_longer_ill_favoured_afterwards(self) -> None:
        hero = hero_with(shadow=14, max_hope=14)
        bus = EffectBus()
        register_shadow_conditions(bus)
        bout_of_madness(hero, "he broods, then strikes", path_steps=PATH, ctx=context(hero, bus))
        request = build_request(hero, AbilityId("hunting"), bus=bus, target_number=14)
        assert request.ill_favoured_sources == ()

    def test_each_bout_takes_the_next_step(self) -> None:
        hero = hero_with(shadow=14, max_hope=14, flaws=2, step=2)
        outcome = bout_of_madness(hero, "again", path_steps=PATH, ctx=context(hero))
        assert (outcome.step, outcome.flaw) == (3, "uncaring")
        assert hero.flaws == ["idle", "forgetful", "uncaring"]
        hero.validate()

    def test_the_fourth_bout_is_the_last(self) -> None:
        hero = hero_with(shadow=14, max_hope=14, flaws=3, step=3)
        outcome = bout_of_madness(hero, "the last time", path_steps=PATH, ctx=context(hero))
        assert outcome.at_final_step

    def test_a_hero_with_every_flaw_has_no_further_bout(self) -> None:
        hero = hero_with(shadow=14, max_hope=14, flaws=4, step=4)
        with pytest.raises(RuleViolation, match="no further bout"):
            bout_of_madness(hero, "there is nothing left", path_steps=PATH, ctx=context(hero))

    def test_a_bout_must_be_described(self) -> None:
        # 11.6 makes describing it the substance of it.
        hero = hero_with(shadow=14, max_hope=14)
        with pytest.raises(RuleViolation, match="described by the player"):
            bout_of_madness(hero, "  ", path_steps=PATH, ctx=context(hero))

    def test_a_floor_holds_the_bout_back(self) -> None:
        hero = hero_with(shadow=14, max_hope=14)
        bus = EffectBus()
        bus.register(
            effect(
                "shadow_taint",
                EffectKind.CURSE,
                "numeric_modifier",
                hook="MODIFY_SHADOW_FLOOR",
                delta=3,
            ),
            EffectSource.item("cursed_ring"),
        )
        bout_of_madness(hero, "he rages", path_steps=PATH, ctx=context(hero, bus))
        assert hero.shadow == 3


class TestFocusInteraction:
    def test_the_mandatory_vector_continues(self) -> None:
        # 19.4: after the bout, every hero whose Fellowship Focus is this hero gained 1
        # unresistible Shadow.
        stricken = hero_with(shadow=14, scars=2, max_hope=14, hero_id="stricken")
        watcher = hero_with(shadow=0, hero_id="watcher")
        ctx, _, _ = pair(stricken, watcher)
        company = Company(heroes=[stricken.id, watcher.id])
        company.set_focus(watcher.id, stricken.id)

        bout_of_madness(stricken, "he turns on them", path_steps=PATH, ctx=ctx)
        events = harm_to_focus(company, stricken.id, {watcher.id: watcher}, ctx=ctx)

        assert watcher.shadow == 1
        gained = [e for e in events if e.kind is EventKind.SHADOW_GAINED]
        assert [e.payload["source"] for e in gained] == ["focus"]

    def test_nobody_focused_on_them_means_nothing_happens(self) -> None:
        a = hero_with(hero_id="a")
        b = hero_with(hero_id="b")
        ctx, _, _ = pair(a, b)
        company = Company(heroes=[a.id, b.id])
        assert harm_to_focus(company, a.id, {b.id: b}, ctx=ctx) == []
        assert b.shadow == 0

    def test_the_point_cannot_be_resisted(self) -> None:
        # 11.3: the 1 point gained when your Focus is harmed cannot be prevented.
        assert resisting_ability(ShadowSource.FOCUS) is None


class TestSuccumbing:
    def test_four_flaws_and_maximum_shadow_takes_a_hero_out_of_play(self) -> None:
        hero = hero_with(shadow=14, max_hope=14, flaws=4, step=4)
        assert check_succumb(hero)

    def test_four_flaws_below_the_maximum_does_not(self) -> None:
        hero = hero_with(shadow=13, max_hope=14, flaws=4, step=4)
        assert not check_succumb(hero)

    def test_maximum_shadow_with_three_flaws_does_not(self) -> None:
        hero = hero_with(shadow=14, max_hope=14, flaws=3, step=3)
        assert not check_succumb(hero)

    def test_a_temporary_flaw_does_not_count_toward_succumbing(self) -> None:
        # 11.7: the Curse of Weakness gives the bearer the worst Flaw on their own path,
        # and such a Flaw is excluded from the four.
        hero = hero_with(shadow=14, max_hope=14, flaws=3, step=3)
        hero.temporary_flaws.append(EffectId("cowardly"))
        assert not check_succumb(hero)

    def test_a_gain_that_reaches_the_maximum_reports_succumbing(self) -> None:
        hero = hero_with(shadow=13, max_hope=14, flaws=4, step=4)
        outcome = gain_shadow(hero, 1, ShadowSource.MISDEED, ctx=context(hero))
        assert outcome.at_maximum and outcome.succumbed


# -- 11.4, grading a source ---------------------------------------------------------------


class TestGrading:
    def test_a_misdeed_can_be_previewed_before_it_is_committed(self) -> None:
        # 11.4.3: the Loremaster should normally warn players first, and a warning nobody
        # can compute is no warning at all.
        assert preview_misdeed(1, MISDEEDS) == MisdeedCost(points=1, scar=False)
        assert preview_misdeed(4, MISDEEDS) == MisdeedCost(points=3, scar=False)

    def test_the_worst_grade_carries_a_scar(self) -> None:
        assert preview_misdeed(6, MISDEEDS) == MisdeedCost(points=4, scar=True)

    def test_dread_is_graded_not_rolled(self) -> None:
        assert [graded_dread(g, DREAD) for g in range(1, 7)] == [1, 1, 2, 2, 3, 4]

    def test_a_previewed_misdeed_feeds_straight_into_a_gain(self) -> None:
        hero = hero_with()
        cost = preview_misdeed(6, MISDEEDS)
        outcome = gain_shadow(
            hero, cost.points, ShadowSource.MISDEED, ctx=context(hero), add_scar=cost.scar
        )
        assert (hero.shadow, hero.shadow_scars) == (5, 1)
        assert outcome.scar_gained


class TestScriptedFaces:
    """A sanity net on the dice the other tests script, per 19.2."""

    @pytest.mark.parametrize("face", [FeatFace.RUNE, FeatFace.EYE, 1, 10])
    def test_a_shadow_test_always_consumes_one_feat_die_and_the_rank(self, face: FeatValue) -> None:
        hero = hero_with()
        rng = ScriptedRandomness(feats=[face], successes=[4, 4])
        resolve_shadow_test(hero, ShadowSource.DREAD, rng, ctx=context(hero))
        assert rng.exhausted
