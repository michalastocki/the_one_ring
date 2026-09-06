"""`tor.effects` — the bus, its determinism and purity, and the declarative factories.

Spec 04. Seam B: every Blessing, Virtue, Reward, Curse, Fell Ability, Flaw and Condition is
a named package of listeners on named hooks, so nothing in the engine branches on a
content id.
"""

from __future__ import annotations

import pytest

from tor.effects.bus import (
    Effect,
    EffectBus,
    EffectKind,
    EffectSource,
    UsageScope,
)
from tor.effects.hooks import (
    DEFAULT_ENVIRONMENT,
    ActionContribution,
    Contribution,
    Environment,
    FlagContribution,
    Hook,
    HookContext,
    NumericContribution,
    ReplacementContribution,
    SceneKind,
)
from tor.effects.library import build_effect, build_predicate
from tor.errors import ContentError, RuleViolation
from tor.model.conditions import ConditionSet
from tor.model.ids import AbilityId, EffectId
from tor.rolls import FeatDicePolicy, RollPurpose, build_request


def ctx(hook: Hook = Hook.MODIFY_ROLL_REQUEST, **kwargs: object) -> HookContext:
    return HookContext(hook=hook, **kwargs)  # type: ignore[arg-type]


def numeric(
    effect_id: str, delta: int, *, hook: Hook = Hook.MODIFY_MAX_HOPE, **params: object
) -> Effect:
    return build_effect(
        EffectId(effect_id),
        EffectKind.VIRTUE,
        "numeric_modifier",
        {"hook": hook.value, "delta": delta, **params},
    )


class FakeCharacter:
    """A minimal RollingCharacter — the structural view of 01.5, nothing more."""

    def __init__(self, *, ratings: dict[str, int] | None = None, **attrs: object) -> None:
        self._ratings = ratings or {}
        self.effects = EffectBus()
        self.conditions = ConditionSet()
        for name, value in attrs.items():
            setattr(self, name, value)

    def rating(self, ability: AbilityId) -> int:
        return self._ratings.get(str(ability), 0)


class Support:
    def __init__(self, rating: int = 1, *, is_focus: bool = False, approved: bool = True) -> None:
        self.rating = rating
        self.is_focus = is_focus
        self.approved = approved


class TestRegistration:
    def test_registering_and_unregistering(self) -> None:
        bus = EffectBus()
        effect = numeric("hardiness", 2)
        bus.register(effect, EffectSource.acquired("virtue"))
        assert bus.registered(EffectId("hardiness"))
        bus.unregister(EffectId("hardiness"))
        assert not bus.registered(EffectId("hardiness"))

    def test_the_same_effect_from_two_sources_is_legal(self) -> None:
        # Hardiness is repeatable: a hero may hold it twice, from two acquisitions.
        bus = EffectBus()
        bus.register(numeric("hardiness", 2), EffectSource.acquired("first"))
        bus.register(numeric("hardiness", 2), EffectSource.acquired("second"))
        assert bus.apply_numeric(Hook.MODIFY_MAX_HOPE, ctx(), base=10).value == 14

    def test_registering_twice_from_one_source_is_refused(self) -> None:
        bus = EffectBus()
        source = EffectSource.item("blade#1")
        bus.register(numeric("grievous", 1), source)
        with pytest.raises(RuleViolation, match="already registered"):
            bus.register(numeric("grievous", 1), source)

    def test_unregistering_a_source_drops_everything_it_gave(self) -> None:
        # This is how a smashed shield loses its Parry and a dropped helm its Protection.
        bus = EffectBus()
        shield = EffectSource.item("shield#1")
        bus.register(numeric("reinforced", 1, hook=Hook.MODIFY_SHIELD_PARRY), shield)
        bus.register(numeric("cunning_make", -2, hook=Hook.MODIFY_ITEM_LOAD), shield)
        bus.register(
            numeric("nimbleness", 1, hook=Hook.MODIFY_SHIELD_PARRY), EffectSource.acquired()
        )
        bus.unregister_source(shield)
        assert bus.apply_numeric(Hook.MODIFY_SHIELD_PARRY, ctx(), base=0).value == 1
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=4).value == 4

    def test_register_then_unregister_restores_the_prior_derived_stat_exactly(self) -> None:
        # 19.5: the property that catches the whole class of effect-residue bugs.
        bus = EffectBus()
        before = bus.apply_numeric(Hook.MODIFY_PARRY, ctx(), base=12).value
        source = EffectSource.item("shield#1")
        bus.register(numeric("reinforced", 1, hook=Hook.MODIFY_PARRY), source)
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx(), base=12).value == before + 1
        bus.unregister_source(source)
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx(), base=12).value == before


