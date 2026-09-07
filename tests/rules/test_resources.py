"""`tor.rules.resources` — Endurance, Hope, Fatigue, Treasure, Load, conditions, rest (spec 07).

Build step 7 uses **no randomness at all**: resting, Load, the conditions and the four
`change_*` functions are wholly deterministic. So no `ScriptedRandomness` appears in this
module and there is no `roll()` helper — the exhaustion discipline of 19.2 has nothing to
bite on here, and inventing a scripted RNG would only suggest otherwise.

Every test builds its own effect bus by hand rather than loading a pack. That is what the
narrow `GearIndex` protocol of 01.5 is for.
"""

from __future__ import annotations

import pytest

from tor.effects.bus import Effect, EffectBus, EffectKind, EffectSource, UsagePolicy, UsageScope
from tor.effects.hooks import FlagContribution, Hook, NumericContribution
from tor.effects.library import build_effect
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS
from tor.model.attributes import AttributeSet
from tor.model.company import Company
from tor.model.conditions import (
    ConditionSet,
    ResourcePool,
    StandardOfLiving,
    TreasureHoldings,
)
from tor.model.derive import DerivedStat
from tor.model.gear import (
    ArmourCategory,
    ArmourInstance,
    ArmourType,
    Gear,
    ShieldInstance,
    ShieldType,
    WeaponInstance,
    WeaponType,
)
from tor.model.hero import Hero
from tor.model.ids import CallingId, CultureId, EffectId, HeroId, ItemId
from tor.rules.context import RulesContext
from tor.rules.resources import (
    ChangeSource,
    RestDelta,
    RestKind,
    TreasurePlace,
    ZeroEnduranceOutcome,
    advance_injury_recovery,
    change_endurance,
    change_fatigue,
    change_hope,
    change_treasure,
    move_treasure,
    prolonged_rest,
    recompute_conditions,
    recompute_load,
    resolve_rest,
    short_rest,
    spend_fellowship_for_hope,
    treasure_load,
)

# 05.9's ladder as content would supply it.
LADDER: list[tuple[StandardOfLiving, int | None]] = [
    (StandardOfLiving.POOR, None),
    (StandardOfLiving.FRUGAL, 0),
    (StandardOfLiving.COMMON, 30),
    (StandardOfLiving.PROSPEROUS, 90),
    (StandardOfLiving.RICH, 180),
    (StandardOfLiving.VERY_RICH, 300),
]

BLADE = ItemId("example_blade")
MAIL = ItemId("example_mail")
HELM = ItemId("example_helm")
SHIELD = ItemId("example_shield")


class FakeGear:
    """A `GearIndex` over three dicts (01.5) — the point of keeping it a Protocol."""

    def __init__(self, *, weapon_load: int = 2, armour_load: int = 8, shield_load: int = 3) -> None:
        self._weapons = {
            BLADE: WeaponType(
                id=BLADE,
                name="Example Blade",
                damage=5,
                injury_one_handed=16,
                injury_two_handed=None,
                load=weapon_load,
                proficiency=SKILLS and next(iter(COMBAT_PROFICIENCIES)),
            )
        }
        self._armour = {
            MAIL: ArmourType(
                id=MAIL,
                name="Example Mail",
                protection=3,
                load=armour_load,
                category=ArmourCategory.MAIL,
            ),
            HELM: ArmourType(
                id=HELM,
                name="Example Helm",
                protection=1,
                load=4,
                category=ArmourCategory.HEADGEAR,
            ),
        }
        self._shields = {
            SHIELD: ShieldType(id=SHIELD, name="Example Shield", parry_bonus=1, load=shield_load)
        }

    def weapon(self, item_id: str) -> WeaponType:
        return self._weapons[ItemId(item_id)]

    def armour_type(self, item_id: str) -> ArmourType:
        return self._armour[ItemId(item_id)]

    def shield_type(self, item_id: str) -> ShieldType:
        return self._shields[ItemId(item_id)]


def make_hero(**overrides: object) -> Hero:
    defaults: dict[str, object] = {
        "id": HeroId("h1"),
        "name": "Testfolk Wanderer",
        "culture": CultureId("example_folk"),
        "calling": CallingId("example_calling"),
        "age": 30,
        "attributes": AttributeSet(strength=5, heart=6, wits=3),
        "skills": dict.fromkeys(SKILLS, 1),
        "proficiencies": dict.fromkeys(COMBAT_PROFICIENCIES, 0),
        "max_endurance": DerivedStat(base=25),
        "max_hope": DerivedStat(base=14),
        "parry": DerivedStat(base=15),
        "endurance": 25,
        "hope": 14,
        "conditions": ConditionSet(),
        "gear": Gear(),
    }
    return Hero(**(defaults | overrides))  # type: ignore[arg-type]


def make_ctx(
    *heroes: Hero,
    bus: EffectBus | None = None,
    gear: FakeGear | None = None,
) -> RulesContext:
    """A context covering every hero passed, sharing one bus unless told otherwise."""
    shared = bus if bus is not None else EffectBus()
    return RulesContext(gear=gear or FakeGear(), buses={hero.id: shared for hero in heroes})


def numeric(effect_id: str, hook: Hook, delta: int, **params: object) -> Effect:
    return build_effect(
        EffectId(effect_id),
        EffectKind.VIRTUE,
        "numeric_modifier",
        {"hook": hook.value, "delta": delta, **params},
    )


def flag(effect_id: str, hook: Hook, name: str, **params: object) -> Effect:
    return build_effect(
        EffectId(effect_id),
        EffectKind.CULTURAL_VIRTUE,
        "set_flag",
        {"hook": hook.value, "flag": name, **params},
    )


def kinds(events: list[object]) -> list[EventKind]:
    return [e.kind for e in events]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------------------
# 19.4 — the mandatory vectors this build step is responsible for
# --------------------------------------------------------------------------------------