class TestApplyNumericFolding:
    """What `apply_numeric` folds into a DerivedStat, and what it deliberately leaves."""

    def _bus_with(self, contribution: Contribution) -> EffectBus:
        bus = EffectBus()
        bus.register(
            Effect(
                id=EffectId("mithril_make"),
                kind=EffectKind.ENCHANTED_REWARD,
                listeners={Hook.MODIFY_ITEM_LOAD: lambda _ctx: contribution},
            ),
            EffectSource.item("mail#1"),
        )
        return bus

    def test_an_int_replacement_overrides_the_base(self) -> None:
        # 04.3.2 names Mithril for MODIFY_ITEM_LOAD: it replaces an item's Load outright
        # rather than adjusting it, so folding only NumericContribution would drop it.
        bus = self._bus_with(ReplacementContribution(source=EffectId("mithril_make"), value=2))
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=9).value == 2

    def test_a_replacement_and_a_delta_compose(self) -> None:
        bus = self._bus_with(ReplacementContribution(source=EffectId("mithril_make"), value=4))
        bus.register(
            numeric("cunning_make", -2, hook=Hook.MODIFY_ITEM_LOAD), EffectSource.acquired()
        )
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=9).value == 2

    def test_a_bool_is_not_a_replacement_value(self) -> None:
        # bool subclasses int, so this is worth pinning: True must not become Load 1.
        bus = self._bus_with(FlagContribution(source=EffectId("mithril_make"), flag="shiny"))
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=9).value == 9

    def test_a_non_int_replacement_is_left_to_a_direct_collector(self) -> None:
        bus = self._bus_with(
            ReplacementContribution(source=EffectId("mithril_make"), value="feather-light")
        )
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=9).value == 9

    def test_an_action_contribution_is_not_a_value(self) -> None:
        bus = self._bus_with(
            ActionContribution(source=EffectId("mithril_make"), action="glow", payload={})
        )
        assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx(), base=9).value == 9


class TestDeterminism:
    def test_collect_order_is_independent_of_registration_order(self) -> None:
        # 04.2.1: sorted by (priority, kind rank, id). Two runs with the same seed and
        # state must be byte-identical — the replay guarantee rests on this.
        ids = ["zeta", "alpha", "mu"]
        forwards, backwards = EffectBus(), EffectBus()
        for name in ids:
            forwards.register(numeric(name, 1), EffectSource.acquired(name))
        for name in reversed(ids):
            backwards.register(numeric(name, 1), EffectSource.acquired(name))

        def sources(bus: EffectBus) -> list[str]:
            return [str(c.source) for c in bus.collect(Hook.MODIFY_MAX_HOPE, ctx())]

        assert sources(forwards) == sources(backwards) == ["alpha", "mu", "zeta"]

    def test_kind_rank_orders_before_id(self) -> None:
        bus = EffectBus()
        blessing = build_effect(
            EffectId("zzz_blessing"),
            EffectKind.CULTURAL_BLESSING,
            "numeric_modifier",
            {"hook": "MODIFY_MAX_HOPE", "delta": 1},
        )
        curse = build_effect(
            EffectId("aaa_curse"),
            EffectKind.CURSE,
            "numeric_modifier",
            {"hook": "MODIFY_MAX_HOPE", "delta": -1},
        )
        bus.register(curse, EffectSource.acquired("c"))
        bus.register(blessing, EffectSource.culture("x"))
        # CULTURAL_BLESSING precedes CURSE in EffectKind, so it wins despite the later id.
        assert [str(c.source) for c in bus.collect(Hook.MODIFY_MAX_HOPE, ctx())] == [
            "zzz_blessing",
            "aaa_curse",
        ]