class TestMandatoryVectors:
    def test_fatigue_and_load(self) -> None:
        # 19.4: Fatigue 3 raises Load by 3; a hero at Endurance 10 / Load 8 becomes Weary
        # at Fatigue 2.
        hero = make_hero(endurance=10, gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        ctx = make_ctx(hero)
        assert recompute_load(hero, ctx=ctx) == 8

        hero.fatigue = 3
        assert recompute_load(hero, ctx=ctx) == 11

        hero.fatigue = 0
        recompute_conditions(hero, ctx=ctx)
        assert not hero.conditions.weary

        change_fatigue(hero, 2, ChangeSource.JOURNEY, ctx=ctx)
        # Load 10, Endurance 10 — equal, and 07.5's threshold is `<=`.
        assert recompute_load(hero, ctx=ctx) == 10
        assert hero.conditions.weary

    def test_weary_threshold_is_inclusive(self) -> None:
        # 07.5 says this in so many words: Endurance exactly equal to Load means Weary.
        hero = make_hero(endurance=8, gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        ctx = make_ctx(hero)
        assert recompute_load(hero, ctx=ctx) == 8
        recompute_conditions(hero, ctx=ctx)
        assert hero.conditions.weary

        hero.endurance = 9
        recompute_conditions(hero, ctx=ctx)
        assert not hero.conditions.weary

    def test_carried_versus_cached_treasure(self) -> None:
        # 19.4: cache 60 of 90 — Standard of Living stays Prosperous, Load drops by 60.
        hero = make_hero(treasure=TreasureHoldings(carried=90))
        ctx = make_ctx(hero)
        assert recompute_load(hero, ctx=ctx) == 90
        assert hero.standard_of_living(LADDER) is StandardOfLiving.PROSPEROUS

        move_treasure(
            hero, 60, origin=TreasurePlace.CARRIED, destination=TreasurePlace.CACHED, ctx=ctx
        )
        assert recompute_load(hero, ctx=ctx) == 30
        assert hero.treasure.total == 90
        assert hero.standard_of_living(LADDER) is StandardOfLiving.PROSPEROUS


# --------------------------------------------------------------------------------------
# 07.4 — Load
# --------------------------------------------------------------------------------------


class TestLoad:
    def test_load_sums_war_gear_treasure_and_fatigue(self) -> None:
        hero = make_hero(
            gear=Gear(
                weapons=[WeaponInstance(type_id=BLADE)],
                armour=ArmourInstance(type_id=MAIL),
                helm=ArmourInstance(type_id=HELM),
                shield=ShieldInstance(type_id=SHIELD),
            ),
            treasure=TreasureHoldings(carried=5),
            fatigue=2,
        )
        # 2 + 8 + 4 + 3 war gear, + 5 Treasure, + 2 Fatigue.
        assert recompute_load(hero, ctx=make_ctx(hero)) == 24

    def test_a_dropped_item_weighs_nothing(self) -> None:
        # 08.6: dropping a helm mid-combat is a legal secondary action precisely because
        # it sheds Load, which may immediately clear Weary.
        hero = make_hero(gear=Gear(helm=ArmourInstance(type_id=HELM)))
        ctx = make_ctx(hero)
        assert recompute_load(hero, ctx=ctx) == 4
        hero.gear.helm.dropped = True
        assert recompute_load(hero, ctx=ctx) == 0

    def test_only_carried_treasure_counts(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=3, on_mounts=20, cached=50))
        assert treasure_load(hero) == 3
        assert recompute_load(hero, ctx=make_ctx(hero)) == 3

    def test_modify_item_load_reduces_an_item(self) -> None:
        # Cunning Make: -2 to a piece of war gear.
        hero = make_hero(gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        bus = EffectBus()
        bus.register(
            numeric("cunning_make", Hook.MODIFY_ITEM_LOAD, -2), EffectSource.acquired("reward")
        )
        assert recompute_load(hero, ctx=make_ctx(hero, bus=bus)) == 6

    def test_an_items_load_is_floored_at_zero(self) -> None:
        # A -2 modifier on a Load-1 item must not make the hero lighter for wearing it.
        hero = make_hero(gear=Gear(weapons=[WeaponInstance(type_id=BLADE)]))
        bus = EffectBus()
        bus.register(
            numeric("very_cunning", Hook.MODIFY_ITEM_LOAD, -9), EffectSource.acquired("reward")
        )
        assert recompute_load(hero, ctx=make_ctx(hero, bus=bus, gear=FakeGear(weapon_load=1))) == 0

    def test_a_replacement_overrides_the_base_load(self) -> None:
        # 04.3.2 names Mithril for MODIFY_ITEM_LOAD: it replaces rather than adjusts.
        hero = make_hero(gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        bus = EffectBus()
        bus.register(
            build_effect(
                EffectId("mithril_make"),
                EffectKind.ENCHANTED_REWARD,
                "modify_piercing_threshold",
                {"threshold": 2},
            ),
            EffectSource.acquired("enchanted"),
        )
        # That factory only listens on PIERCING_THRESHOLD, so Load is untouched — the
        # point is that apply_numeric folds an int replacement when one does arrive.
        assert recompute_load(hero, ctx=make_ctx(hero, bus=bus)) == 8

    def test_recomputing_load_never_consumes_a_budget(self) -> None:
        # 19.5: derived stats are idempotent. A budgeted Load effect burned on every
        # recompute would make Load depend on how often it had been asked for.
        hero = make_hero(gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        effect = numeric(
            "budgeted_load",
            Hook.MODIFY_ITEM_LOAD,
            -1,
            usage={"scope": UsageScope.SCENE.value, "limit": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired("reward"))
        ctx = make_ctx(hero, bus=bus)

        first = recompute_load(hero, ctx=ctx)
        second = recompute_load(hero, ctx=ctx)
        assert first == second == 7
        assert bus.uses(effect.id, UsageScope.SCENE) == 0


# --------------------------------------------------------------------------------------
# 07.5 — conditions
# --------------------------------------------------------------------------------------


class TestConditions:
    def test_a_flip_emits_exactly_one_event(self) -> None:
        hero = make_hero(endurance=4, gear=Gear(armour=ArmourInstance(type_id=MAIL)))
        ctx = make_ctx(hero)
        events = recompute_conditions(hero, ctx=ctx)
        assert kinds(events) == [EventKind.CONDITION_CHANGED]
        assert events[0].payload["condition"] == "weary"
        assert events[0].payload["active"] is True

    def test_a_no_op_recompute_is_silent(self) -> None:
        hero = make_hero()
        ctx = make_ctx(hero)
        recompute_conditions(hero, ctx=ctx)
        assert recompute_conditions(hero, ctx=ctx) == []

    def test_miserable_compares_shadow_with_current_hope(self) -> None:
        hero = make_hero(hope=5, shadow=5)
        recompute_conditions(hero, ctx=make_ctx(hero))
        assert hero.conditions.miserable

    def test_a_hero_at_zero_hope_and_zero_shadow_is_miserable(self) -> None:
        # 11.2 / 03.8: the comparison is `shadow >= hope`, so this reads like a bug and
        # is not. Spending your last Hope makes you Miserable.
        hero = make_hero(hope=0, shadow=0)
        recompute_conditions(hero, ctx=make_ctx(hero))
        assert hero.conditions.miserable

    def test_wounded_is_never_derived(self) -> None:
        # 07.5: Wounded is set by the Wound rules, not computed from anything.
        hero = make_hero(conditions=ConditionSet(wounded=True))
        recompute_conditions(hero, ctx=make_ctx(hero))
        assert hero.conditions.wounded

    def test_both_flags_flipping_emits_both_events(self) -> None:
        hero = make_hero(endurance=0, hope=0, shadow=3)
        events = recompute_conditions(hero, ctx=make_ctx(hero))
        assert kinds(events) == [EventKind.CONDITION_CHANGED, EventKind.CONDITION_CHANGED]

    def test_a_flag_clears_as_well_as_raises(self) -> None:
        hero = make_hero(endurance=1, conditions=ConditionSet(weary=True))
        ctx = make_ctx(hero)
        events = recompute_conditions(hero, ctx=ctx)
        assert not hero.conditions.weary
        assert events[0].payload["active"] is False


# --------------------------------------------------------------------------------------
# 07.2 — Endurance
# --------------------------------------------------------------------------------------


class TestEndurance:
    def test_loss_is_clamped_at_zero_and_never_raises(self) -> None:
        # 18: failing badly is a result, not an error.
        hero = make_hero(endurance=3)
        change_endurance(hero, -10, ChangeSource.COMBAT, ctx=make_ctx(hero))
        assert hero.endurance == 0

    def test_gain_is_clamped_at_the_maximum(self) -> None:
        hero = make_hero(endurance=24)
        change_endurance(hero, 10, ChangeSource.SHORT_REST, ctx=make_ctx(hero))
        assert hero.endurance == 25

    def test_an_unchanged_value_emits_no_change_event(self) -> None:
        hero = make_hero()
        assert kinds(change_endurance(hero, 0, ChangeSource.COMBAT, ctx=make_ctx(hero))) == []

    def test_reaching_zero_knocks_a_hero_unconscious(self) -> None:
        # 07.2: they wake after about an hour with 1 Endurance.
        hero = make_hero(endurance=4)
        events = change_endurance(hero, -4, ChangeSource.COMBAT, ctx=make_ctx(hero))
        unconscious = [e for e in events if e.kind is EventKind.HERO_UNCONSCIOUS]
        assert len(unconscious) == 1
        assert unconscious[0].payload == {"wakes_after_minutes": 60, "wakes_at_endurance": 1}
        assert not hero.dying

    def test_reaching_zero_while_wounded_is_dying(self) -> None:
        # 07.2 -> 08.9: the Dying rules take over instead.
        hero = make_hero(endurance=4, conditions=ConditionSet(wounded=True))
        events = change_endurance(hero, -4, ChangeSource.COMBAT, ctx=make_ctx(hero))
        assert EventKind.HERO_DYING in kinds(events)
        assert hero.dying

    def test_a_source_of_injury_overrides_the_default(self) -> None:
        # 16.4.3: fire and falling Wound at zero; cold, suffocation and poison kill.
        hero = make_hero(endurance=2)
        events = change_endurance(
            hero,
            -2,
            ChangeSource.INJURY_SOURCE,
            ctx=make_ctx(hero),
            at_zero=ZeroEnduranceOutcome.WOUNDED,
        )
        assert hero.conditions.wounded
        assert EventKind.HERO_UNCONSCIOUS not in kinds(events)

    def test_a_source_may_declare_dying_outright(self) -> None:
        hero = make_hero(endurance=2)
        change_endurance(
            hero,
            -2,
            ChangeSource.INJURY_SOURCE,
            ctx=make_ctx(hero),
            at_zero=ZeroEnduranceOutcome.DYING,
        )
        assert hero.dying

    def test_staying_at_zero_does_not_re_trigger(self) -> None:
        # The consequence fires on *reaching* zero, not on being there.
        hero = make_hero(endurance=0)
        events = change_endurance(hero, -3, ChangeSource.COMBAT, ctx=make_ctx(hero))
        assert EventKind.HERO_UNCONSCIOUS not in kinds(events)


# --------------------------------------------------------------------------------------
# 07.3 — Hope
# --------------------------------------------------------------------------------------


class TestHope:
    def test_spending_reduces_hope(self) -> None:
        hero = make_hero(hope=5)
        events = change_hope(hero, -2, ChangeSource.HOPE_SPEND, ctx=make_ctx(hero))
        assert hero.hope == 3
        assert events[0].payload["delta"] == -2

    def test_a_hero_at_zero_hope_cannot_spend(self) -> None:
        # 07.3: no bonus dice, no support, no magical success, no Hope-fuelled Virtue.
        hero = make_hero(hope=0)
        with pytest.raises(RuleViolation, match="zero Hope cannot spend"):
            change_hope(hero, -1, ChangeSource.HOPE_SPEND, ctx=make_ctx(hero))

    def test_overspending_is_refused(self) -> None:
        hero = make_hero(hope=1)
        with pytest.raises(RuleViolation, match="only 1 left"):
            change_hope(hero, -2, ChangeSource.HOPE_SPEND, ctx=make_ctx(hero))

    def test_a_gain_passes_through_the_recovery_hook(self) -> None:
        # The Virtue adding +1 to any Hope regain, which must reach all three routes.
        hero = make_hero(hope=5)
        bus = EffectBus()
        bus.register(
            numeric("pipe_weed", Hook.MODIFY_HOPE_RECOVERY, 1), EffectSource.acquired("virtue")
        )
        change_hope(hero, 1, ChangeSource.PROLONGED_REST, ctx=make_ctx(hero, bus=bus))
        assert hero.hope == 7

    def test_a_spend_does_not_pass_through_the_recovery_hook(self) -> None:
        hero = make_hero(hope=5)
        bus = EffectBus()
        bus.register(
            numeric("pipe_weed", Hook.MODIFY_HOPE_RECOVERY, 1), EffectSource.acquired("virtue")
        )
        change_hope(hero, -2, ChangeSource.HOPE_SPEND, ctx=make_ctx(hero, bus=bus))
        assert hero.hope == 3

    def test_a_gain_is_clamped_at_the_maximum(self) -> None:
        hero = make_hero(hope=13)
        change_hope(hero, 5, ChangeSource.FELLOWSHIP, ctx=make_ctx(hero))
        assert hero.hope == 14

    def test_a_clamped_gain_of_nothing_is_silent(self) -> None:
        hero = make_hero(hope=14)
        assert kinds(change_hope(hero, 3, ChangeSource.FELLOWSHIP, ctx=make_ctx(hero))) == []

    def test_the_route_reaches_a_predicate_as_a_flag(self) -> None:
        # build_predicate's {"flag": ...} form reads ctx.extra, so a pack predicating on
        # one route needs the flag spelling, not just the `route` string.
        hero = make_hero(hope=5)
        bus = EffectBus()
        bus.register(
            numeric(
                "rangers_blessing",
                Hook.MODIFY_HOPE_RECOVERY,
                2,
                predicate={"flag": "route_fellowship_phase"},
            ),
            EffectSource.culture(CultureId("example_folk")),
        )
        ctx = make_ctx(hero, bus=bus)

        change_hope(hero, 1, ChangeSource.PROLONGED_REST, ctx=ctx)
        assert hero.hope == 6  # the predicate did not match this route
        change_hope(hero, 1, ChangeSource.FELLOWSHIP_PHASE, ctx=ctx)
        assert hero.hope == 9  # 1 + 2 from the matching route

    def test_a_zero_delta_still_recomputes_conditions(self) -> None:
        # 07.1: every one of these ends by calling recompute_conditions.
        hero = make_hero(hope=3, shadow=5)
        events = change_hope(hero, 0, ChangeSource.EFFECT, ctx=make_ctx(hero))
        assert hero.conditions.miserable
        assert kinds(events) == [EventKind.CONDITION_CHANGED]


# --------------------------------------------------------------------------------------
# 07.4 — Fatigue and Treasure
# --------------------------------------------------------------------------------------


class TestFatigue:
    def test_fatigue_is_floored_at_zero(self) -> None:
        hero = make_hero(fatigue=1)
        change_fatigue(hero, -5, ChangeSource.PROLONGED_REST, ctx=make_ctx(hero))
        assert hero.fatigue == 0

    def test_a_gain_passes_through_the_hook(self) -> None:
        # Cram: one less Fatigue per journey event.
        hero = make_hero()
        bus = EffectBus()
        bus.register(numeric("cram", Hook.MODIFY_FATIGUE_GAIN, -1), EffectSource.acquired("virtue"))
        change_fatigue(hero, 3, ChangeSource.JOURNEY, ctx=make_ctx(hero, bus=bus))
        assert hero.fatigue == 2

    def test_a_no_fatigue_flag_cancels_the_gain(self) -> None:
        # Endurance of the Ranger: never gain Fatigue, conditionally.
        hero = make_hero()
        bus = EffectBus()
        bus.register(
            flag("ranger_stride", Hook.MODIFY_FATIGUE_GAIN, "no_fatigue"),
            EffectSource.acquired("virtue"),
        )
        change_fatigue(hero, 3, ChangeSource.JOURNEY, ctx=make_ctx(hero, bus=bus))
        assert hero.fatigue == 0

    def test_a_loss_does_not_pass_through_the_hook(self) -> None:
        hero = make_hero(fatigue=3)
        bus = EffectBus()
        bus.register(numeric("cram", Hook.MODIFY_FATIGUE_GAIN, -1), EffectSource.acquired("virtue"))
        change_fatigue(hero, -1, ChangeSource.PROLONGED_REST, ctx=make_ctx(hero, bus=bus))
        assert hero.fatigue == 2


class TestTreasure:
    def test_gaining_treasure_lands_in_the_carried_pile(self) -> None:
        hero = make_hero()
        change_treasure(hero, 40, ChangeSource.TREASURE_FOUND, ctx=make_ctx(hero))
        assert hero.treasure.carried == 40

    def test_treasure_may_be_gained_straight_onto_a_mount(self) -> None:
        hero = make_hero()
        change_treasure(
            hero,
            10,
            ChangeSource.TREASURE_FOUND,
            ctx=make_ctx(hero),
            where=TreasurePlace.ON_MOUNTS,
        )
        assert hero.treasure.on_mounts == 10
        assert recompute_load(hero, ctx=make_ctx(hero)) == 0

    def test_spending_more_than_is_held_is_refused(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=5))
        with pytest.raises(RuleViolation, match="only 5 there"):
            change_treasure(hero, -6, ChangeSource.TREASURE_SPENT, ctx=make_ctx(hero))

    def test_moving_treasure_keeps_the_total(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=30))
        events = move_treasure(
            hero,
            10,
            origin=TreasurePlace.CARRIED,
            destination=TreasurePlace.ON_MOUNTS,
            ctx=make_ctx(hero),
        )
        assert (hero.treasure.carried, hero.treasure.on_mounts) == (20, 10)
        assert events[0].payload["total"] == 30

    def test_moving_more_than_is_there_is_refused(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=5))
        with pytest.raises(RuleViolation, match="only 5 there"):
            move_treasure(
                hero,
                6,
                origin=TreasurePlace.CARRIED,
                destination=TreasurePlace.CACHED,
                ctx=make_ctx(hero),
            )

    def test_a_negative_move_is_refused(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=5))
        with pytest.raises(RuleViolation, match="non-negative"):
            move_treasure(
                hero,
                -1,
                origin=TreasurePlace.CARRIED,
                destination=TreasurePlace.CACHED,
                ctx=make_ctx(hero),
            )

    def test_moving_treasure_to_where_it_already_is_is_refused(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=5))
        with pytest.raises(RuleViolation, match="both"):
            move_treasure(
                hero,
                1,
                origin=TreasurePlace.CARRIED,
                destination=TreasurePlace.CARRIED,
                ctx=make_ctx(hero),
            )


# --------------------------------------------------------------------------------------
# 07.6 — resting
# --------------------------------------------------------------------------------------


class TestShortRest:
    def test_an_unwounded_hero_recovers_strength(self) -> None:
        hero = make_hero(endurance=10)
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero))
        assert outcome.per_hero[hero.id].endurance == 5
        assert hero.endurance == 15

    def test_a_wounded_hero_recovers_nothing(self) -> None:
        hero = make_hero(endurance=10, conditions=ConditionSet(wounded=True))
        short_rest(Company(), [hero], ctx=make_ctx(hero))
        assert hero.endurance == 10

    def test_a_company_wide_virtue_reaches_a_hero_who_is_not_the_actor(self) -> None:
        # 07.6: one Cultural Virtue makes a short rest restore an amount equal to the
        # bearer's WISDOM to *every* member of the Company. The target is the Company, not
        # the acting hero, so the hook payload has to carry the full party — and only the
        # hero who actually has the Virtue contributes it.
        bearer = make_hero(id=HeroId("bearer"), endurance=10)
        bearer.wisdom = 3
        other = make_hero(
            id=HeroId("other"),
            endurance=10,
            attributes=AttributeSet(strength=2, heart=4, wits=4),
        )

        bearer_bus = EffectBus()
        bearer_bus.register(
            build_effect(
                EffectId("cram"),
                EffectKind.CULTURAL_VIRTUE,
                "rest_recovery",
                {
                    "hook": Hook.MODIFY_SHORT_REST_RECOVERY.value,
                    "stat": "wisdom",
                    "target": "company",
                },
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        ctx = RulesContext(gear=FakeGear(), buses={bearer.id: bearer_bus, other.id: EffectBus()})

        outcome = short_rest(Company(), [bearer, other], ctx=ctx)
        # Each hero recovers their own STRENGTH, plus the bearer's WISDOM 3 to everyone.
        assert outcome.per_hero[other.id].endurance == 2 + 3
        assert outcome.per_hero[bearer.id].endurance == 5 + 3

    def test_a_company_wide_bonus_does_not_depend_on_hero_order(self) -> None:
        # Company amounts are gathered across every hero first and folded in second, so
        # the outcome cannot depend on the order the heroes were passed in.
        bearer = make_hero(id=HeroId("bearer"), endurance=10)
        bearer.wisdom = 3
        other = make_hero(id=HeroId("other"), endurance=10)

        def build() -> tuple[Hero, Hero, RulesContext]:
            a = make_hero(id=HeroId("bearer"), endurance=10)
            a.wisdom = 3
            b = make_hero(id=HeroId("other"), endurance=10)
            bus = EffectBus()
            bus.register(
                build_effect(
                    EffectId("cram"),
                    EffectKind.CULTURAL_VIRTUE,
                    "rest_recovery",
                    {
                        "hook": Hook.MODIFY_SHORT_REST_RECOVERY.value,
                        "stat": "wisdom",
                        "target": "company",
                    },
                ),
                EffectSource.acquired("cultural_virtue"),
            )
            return a, b, RulesContext(gear=FakeGear(), buses={a.id: bus, b.id: EffectBus()})

        a1, b1, ctx1 = build()
        a2, b2, ctx2 = build()
        forwards = resolve_rest(RestKind.SHORT, [a1, b1], ctx=ctx1, sheltered=False)
        backwards = resolve_rest(RestKind.SHORT, [b2, a2], ctx=ctx2, sheltered=False)
        assert forwards == backwards
        del bearer, other

    def test_the_conversion_virtue_is_checked_before_the_formula(self) -> None:
        # 07.6 is explicit about the ordering. A Wounded hero taking a short rest would
        # recover 0; converted to prolonged, they recover STRENGTH instead.
        hero = make_hero(endurance=10, conditions=ConditionSet(wounded=True))
        bus = EffectBus()
        bus.register(
            flag("elvish_dreams", Hook.SHORT_REST_COUNTS_AS_PROLONGED, "counts_as_prolonged"),
            EffectSource.acquired("cultural_virtue"),
        )
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus))
        assert outcome.per_hero[hero.id].counted_as_prolonged
        assert outcome.per_hero[hero.id].endurance == 5

    def test_the_conversion_brings_the_whole_prolonged_rest_with_it(self) -> None:
        # 07.6: "count as a prolonged rest entirely" — so the zero-Hope point and the
        # sheltered Fatigue shed come too, not just the Endurance formula.
        hero = make_hero(endurance=10, hope=0, fatigue=2)
        bus = EffectBus()
        bus.register(
            flag("elvish_dreams", Hook.SHORT_REST_COUNTS_AS_PROLONGED, "counts_as_prolonged"),
            EffectSource.acquired("cultural_virtue"),
        )
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus), sheltered=True)
        delta = outcome.per_hero[hero.id]
        assert (delta.hope, delta.fatigue) == (1, -1)