class TestPredicates:
    def test_an_environment_flag_gates_an_effect(self) -> None:
        bus = EffectBus()
        bus.register(
            numeric("dark_business", 2, hook=Hook.MODIFY_PARRY, predicate={"underground": True}),
            EffectSource.acquired(),
        )
        underground = ctx(environment=Environment(underground=True))
        assert bus.apply_numeric(Hook.MODIFY_PARRY, underground, base=12).value == 14
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx(), base=12).value == 12

    def test_any_of_matches_either_branch(self) -> None:
        # "+2 Parry when fighting underground or in cramped quarters" (04.6).
        effect = numeric(
            "cave_fighter",
            2,
            hook=Hook.MODIFY_PARRY,
            predicate={"any_of": [{"underground": True}, {"cramped": True}]},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        for environment in (Environment(underground=True), Environment(cramped=True)):
            assert (
                bus.apply_numeric(Hook.MODIFY_PARRY, ctx(environment=environment), base=12).value
                == 14
            )
        assert bus.apply_numeric(Hook.MODIFY_PARRY, ctx(), base=12).value == 12

    def test_all_of_requires_both(self) -> None:
        predicate = build_predicate({"all_of": [{"underground": True}, {"in_darkness": True}]})
        assert predicate is not None
        assert predicate(ctx(environment=Environment(underground=True, in_darkness=True)))
        assert not predicate(ctx(environment=Environment(underground=True)))

    def test_not_miserable_reads_the_actor(self) -> None:
        predicate = build_predicate({"not_miserable": True})
        assert predicate is not None
        hale = FakeCharacter()
        assert predicate(ctx(actor=hale))
        hale.conditions.miserable = True
        assert not predicate(ctx(actor=hale))

    def test_scene_and_purpose_predicates(self) -> None:
        scene_check = build_predicate({"scene": "combat"})
        purpose_check = build_predicate({"purpose": "valour_test"})
        assert scene_check is not None and purpose_check is not None
        assert scene_check(ctx(scene=SceneKind.COMBAT))
        assert not scene_check(ctx(scene=SceneKind.COUNCIL))
        assert not scene_check(ctx())
        assert purpose_check(ctx(purpose="valour_test"))

    def test_a_loremaster_flag_predicate(self) -> None:
        predicate = build_predicate({"flag": "flaw_applies"})
        assert predicate is not None
        assert predicate(ctx(extra={"flaw_applies": True}))
        assert not predicate(ctx(extra={}))

    def test_an_empty_predicate_is_no_predicate(self) -> None:
        assert build_predicate(None) is None
        assert build_predicate({}) is None

    def test_an_unknown_predicate_key_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="unknown predicate key"):
            build_predicate({"phase_of_the_moon": "waxing"})

    @pytest.mark.parametrize("key", ["weary", "wounded", "miserable"])
    def test_condition_predicates(self, key: str) -> None:
        predicate = build_predicate({key: True})
        assert predicate is not None
        actor = FakeCharacter()
        assert not predicate(ctx(actor=actor))
        setattr(actor.conditions, key, True)
        assert predicate(ctx(actor=actor))

    def test_ability_and_stance_predicates(self) -> None:
        ability_check = build_predicate({"ability": "lore"})
        stance_check = build_predicate({"stance": "forward"})
        assert ability_check is not None and stance_check is not None
        assert ability_check(ctx(ability=AbilityId("lore")))
        assert stance_check(ctx(stance="forward"))

    def test_the_environment_key_names_a_flag(self) -> None:
        predicate = build_predicate({"environment": "in_darkness"})
        assert predicate is not None
        assert predicate(ctx(environment=Environment(in_darkness=True)))
        assert not predicate(ctx(environment=DEFAULT_ENVIRONMENT))


class TestUsageBudgets:
    def make_bus(self, limit: object = 1) -> tuple[EffectBus, Effect]:
        effect = build_effect(
            EffectId("elbereth"),
            EffectKind.CULTURAL_VIRTUE,
            "grant_inspiration",
            {"usage": {"scope": "adventuring_phase", "limit": limit}},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        return bus, effect

    def test_collect_does_not_consume_the_budget(self) -> None:
        # 04.7: collection is pure. Only an explicit consume() spends a use.
        bus, _ = self.make_bus()
        for _ in range(5):
            assert bus.collect(Hook.MODIFY_INSPIRATION, ctx()) != []
        assert bus.uses(EffectId("elbereth"), UsageScope.ADVENTURING_PHASE) == 0

    def test_consuming_exhausts_the_budget(self) -> None:
        bus, _ = self.make_bus()
        bus.consume(EffectId("elbereth"), ctx())
        assert bus.collect(Hook.MODIFY_INSPIRATION, ctx()) == []

    def test_consuming_past_the_limit_is_refused(self) -> None:
        bus, _ = self.make_bus()
        bus.consume(EffectId("elbereth"), ctx())
        with pytest.raises(RuleViolation, match="no uses left"):
            bus.consume(EffectId("elbereth"), ctx())

    def test_a_limit_may_be_read_from_the_actor(self) -> None:
        # "a number of times per Adventuring Phase equal to your WISDOM".
        bus, _ = self.make_bus(limit="wisdom")
        actor = FakeCharacter(wisdom=3)
        for _ in range(3):
            bus.consume(EffectId("elbereth"), ctx(actor=actor))
        assert bus.collect(Hook.MODIFY_INSPIRATION, ctx(actor=actor)) == []

    def test_resetting_a_scope_refills_the_budget(self) -> None:
        bus, _ = self.make_bus()
        bus.consume(EffectId("elbereth"), ctx())
        bus.reset_scope(UsageScope.ADVENTURING_PHASE)
        assert bus.uses(EffectId("elbereth"), UsageScope.ADVENTURING_PHASE) == 0

    def test_resetting_another_scope_leaves_it_alone(self) -> None:
        bus, _ = self.make_bus()
        bus.consume(EffectId("elbereth"), ctx())
        bus.reset_scopes([UsageScope.ROUND, UsageScope.COMBAT])
        assert bus.uses(EffectId("elbereth"), UsageScope.ADVENTURING_PHASE) == 1

    def test_consuming_an_unbudgeted_effect_is_a_no_op(self) -> None:
        bus = EffectBus()
        bus.register(numeric("hardiness", 2), EffectSource.acquired())
        bus.consume(EffectId("hardiness"), ctx())

    def test_consuming_an_unregistered_effect_is_refused(self) -> None:
        with pytest.raises(RuleViolation, match="not registered"):
            EffectBus().consume(EffectId("nothing"), ctx())


class TestFactories:
    def test_numeric_modifier(self) -> None:
        bus = EffectBus()
        bus.register(
            numeric("hardiness", 2, hook=Hook.MODIFY_MAX_ENDURANCE), EffectSource.acquired()
        )
        assert bus.apply_numeric(Hook.MODIFY_MAX_ENDURANCE, ctx(), base=20).value == 22

    def test_greater_of_takes_the_larger(self) -> None:
        # 13.7.5: "+3, or your VALOUR, whichever is higher".
        effect = build_effect(
            EffectId("ancient_close_fitting"),
            EffectKind.ENCHANTED_REWARD,
            "greater_of",
            {"hook": "MODIFY_PROTECTION_ROLL", "flat": 3, "stat": "valour"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.item("mail#1"))
        modest = bus.apply_numeric(
            Hook.MODIFY_PROTECTION_ROLL, ctx(actor=FakeCharacter(valour=2)), base=0
        )
        renowned = bus.apply_numeric(
            Hook.MODIFY_PROTECTION_ROLL, ctx(actor=FakeCharacter(valour=5)), base=0
        )
        assert modest.value == 3
        assert renowned.value == 5

    def test_favour_rolls_matches_by_purpose(self) -> None:
        effect = build_effect(
            EffectId("stout_hearted"),
            EffectKind.CULTURAL_BLESSING,
            "favour_rolls",
            {"purpose": "valour_test"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.culture("example_folk"))
        matched = bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(purpose="valour_test"))
        assert [c.value for c in matched if isinstance(c, FlagContribution)] == ["stout_hearted"]
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(purpose="skill")) == []

    def test_favour_rolls_may_name_abilities_instead(self) -> None:
        effect = build_effect(
            EffectId("hobbit_sense"),
            EffectKind.CULTURAL_BLESSING,
            "favour_rolls",
            {"abilities": ["wisdom"]},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.culture("x"))
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(ability=AbilityId("wisdom"))) != []
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(ability=AbilityId("lore"))) == []

    def test_bonus_dice_can_key_off_a_shadow_source(self) -> None:
        effect = build_effect(
            EffectId("untameable_spirit"),
            EffectKind.CULTURAL_VIRTUE,
            "bonus_dice",
            {"purpose": "shadow_test", "source": "sorcery", "dice": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        against_sorcery = ctx(purpose="shadow_test", extra={"source": "sorcery"})
        against_dread = ctx(purpose="shadow_test", extra={"source": "dread"})
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, against_sorcery) != []
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, against_dread) == []

    def test_ill_favour_when_applicable_needs_the_loremasters_ruling(self) -> None:
        # 11.7: a Flaw is never automatic, because plausibility cannot be computed.
        effect = build_effect(EffectId("idle"), EffectKind.FLAW, "ill_favour_when_applicable", {})
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx()) == []
        ruled = bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(extra={"flaw_applies": True}))
        assert [c.flag for c in ruled if isinstance(c, FlagContribution)] == ["ill_favoured"]

    def test_modify_piercing_threshold_is_a_replacement(self) -> None:
        effect = build_effect(
            EffectId("keen"), EffectKind.REWARD, "modify_piercing_threshold", {"threshold": 9}
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.item("blade#1"))
        (contribution,) = bus.collect(Hook.PIERCING_THRESHOLD, ctx())
        assert isinstance(contribution, ReplacementContribution)
        assert contribution.value == 9

    def test_a_piercing_threshold_may_be_computed_from_valour(self) -> None:
        # "10 minus your VALOUR" against a Bane creature (13.7.5).
        effect = build_effect(
            EffectId("superior_keen"),
            EffectKind.ENCHANTED_REWARD,
            "modify_piercing_threshold",
            {"minus_stat": "valour"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.item("blade#1"))
        (contribution,) = bus.collect(Hook.PIERCING_THRESHOLD, ctx(actor=FakeCharacter(valour=4)))
        assert isinstance(contribution, ReplacementContribution)
        assert contribution.value == 6

    def test_secondary_action_task_is_gated_by_stance(self) -> None:
        effect = build_effect(
            EffectId("swift_guard"),
            EffectKind.CULTURAL_VIRTUE,
            "secondary_action_task",
            {"task": "protect_companion", "stance": "defensive"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        offered = bus.collect(Hook.SECONDARY_ACTION_OPTIONS, ctx(stance="defensive"))
        assert [c.payload["task"] for c in offered if isinstance(c, ActionContribution)] == [
            "protect_companion"
        ]
        assert bus.collect(Hook.SECONDARY_ACTION_OPTIONS, ctx(stance="forward")) == []

    def test_spend_drive_for_is_offered_not_applied(self) -> None:
        # 12.6: the Loremaster decides whether to pay, so it arrives as an action.
        effect = build_effect(
            EffectId("snake_like_speed"),
            EffectKind.FELL_ABILITY,
            "spend_drive_for",
            {"cost": 1, "trigger": "targeted_by_attack", "grant": {"ill_favoured": True}},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        (contribution,) = bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx())
        assert isinstance(contribution, ActionContribution)
        assert contribution.payload["cost"] == 1

    def test_drain_drive_on_hit_scales_with_icons(self) -> None:
        effect = build_effect(
            EffectId("gleam_of_wrath"),
            EffectKind.ENCHANTED_REWARD,
            "drain_drive_on_hit",
            {"amount": 1, "per_icon": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.item("blade#1"))
        (contribution,) = bus.collect(Hook.ON_ATTACK_HIT, ctx(extra={"icons": 2}))
        assert isinstance(contribution, ActionContribution)
        assert contribution.payload["amount"] == 3

    def test_shadow_on_hit_carries_its_rider(self) -> None:
        effect = build_effect(
            EffectId("strike_fear"),
            EffectKind.FELL_ABILITY,
            "shadow_on_hit",
            {"points": 3, "source": "dread", "on_fail": "cannot_spend_hope"},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        (contribution,) = bus.collect(Hook.ON_ATTACK_HIT, ctx())
        assert isinstance(contribution, ActionContribution)
        assert contribution.payload == {
            "points": 3,
            "source": "dread",
            "on_fail": "cannot_spend_hope",
        }

    def test_make_favoured_uses_the_choice_captured_at_acquisition(self) -> None:
        # Mastery: one shared definition, made personal by params (04.1).
        effect = build_effect(
            EffectId("mastery"),
            EffectKind.VIRTUE,
            "make_favoured",
            {"skills": ["lore", "riddle"]},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(ability=AbilityId("lore"))) != []
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(ability=AbilityId("battle"))) == []

    def test_an_unknown_factory_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="unknown effect factory"):
            build_effect(EffectId("x"), EffectKind.VIRTUE, "teleportation", {})

    def test_an_unknown_kind_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="unknown effect kind"):
            build_effect(EffectId("x"), "sorcery", "numeric_modifier", {"hook": "MODIFY_PARRY"})

    def test_an_unknown_hook_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="unknown hook"):
            build_effect(
                EffectId("x"), EffectKind.VIRTUE, "numeric_modifier", {"hook": "MODIFY_LUCK"}
            )

    def test_a_missing_hook_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="needs a 'hook'"):
            build_effect(EffectId("x"), EffectKind.VIRTUE, "numeric_modifier", {"delta": 1})

    def test_an_invalid_usage_scope_is_a_content_error(self) -> None:
        with pytest.raises(ContentError, match="invalid usage scope"):
            build_effect(
                EffectId("x"),
                EffectKind.VIRTUE,
                "grant_inspiration",
                {"usage": {"scope": "fortnight"}},
            )