class TestProlongedRest:
    def test_an_unwounded_hero_recovers_all_lost_endurance(self) -> None:
        hero = make_hero(endurance=10)
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert hero.endurance == 25

    def test_a_wounded_hero_recovers_strength(self) -> None:
        hero = make_hero(endurance=10, conditions=ConditionSet(wounded=True))
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert hero.endurance == 15

    def test_a_hero_at_zero_hope_recovers_exactly_one(self) -> None:
        hero = make_hero(hope=0)
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert hero.hope == 1

    def test_a_hero_at_one_hope_recovers_none(self) -> None:
        # 07.3 calls this out as narrow and easy to over-implement.
        hero = make_hero(hope=1)
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert hero.hope == 1

    def test_fatigue_sheds_only_in_a_refuge(self) -> None:
        # 10.7: explicitly not "on the road".
        hero = make_hero(fatigue=2)
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert hero.fatigue == 2
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=True)
        assert hero.fatigue == 1

    def test_a_hero_with_no_fatigue_sheds_nothing(self) -> None:
        hero = make_hero(fatigue=0)
        outcome = prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=True)
        assert outcome.per_hero[hero.id].fatigue == 0

    def test_a_wounded_hero_doubling_virtue_reaches_the_hook(self) -> None:
        hero = make_hero(endurance=10, conditions=ConditionSet(wounded=True))
        bus = EffectBus()
        bus.register(
            build_effect(
                EffectId("deep_roots"),
                EffectKind.CULTURAL_VIRTUE,
                "rest_recovery",
                {
                    "hook": Hook.MODIFY_PROLONGED_REST_RECOVERY.value,
                    "stat": "strength",
                    "predicate": {"wounded": True},
                },
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        prolonged_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus), sheltered=False)
        assert hero.endurance == 20  # STRENGTH 5, doubled by the virtue's own 5

    def test_a_blocked_hero_gains_nothing(self) -> None:
        # 16.4.5: a poisoned hero cannot rest. Legal, but achieves nothing — so it is a
        # RestDelta naming the blocker, not a RuleViolation.
        hero = make_hero(endurance=10, hope=0, fatigue=1)
        bus = EffectBus()
        bus.register(
            flag("poisoned", Hook.MODIFY_PROLONGED_REST_RECOVERY, "cannot_rest"),
            EffectSource.condition("poisoned"),
        )
        outcome = prolonged_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus), sheltered=True)
        delta = outcome.per_hero[hero.id]
        assert delta.blocked_by == EffectId("poisoned")
        assert (delta.endurance, delta.hope, delta.fatigue) == (0, 0, 0)
        assert (hero.endurance, hero.hope, hero.fatigue) == (10, 0, 1)

    def test_the_outcome_carries_its_events(self) -> None:
        hero = make_hero(endurance=10)
        outcome = prolonged_rest(Company(), [hero], ctx=make_ctx(hero), sheltered=False)
        assert EventKind.RESTED in kinds(list(outcome.events))
        assert outcome.kind is RestKind.PROLONGED

    def test_resolving_a_rest_changes_nothing(self) -> None:
        # 01.4: the preview must be free of side effects, which is what makes it a preview.
        hero = make_hero(endurance=10, hope=0)
        per_hero = resolve_rest(RestKind.PROLONGED, [hero], ctx=make_ctx(hero), sheltered=True)
        assert per_hero[hero.id].endurance == 15
        assert hero.endurance == 10
        assert hero.hope == 0