class TestBuildRequest:
    def test_folds_effect_dice_into_the_request(self) -> None:
        actor = FakeCharacter(ratings={"lore": 2})
        actor.effects.register(
            build_effect(
                EffectId("lore_bonus"),
                EffectKind.VIRTUE,
                "bonus_dice",
                {"purpose": "skill", "dice": 1},
            ),
            EffectSource.acquired(),
        )
        request = build_request(
            actor,
            AbilityId("lore"),
            bus=actor.effects,
            target_number=14,
            purpose=RollPurpose.SKILL,
        )
        assert request.rating == 2
        assert request.bonus_dice == 1
        assert request.dice_count == 3

    def test_a_favoured_source_reaches_the_policy(self) -> None:
        actor = FakeCharacter(ratings={"valour": 2})
        actor.effects.register(
            build_effect(
                EffectId("stout_hearted"),
                EffectKind.CULTURAL_BLESSING,
                "favour_rolls",
                {"purpose": "valour_test"},
            ),
            EffectSource.culture("example_folk"),
        )
        request = build_request(
            actor, AbilityId("valour"), bus=actor.effects, purpose=RollPurpose.VALOUR_TEST
        )
        assert request.favoured_sources == ("stout_hearted",)
        assert request.policy is FeatDicePolicy.FAVOURED

    def test_hope_grants_one_die_and_two_while_inspired(self) -> None:
        plain = FakeCharacter()
        assert build_request(plain, None, bus=plain.effects, spend_hope=True).bonus_dice == 1

        inspired = FakeCharacter()
        inspired.effects.register(
            build_effect(
                EffectId("brave_at_a_pinch"), EffectKind.CULTURAL_VIRTUE, "grant_inspiration", {}
            ),
            EffectSource.acquired(),
        )
        assert build_request(inspired, None, bus=inspired.effects, spend_hope=True).bonus_dice == 2

    def test_a_focused_supporter_replaces_rather_than_stacks(self) -> None:
        actor = FakeCharacter()
        ordinary = build_request(actor, None, bus=actor.effects, support=Support(rating=2))
        focused = build_request(
            actor, None, bus=actor.effects, support=Support(rating=2, is_focus=True)
        )
        assert ordinary.bonus_dice == 1
        assert focused.bonus_dice == 2

    def test_an_unskilled_supporter_is_refused(self) -> None:
        actor = FakeCharacter()
        with pytest.raises(RuleViolation, match="at least rank 1"):
            build_request(actor, None, bus=actor.effects, support=Support(rating=0))

    def test_unapproved_support_is_refused(self) -> None:
        actor = FakeCharacter()
        with pytest.raises(RuleViolation, match="approval"):
            build_request(actor, None, bus=actor.effects, support=Support(approved=False))

    def test_a_useful_item_adds_one_die(self) -> None:
        actor = FakeCharacter()
        assert build_request(actor, None, bus=actor.effects, useful_item=True).bonus_dice == 1

    def test_magical_success_requires_an_eligible_effect(self) -> None:
        # 02.3.7: only legal when an effect grants it for that specific ability.
        actor = FakeCharacter()
        with pytest.raises(RuleViolation, match="magical success"):
            build_request(actor, AbilityId("lore"), bus=actor.effects, magical_success=True)

    def test_magical_success_is_permitted_by_an_eligible_effect(self) -> None:
        actor = FakeCharacter()
        actor.effects.register(
            build_effect(
                EffectId("elven_blessing"),
                EffectKind.CULTURAL_BLESSING,
                "enable_magical_success",
                {"abilities": ["lore"]},
            ),
            EffectSource.culture("elves"),
        )
        request = build_request(
            actor, AbilityId("lore"), bus=actor.effects, magical_success=True, target_number=99
        )
        assert request.magical_success

    def test_an_explicit_rating_overrides_the_characters(self) -> None:
        # Brawling and PROTECTION rolls supply their dice count directly.
        actor = FakeCharacter(ratings={"swords": 1})
        assert build_request(actor, None, bus=actor.effects, rating=3).rating == 3

    def test_flags_pass_straight_through(self) -> None:
        actor = FakeCharacter()
        request = build_request(
            actor,
            None,
            bus=actor.effects,
            weary=True,
            eye_is_auto_failure=True,
            icon_inverted=True,
            penalty_dice=2,
        )
        assert request.weary and request.eye_is_auto_failure and request.icon_inverted
        assert request.penalty_dice == 2