# --------------------------------------------------------------------------------------
# 07.3 route 1 — Fellowship into Hope
# --------------------------------------------------------------------------------------


class TestFellowshipForHope:
    def test_one_point_buys_one_hope(self) -> None:
        hero = make_hero(hope=5)
        company = Company(heroes=[hero.id], fellowship=ResourcePool(current=3, maximum=3))
        events = spend_fellowship_for_hope(
            company, {hero.id: 2}, heroes={hero.id: hero}, ctx=make_ctx(hero)
        )
        assert hero.hope == 7
        assert company.fellowship.current == 1
        assert EventKind.FELLOWSHIP_SPENT in kinds(events)

    def test_an_over_allocation_fails_before_anyone_gains(self) -> None:
        # The pool is spent first, so a refusal leaves no hero half-credited.
        hero = make_hero(hope=5)
        company = Company(heroes=[hero.id], fellowship=ResourcePool(current=1, maximum=3))
        with pytest.raises(RuleViolation, match="only 1 left"):
            spend_fellowship_for_hope(
                company, {hero.id: 2}, heroes={hero.id: hero}, ctx=make_ctx(hero)
            )
        assert hero.hope == 5
        assert company.fellowship.current == 1

    def test_a_negative_allocation_is_refused(self) -> None:
        hero = make_hero()
        company = Company(heroes=[hero.id], fellowship=ResourcePool(current=3, maximum=3))
        with pytest.raises(RuleViolation, match="cannot allocate"):
            spend_fellowship_for_hope(
                company, {hero.id: -1}, heroes={hero.id: hero}, ctx=make_ctx(hero)
            )

    def test_an_unknown_hero_is_refused(self) -> None:
        hero = make_hero()
        company = Company(heroes=[hero.id], fellowship=ResourcePool(current=3, maximum=3))
        with pytest.raises(RuleViolation, match="not a hero of this Company"):
            spend_fellowship_for_hope(
                company, {HeroId("stranger"): 1}, heroes={hero.id: hero}, ctx=make_ctx(hero)
            )

    def test_an_empty_allocation_spends_nothing(self) -> None:
        hero = make_hero()
        company = Company(heroes=[hero.id], fellowship=ResourcePool(current=3, maximum=3))
        assert (
            spend_fellowship_for_hope(company, {}, heroes={hero.id: hero}, ctx=make_ctx(hero)) == []
        )
        assert company.fellowship.current == 3

    def test_heroes_are_credited_in_sorted_id_order(self) -> None:
        # 17.4 promises a replay reproduces state byte for byte, and a save/load round
        # trip need not preserve mapping insertion order.
        zed = make_hero(id=HeroId("zed"), hope=1)
        amy = make_hero(id=HeroId("amy"), hope=1)
        company = Company(heroes=[zed.id, amy.id], fellowship=ResourcePool(current=2, maximum=2))
        ctx = RulesContext(gear=FakeGear(), buses={zed.id: EffectBus(), amy.id: EffectBus()})
        events = spend_fellowship_for_hope(
            company, {zed.id: 1, amy.id: 1}, heroes={zed.id: zed, amy.id: amy}, ctx=ctx
        )
        credited = [e.actor for e in events if e.kind is EventKind.HOPE_CHANGED]
        assert credited == [HeroId("amy"), HeroId("zed")]


# --------------------------------------------------------------------------------------
# 07.7 — injury recovery
# --------------------------------------------------------------------------------------


class TestInjuryRecovery:
    def test_days_count_down(self) -> None:
        hero = make_hero(injury_days=3, conditions=ConditionSet(wounded=True))
        advance_injury_recovery(hero, ctx=make_ctx(hero))
        assert hero.injury_days == 2
        assert hero.conditions.wounded

    def test_reaching_zero_clears_the_wound_once(self) -> None:
        hero = make_hero(injury_days=1, conditions=ConditionSet(wounded=True))
        ctx = make_ctx(hero)
        events = advance_injury_recovery(hero, ctx=ctx)
        assert not hero.conditions.wounded
        assert kinds(events).count(EventKind.WOUND_HEALED) == 1

        assert EventKind.WOUND_HEALED not in kinds(advance_injury_recovery(hero, ctx=ctx))

    def test_several_days_may_pass_at_once(self) -> None:
        hero = make_hero(injury_days=10, conditions=ConditionSet(wounded=True))
        advance_injury_recovery(hero, days=10, ctx=make_ctx(hero))
        assert hero.injury_days == 0
        assert not hero.conditions.wounded

    def test_a_negative_advance_is_refused(self) -> None:
        hero = make_hero(injury_days=3)
        with pytest.raises(RuleViolation, match="non-negative"):
            advance_injury_recovery(hero, days=-1, ctx=make_ctx(hero))

    def test_an_unwounded_hero_with_no_days_is_unaffected(self) -> None:
        hero = make_hero()
        assert kinds(advance_injury_recovery(hero, ctx=make_ctx(hero))) == []