class TestRemainingBranches:
    """The paths the behavioural tests above do not reach."""

    def test_a_condition_source_is_addressable(self) -> None:
        # 11.2: "Ill-favoured on every roll" is registered as a condition-sourced effect,
        # so crossing back below the threshold unregisters it cleanly.
        bus = EffectBus()
        source = EffectSource.condition("overburdened_by_shadow")
        bus.register(numeric("overburdened", 0), source)
        assert source.kind == "condition"
        bus.unregister_source(source)
        assert not bus.registered(EffectId("overburdened"))

    def test_a_listener_may_return_several_contributions(self) -> None:
        # 12.6: one drive spend granting two contributions at once — "+1d *and* make the
        # attack roll Favoured".
        def listen(_: HookContext) -> list[NumericContribution]:
            return [
                NumericContribution(source=EffectId("double_a"), delta=1),
                NumericContribution(source=EffectId("double"), delta=2),
            ]

        bus = EffectBus()
        bus.register(
            Effect(
                id=EffectId("double"),
                kind=EffectKind.FELL_ABILITY,
                listeners={Hook.MODIFY_ROLL_REQUEST: listen},
            ),
            EffectSource.acquired(),
        )
        assert len(bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx())) == 2

    def test_a_listener_may_decline_by_returning_none(self) -> None:
        bus = EffectBus()
        bus.register(
            Effect(
                id=EffectId("quiet"),
                kind=EffectKind.VIRTUE,
                listeners={Hook.MODIFY_ROLL_REQUEST: lambda _: None},
            ),
            EffectSource.acquired(),
        )
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx()) == []

    def test_bonus_dice_without_a_purpose_filter_always_applies(self) -> None:
        effect = build_effect(EffectId("always"), EffectKind.VIRTUE, "bonus_dice", {"dice": 1})
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(purpose="anything")) != []

    def test_bonus_dice_with_a_purpose_but_no_source_filter(self) -> None:
        effect = build_effect(
            EffectId("dread_ward"),
            EffectKind.CULTURAL_VIRTUE,
            "bonus_dice",
            {"purpose": "shadow_test", "dice": 1},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(purpose="shadow_test")) != []
        assert bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx(purpose="skill")) == []

    def test_magical_success_may_be_granted_for_every_ability(self) -> None:
        # The Elven blessing applies to Skill rolls generally, not a named list (02.3.7).
        effect = build_effect(
            EffectId("elven_blessing"),
            EffectKind.CULTURAL_BLESSING,
            "enable_magical_success",
            {},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.culture("elves"))
        assert bus.collect(Hook.MAGICAL_SUCCESS_ELIGIBLE, ctx(ability=AbilityId("song"))) != []

    def test_an_ill_favoured_source_reaches_the_request(self) -> None:
        # A Flaw the Loremaster has ruled applicable makes the whole roll Ill-favoured.
        actor = FakeCharacter()
        actor.effects.register(
            build_effect(EffectId("idle"), EffectKind.FLAW, "ill_favour_when_applicable", {}),
            EffectSource.acquired(),
        )
        request = build_request(
            actor, AbilityId("travel"), bus=actor.effects, extra={"flaw_applies": True}
        )
        assert request.ill_favoured_sources == ("idle",)
        assert request.policy is FeatDicePolicy.ILL_FAVOURED

    def test_a_flaw_and_a_blessing_cancel_to_normal(self) -> None:
        # 02.3.2 end to end: the two sides cancel regardless of counts.
        actor = FakeCharacter()
        actor.effects.register(
            build_effect(EffectId("idle"), EffectKind.FLAW, "ill_favour_when_applicable", {}),
            EffectSource.acquired(),
        )
        actor.effects.register(
            build_effect(
                EffectId("stout_hearted"), EffectKind.CULTURAL_BLESSING, "favour_rolls", {}
            ),
            EffectSource.culture("x"),
        )
        request = build_request(
            actor, AbilityId("travel"), bus=actor.effects, extra={"flaw_applies": True}
        )
        assert request.favoured_sources and request.ill_favoured_sources
        assert request.policy is FeatDicePolicy.NORMAL

    def test_an_action_contribution_is_ignored_by_the_request_builder(self) -> None:
        # build_request folds numbers and flags; offered actions are the caller's business.
        actor = FakeCharacter()
        actor.effects.register(
            build_effect(
                EffectId("snake_like_speed"),
                EffectKind.FELL_ABILITY,
                "spend_drive_for",
                {"cost": 1},
            ),
            EffectSource.acquired(),
        )
        request = build_request(actor, None, bus=actor.effects)
        assert request.bonus_dice == 0
        assert request.policy is FeatDicePolicy.NORMAL

    def test_a_restricted_magical_success_declines_other_abilities(self) -> None:
        effect = build_effect(
            EffectId("riddle_lore"),
            EffectKind.CULTURAL_VIRTUE,
            "enable_magical_success",
            {"abilities": ["riddle"]},
        )
        bus = EffectBus()
        bus.register(effect, EffectSource.acquired())
        assert bus.collect(Hook.MAGICAL_SUCCESS_ELIGIBLE, ctx(ability=AbilityId("riddle"))) != []
        assert bus.collect(Hook.MAGICAL_SUCCESS_ELIGIBLE, ctx(ability=AbilityId("song"))) == []

    def test_an_unrelated_flag_is_passed_over_by_the_request_builder(self) -> None:
        # Only "favoured" and "ill_favoured" steer the die policy; other flags — Inspired,
        # "you never gain Fatigue" — are read by their own consumers elsewhere.
        def listen(_: HookContext) -> FlagContribution:
            return FlagContribution(source=EffectId("cram"), flag="never_gain_fatigue")

        actor = FakeCharacter()
        actor.effects.register(
            Effect(
                id=EffectId("cram"),
                kind=EffectKind.CULTURAL_VIRTUE,
                listeners={Hook.MODIFY_ROLL_REQUEST: listen},
            ),
            EffectSource.acquired(),
        )
        request = build_request(actor, None, bus=actor.effects)
        assert request.favoured_sources == () and request.ill_favoured_sources == ()