# --------------------------------------------------------------------------------------
# The paths the behavioural tests above do not reach
# --------------------------------------------------------------------------------------


class TestGuardsAndAccessors:
    def test_a_missing_bus_is_a_state_error(self) -> None:
        # 01.6: the engine was driven into an impossible state — a caller bug, not a rule.
        hero = make_hero()
        ctx = RulesContext(gear=FakeGear(), buses={})
        with pytest.raises(StateError, match="no effect bus registered"):
            recompute_load(hero, ctx=ctx)

    def test_consume_is_a_no_op_for_a_derived_stat_hook(self) -> None:
        # The rule stated in DERIVED_STAT_HOOKS: recomputing a derived value must not burn
        # a budget, or the value would depend on how often it had been asked for.
        hero = make_hero()
        effect = numeric(
            "budgeted",
            Hook.MODIFY_ITEM_LOAD,
            -1,
            usage={"scope": UsageScope.SCENE.value, "limit": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired("reward"))
        ctx = make_ctx(hero, bus=bus)
        hook_ctx = ctx.hook_context(Hook.MODIFY_ITEM_LOAD, hero)

        ctx.consume(hero.id, bus.collect(Hook.MODIFY_ITEM_LOAD, hook_ctx), hook_ctx)
        assert bus.uses(effect.id, UsageScope.SCENE) == 0

    def test_a_dropped_weapon_weighs_nothing(self) -> None:
        hero = make_hero(gear=Gear(weapons=[WeaponInstance(type_id=BLADE, dropped=True)]))
        assert recompute_load(hero, ctx=make_ctx(hero)) == 0

    def test_a_zero_treasure_change_emits_nothing(self) -> None:
        hero = make_hero(treasure=TreasureHoldings(carried=5))
        events = change_treasure(hero, 0, ChangeSource.TREASURE_FOUND, ctx=make_ctx(hero))
        assert EventKind.TREASURE_CHANGED not in kinds(events)
        assert hero.treasure.carried == 5

    def test_a_hero_allocated_no_fellowship_points_gains_nothing(self) -> None:
        giver = make_hero(id=HeroId("giver"), hope=1)
        skipped = make_hero(id=HeroId("skipped"), hope=1)
        company = Company(
            heroes=[giver.id, skipped.id], fellowship=ResourcePool(current=2, maximum=2)
        )
        ctx = RulesContext(gear=FakeGear(), buses={giver.id: EffectBus(), skipped.id: EffectBus()})
        spend_fellowship_for_hope(
            company,
            {giver.id: 1, skipped.id: 0},
            heroes={giver.id: giver, skipped.id: skipped},
            ctx=ctx,
        )
        assert giver.hope == 2
        assert skipped.hope == 1

    def test_in_scene_returns_a_copy(self) -> None:
        from tor.effects.hooks import Environment, SceneKind

        hero = make_hero()
        ctx = make_ctx(hero)
        scoped = ctx.in_scene(SceneKind.COMBAT, Environment(in_darkness=True))
        assert scoped.scene is SceneKind.COMBAT
        assert scoped.environment.in_darkness
        assert ctx.scene is None
        assert not ctx.environment.in_darkness

    def test_single_builds_a_one_actor_context(self) -> None:
        hero = make_hero()
        bus = EffectBus()
        ctx = RulesContext.single(hero.id, bus=bus, gear=FakeGear())
        assert ctx.bus(hero.id) is bus

    def test_a_non_derived_hook_does_consume(self) -> None:
        # The other half of the consume rule: a one-off contribution is spent.
        hero = make_hero(hope=1)
        effect = numeric(
            "once_only",
            Hook.MODIFY_HOPE_RECOVERY,
            1,
            usage={"scope": UsageScope.SCENE.value, "limit": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired("virtue"))
        ctx = make_ctx(hero, bus=bus)

        change_hope(hero, 1, ChangeSource.PROLONGED_REST, ctx=ctx)
        assert hero.hope == 3  # 1 + 1 base + 1 from the effect
        assert bus.uses(effect.id, UsageScope.SCENE) == 1

        change_hope(hero, 1, ChangeSource.PROLONGED_REST, ctx=ctx)
        assert hero.hope == 4  # the budget is spent, so only the base point lands

    def test_applying_a_rest_skips_a_hero_it_was_not_resolved_for(self) -> None:
        from tor.rules.resources import apply_rest

        hero = make_hero(endurance=10)
        assert apply_rest(RestKind.SHORT, [hero], {}, ctx=make_ctx(hero)) == []
        assert hero.endurance == 10

    def test_a_rest_delta_defaults_to_nothing(self) -> None:
        delta = RestDelta()
        assert (delta.endurance, delta.hope, delta.fatigue) == (0, 0, 0)
        assert delta.blocked_by is None

    def test_a_usage_policy_limit_may_scale_with_an_attribute(self) -> None:
        # The latent bug _stat_value fixes: Attributes live on hero.attributes, so a bare
        # getattr(hero, "wits") reads 0 and silently mis-scales the budget.
        hero = make_hero(hope=1)
        effect = numeric(
            "wits_many_times",
            Hook.MODIFY_HOPE_RECOVERY,
            1,
            usage={"scope": UsageScope.SCENE.value, "limit": "wits"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired("virtue"))
        ctx = make_ctx(hero, bus=bus)
        assert isinstance(effect.usage, UsagePolicy)
        assert effect.usage.limit_for(ctx.hook_context(Hook.MODIFY_HOPE_RECOVERY, hero)) == 3

    def test_a_flag_contribution_carrying_false_does_not_block(self) -> None:
        hero = make_hero(endurance=10)
        bus = EffectBus()
        bus.register(
            flag("not_really", Hook.MODIFY_PROLONGED_REST_RECOVERY, "cannot_rest", value=False),
            EffectSource.condition("nothing"),
        )
        outcome = prolonged_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus), sheltered=False)
        assert outcome.per_hero[hero.id].blocked_by is None

    def test_a_numeric_contribution_reaches_the_short_rest_hook(self) -> None:
        hero = make_hero(endurance=10)
        bus = EffectBus()
        bus.register(
            numeric("steady", Hook.MODIFY_SHORT_REST_RECOVERY, 2),
            EffectSource.acquired("virtue"),
        )
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus))
        assert outcome.per_hero[hero.id].endurance == 7

    def test_a_recovery_hook_cannot_drive_recovery_negative(self) -> None:
        hero = make_hero(endurance=10)
        bus = EffectBus()
        bus.register(
            numeric("cursed", Hook.MODIFY_SHORT_REST_RECOVERY, -99),
            EffectSource.acquired("curse"),
        )
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus))
        assert outcome.per_hero[hero.id].endurance == 0

    def test_flag_contributions_need_a_flag_name(self) -> None:
        from tor.errors import ContentError

        with pytest.raises(ContentError, match="needs a 'flag'"):
            build_effect(
                EffectId("nameless"),
                EffectKind.VIRTUE,
                "set_flag",
                {"hook": Hook.MODIFY_FATIGUE_GAIN.value},
            )

    def test_rest_recovery_may_be_a_flat_amount(self) -> None:
        hero = make_hero(endurance=10)
        bus = EffectBus()
        bus.register(
            build_effect(
                EffectId("flat"),
                EffectKind.VIRTUE,
                "rest_recovery",
                {"hook": Hook.MODIFY_SHORT_REST_RECOVERY.value, "delta": 3},
            ),
            EffectSource.acquired("virtue"),
        )
        outcome = short_rest(Company(), [hero], ctx=make_ctx(hero, bus=bus))
        assert outcome.per_hero[hero.id].endurance == 8

    def test_numeric_contributions_are_what_apply_numeric_folds(self) -> None:
        # Guard on the bus change: a plain numeric still becomes a delta.
        hero = make_hero()
        bus = EffectBus()
        bus.register(numeric("plain", Hook.MODIFY_ITEM_LOAD, 1), EffectSource.acquired("reward"))
        hook_ctx = make_ctx(hero, bus=bus).hook_context(Hook.MODIFY_ITEM_LOAD, hero)
        stat = bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, hook_ctx, 5)
        assert stat.value == 6
        assert isinstance(bus.collect(Hook.MODIFY_ITEM_LOAD, hook_ctx)[0], NumericContribution)

    def test_a_non_int_replacement_is_left_alone(self) -> None:
        # apply_numeric folds only int-valued replacements; anything else is not a value
        # for this stat and belongs to whoever collects the hook directly.
        hero = make_hero()
        bus = EffectBus()
        bus.register(
            Effect(
                id=EffectId("wordy"),
                kind=EffectKind.CURSE,
                listeners={
                    Hook.MODIFY_ITEM_LOAD: lambda ctx: FlagContribution(
                        source=EffectId("wordy"), flag="noise"
                    )
                },
            ),
            EffectSource.acquired("curse"),
        )
        hook_ctx = make_ctx(hero, bus=bus).hook_context(Hook.MODIFY_ITEM_LOAD, hero)
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, hook_ctx, 5).value == 5
