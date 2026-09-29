"""The branches the worked examples do not reach (spec 08, 12).

Each of these is a real rule with a real consequence — the target-side stance table, an
adversary's flat Pierce, a PROTECTION roll made Favoured or condition-proof, an
interceptor the creature cannot pay for — that simply does not come up while asserting
the mandatory vectors. 19 holds `tor.rules` to full branch coverage precisely so they get
written down rather than assumed.
"""

from __future__ import annotations

from conftest import BRUTE, LURKER, MINION, RUFFIAN, build_adversary, build_hero
from test_combat_attack import blade_form, duel, strike, swing

from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import Effect, EffectKind, EffectSource
from tor.effects.hooks import (
    ActionContribution,
    FlagContribution,
    Hook,
    NumericContribution,
)
from tor.model.ids import EffectId
from tor.rules.combat.attack import (
    ADVERSARY_PIERCE,
    SpecialDamage,
    SpendPlan,
)
from tor.rules.combat.engagement import EngagementPlan, may_engage, resolve_engagement
from tor.rules.combat.sequence import begin_combat, begin_round, end_round
from tor.rules.combat.state import Stance


def effect_on(f, ref, effect_id, hook, contribution, source=None):
    f.buses[ref].register(
        Effect(
            id=EffectId(effect_id),
            kind=EffectKind.CULTURAL_VIRTUE,
            listeners={hook: lambda _c: contribution},
        ),
        source or EffectSource.acquired("cultural_virtue"),
    )


class TestTargetSideStance:
    def test_a_forward_hero_is_easier_to_hit(self, fight) -> None:
        # 08.2.3's second column: Forward gives close attacks against you +1d.
        f, hero, foe = duel(fight, stance=Stance.FORWARD)
        strike(f, foe, hero, successes=(4, 4, 4, 4))

    def test_a_defensive_hero_is_harder_to_hit(self, fight) -> None:
        f, hero, foe = duel(fight, stance=Stance.DEFENSIVE)
        strike(f, foe, hero, successes=(4, 4))

    def test_an_open_hero_is_neither(self, fight) -> None:
        f, hero, foe = duel(fight, stance=Stance.OPEN)
        strike(f, foe, hero, successes=(4, 4, 4))


class TestAttackRollEdges:
    def test_a_missed_attack_reports_no_icons(self, fight) -> None:
        # The budget an outcome carries is about what may be *spent*, and a miss spends
        # nothing however the dice fell. 08.5: the Miserable rule is the only thing in
        # combat that resembles a fumble, and it is the cleanest way to miss on three
        # icons.
        f, hero, foe = duel(fight)
        hero.conditions.miserable = True
        attack, _ = swing(f, hero, foe, feats=(FeatFace.EYE,), successes=(6, 6, 6))
        assert not attack.hit
        assert attack.roll.icons == 3
        assert attack.icons == 0

    def test_a_hit_reports_the_icons_rolled(self, fight) -> None:
        f, hero, foe = duel(fight)
        attack, _ = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert attack.icons == 3

    def test_an_adversary_pierces_by_a_flat_two(self, fight) -> None:
        # 12.5: a flat value, unlike the hero version which varies by weapon.
        f, hero, foe = duel(fight, template=BRUTE)
        _, outcome = strike(
            f,
            foe,
            hero,
            feats=(8,),
            successes=(6, 6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.PIERCE,)),
        )
        assert outcome.special_damage[0].amount == ADVERSARY_PIERCE
        assert outcome.piercing_blow, "8 plus a flat 2 clears the threshold"

    def test_a_prepared_shot_only_helps_a_ranged_attack(self, fight) -> None:
        f, hero, foe = duel(fight)
        f.state.combatant(hero.id).round_flags.prepared_shot = 2
        swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))


class TestProtectionProfile:
    def test_a_cultural_virtue_may_make_the_roll_favoured(self, fight) -> None:
        # 08.7, conditionally on not being Miserable — the predicate is the pack's.
        f, hero, foe = duel(fight)
        effect_on(
            f,
            hero.id,
            "stone_hard",
            Hook.MODIFY_PROTECTION_ROLL,
            FlagContribution(source=EffectId("stone_hard"), flag="favoured", value="stone_hard"),
        )
        _, outcome = strike(f, foe, hero, prot_feats=(1, 1, FeatFace.RUNE))
        assert outcome.protection.roll.request.favoured_sources == ("stone_hard",)

    def test_enchanted_armour_may_ignore_weary_and_miserable(self, fight) -> None:
        # 08.7: "Enchanted armour can make the roll ignore Miserable and Weary."
        f, hero, foe = duel(fight)
        hero.conditions.weary = True
        hero.conditions.miserable = True
        effect_on(
            f,
            hero.id,
            "rune_scored",
            Hook.MODIFY_PROTECTION_ROLL,
            FlagContribution(source=EffectId("rune_scored"), flag="ignore_conditions"),
            source=EffectSource.item(hero.gear.armour.ref),
        )
        _, outcome = strike(f, foe, hero)
        assert not outcome.protection.roll.request.weary
        assert not outcome.protection.roll.request.eye_is_auto_failure

    def test_without_it_the_conditions_bite(self, fight) -> None:
        f, hero, foe = duel(fight)
        hero.conditions.weary = True
        hero.conditions.miserable = True
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.weary
        assert outcome.protection.roll.request.eye_is_auto_failure


class TestInterceptorEdges:
    def test_an_interceptor_with_the_wrong_trigger_does_not_fire(self, fight) -> None:
        f, hero, foe = duel(fight, template=BRUTE)
        effect_on(
            f,
            foe.instance_id,
            "mistimed",
            Hook.DAMAGE_INTERCEPT,
            ActionContribution(
                source=EffectId("mistimed"),
                action="spend_drive",
                payload={"trigger": "on_turn", "cost": 0, "grant": {}},
            ),
        )
        foe.endurance = 2
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        assert outcome.intercepted_by is None

    def test_one_the_creature_cannot_pay_for_does_not_fire(self, fight) -> None:
        f, hero, foe = duel(fight, template=BRUTE)
        effect_on(
            f,
            foe.instance_id,
            "costly",
            Hook.DAMAGE_INTERCEPT,
            ActionContribution(
                source=EffectId("costly"),
                action="spend_drive",
                payload={"trigger": "would_reach_zero", "cost": 99, "grant": {}},
            ),
        )
        foe.endurance = 2
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        assert outcome.intercepted_by is None

    def test_a_non_action_contribution_on_the_hook_is_ignored(self, fight) -> None:
        # DAMAGE_INTERCEPT and WOUND_INTERCEPT are the only two hooks that may veto a
        # state change, so they are kept narrow: anything that is not an offered action
        # is not an interception (12.6).
        f, hero, foe = duel(fight, template=BRUTE)
        effect_on(
            f,
            foe.instance_id,
            "just_a_number",
            Hook.DAMAGE_INTERCEPT,
            NumericContribution(source=EffectId("just_a_number"), delta=5),
        )
        foe.endurance = 2
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        assert outcome.intercepted_by is None

    def test_the_same_holds_for_a_wound_interception(self, fight) -> None:
        f, hero, foe = duel(fight, template=LURKER)
        effect_on(
            f,
            foe.instance_id,
            "just_a_flag",
            Hook.WOUND_INTERCEPT,
            FlagContribution(source=EffectId("just_a_flag"), flag="nope"),
        )
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.cancelled_by is None

    def test_a_bane_against_another_creature_type_does_not_suppress_it(self, fight) -> None:
        from tor.model.conditions import CreatureType

        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "deathless", EffectSource.acquired("fell_ability"))
        hero.gear.weapons[0].banes = frozenset({CreatureType.TROLLS})
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.cancelled_by == "deathless"


class TestSpecialDamageOptionsEdges:
    def test_a_non_action_contribution_grants_nothing(self, fight) -> None:
        from tor.rules.combat.attack import offered_special_damage

        f, hero, _foe = duel(fight)
        effect_on(
            f,
            hero.id,
            "noise",
            Hook.SPECIAL_DAMAGE_OPTIONS,
            NumericContribution(source=EffectId("noise"), delta=1),
        )
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert SpecialDamage.BREAK_SHIELD not in offered


class TestEngagementEdges:
    def test_a_full_engager_cannot_take_another(self, fight) -> None:
        # The limit binds on both sides of the pair, not only on the target.
        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.FORWARD)
        foes = [f.foe(build_adversary(f.pack, BRUTE, f"t{n}")) for n in range(3)]
        resolve_engagement(
            f.state,
            EngagementPlan(pairs=tuple((hero.id, t.instance_id) for t in foes[:2])),
            ctx=f.ctx,
        )
        assert not may_engage(f.state, hero.id, foes[2].instance_id, ctx=f.ctx)

    def test_a_stance_options_contribution_that_is_not_an_action_is_ignored(self, fight) -> None:
        from tor.rules.combat.tasks import CombatTask, TaskDeclaration, resolve_task

        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.FORWARD)
        foe = f.foe(build_adversary(f.pack, MINION))
        effect_on(
            f,
            foe.instance_id,
            "murmur",
            Hook.STANCE_OPTIONS,
            NumericContribution(source=EffectId("murmur"), delta=1),
        )
        outcome = resolve_task(
            f.state,
            TaskDeclaration(actor=hero.id, task=CombatTask.INTIMIDATE_FOE),
            ctx=f.ctx,
            rng=ScriptedRandomness(feats=[10], successes=[4, 4]),
        )
        assert outcome.affected == (foe.instance_id,)


class TestRoundHookEdges:
    def test_a_fallen_combatant_gets_no_round_hooks(self, fight) -> None:
        f, hero, foe = duel(fight)
        begin_combat(f.state, ctx=f.ctx)
        foe.apply_wound()
        hero.endurance = 0
        # Both sides are out, so nothing fires and nothing raises.
        end_round(f.state, ctx=f.ctx)

    def test_a_round_start_hook_fires_for_the_living(self, fight) -> None:
        f, _hero, foe = duel(fight)
        f.register(foe.instance_id, "flees_when_broken", EffectSource.acquired("fell_ability"))
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)

    def test_a_round_end_hook_fires_for_the_living(self, fight) -> None:
        f, _hero, foe = duel(fight, template=RUFFIAN)
        f.register(foe.instance_id, "guttering_courage", EffectSource.acquired("fell_ability"))
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        end_round(f.state, ctx=f.ctx)


class TestHeroStanceReadback:
    def test_a_heros_effective_stance_is_their_own(self, fight) -> None:
        f, hero, _ = duel(fight, stance=Stance.DEFENSIVE)
        combatant = f.state.combatant(hero.id)
        assert combatant.effective_stance(f.state) is Stance.DEFENSIVE


class TestContributionShapes:
    """Every hook here reads one shape of contribution and must ignore the others.

    04.2.2 lets any effect put any of the four on any hook, so a listener that assumed
    its own shape would crash on a pack that is merely unusual rather than wrong.
    """

    def test_the_protection_hooks_ignore_a_non_flag(self, fight) -> None:
        f, hero, foe = duel(fight)
        effect_on(
            f,
            foe.instance_id,
            "noise",
            Hook.MODIFY_TARGET_PROTECTION_ROLL,
            ActionContribution(source=EffectId("noise"), action="nothing"),
        )
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.ill_favoured_sources == ()

    def test_and_a_flag_they_do_not_know(self, fight) -> None:
        f, hero, foe = duel(fight)
        effect_on(
            f,
            foe.instance_id,
            "murmur",
            Hook.ON_PIERCING_BLOW,
            FlagContribution(source=EffectId("murmur"), flag="unrelated"),
        )
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.ill_favoured_sources == ()

    def test_wound_if_ill_favoured_alone_is_not_an_automatic_wound(self, fight) -> None:
        # 08.7: Foe-slaying converts an *already* Ill-favoured roll. With nothing making
        # it Ill-favoured there is nothing to convert, so the roll still happens.
        f, hero, foe = duel(fight)
        effect_on(
            f,
            foe.instance_id,
            "foe_slaying",
            Hook.MODIFY_TARGET_PROTECTION_ROLL,
            FlagContribution(source=EffectId("foe_slaying"), flag="wound_if_ill_favoured"),
        )
        _, outcome = strike(f, foe, hero)
        assert not outcome.protection.automatic
        assert outcome.protection.roll is not None

    def test_the_protection_profile_ignores_a_flag_it_does_not_know(self, fight) -> None:
        f, hero, foe = duel(fight)
        effect_on(
            f,
            hero.id,
            "gleam",
            Hook.MODIFY_PROTECTION_ROLL,
            FlagContribution(source=EffectId("gleam"), flag="unrelated"),
        )
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.favoured_sources == ()
        assert outcome.protection.modifier == 0

    def test_the_severity_roll_ignores_a_non_flag(self, fight) -> None:
        f, hero, foe = duel(fight)
        effect_on(
            f,
            hero.id,
            "chatter",
            Hook.MODIFY_WOUND_SEVERITY_ROLL,
            ActionContribution(source=EffectId("chatter"), action="nothing"),
        )
        _, outcome = strike(f, foe, hero, severity_feat=7)
        assert outcome.wound.roll.request.favoured_sources == ()
        assert outcome.wound.roll.request.ill_favoured_sources == ()

    def test_rearward_ignores_a_stance_flag_it_does_not_know(self, fight) -> None:
        import pytest

        from tor.errors import RuleViolation
        from tor.rules.combat.sequence import set_stance

        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.OPEN)
        f.foe(build_adversary(f.pack, MINION))
        effect_on(
            f,
            hero.id,
            "murmur",
            Hook.STANCE_OPTIONS,
            FlagContribution(source=EffectId("murmur"), flag="unrelated"),
        )
        with pytest.raises(RuleViolation, match="may not take Rearward"):
            set_stance(f.state, hero.id, Stance.REARWARD, ctx=f.ctx)

    def test_a_secondary_action_grant_for_another_task_does_not_apply(self, fight) -> None:
        import pytest

        from tor.errors import RuleViolation
        from tor.rules.combat.tasks import CombatTask, TaskDeclaration, resolve_task

        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.FORWARD)
        f.foe(build_adversary(f.pack, MINION))
        f.register(hero.id, "example_cv_guard", EffectSource.acquired("cultural_virtue"))
        with pytest.raises(RuleViolation, match="nothing converts"):
            resolve_task(
                f.state,
                TaskDeclaration(actor=hero.id, task=CombatTask.INTIMIDATE_FOE, as_secondary=True),
                ctx=f.ctx,
                rng=ScriptedRandomness(),
            )


class TestFallenCombatants:
    def test_an_illegal_pair_in_a_plan_names_the_reason(self, fight) -> None:
        import pytest

        from tor.errors import RuleViolation

        f = fight()
        a = f.hero(build_hero("a"), stance=Stance.FORWARD)
        b = f.hero(build_hero("b"), stance=Stance.FORWARD)
        f.foe(build_adversary(f.pack, MINION))
        with pytest.raises(RuleViolation, match="cannot engage"):
            resolve_engagement(f.state, EngagementPlan(pairs=((a.id, b.id),)), ctx=f.ctx)

    def test_a_fallen_combatant_in_a_plan_is_refused(self, fight) -> None:
        import pytest

        from tor.errors import RuleViolation

        f = fight()
        hero = f.hero(build_hero("a"), stance=Stance.FORWARD)
        foe = f.foe(build_adversary(f.pack, MINION))
        foe.apply_wound()
        with pytest.raises(RuleViolation, match="cannot engage"):
            resolve_engagement(
                f.state, EngagementPlan(pairs=((hero.id, foe.instance_id),)), ctx=f.ctx
            )

    def test_the_round_skips_a_combatant_who_is_out(self, fight) -> None:
        f = fight()
        standing = f.hero(build_hero("a"))
        fallen = f.hero(build_hero("b", endurance=0))
        f.foe(build_adversary(f.pack, MINION))
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert not f.state.combatant(fallen.id).active
        assert standing.id in {c.ref for c in f.state.active_heroes()}


class TestSecondaryActionGrants:
    def test_a_grant_for_this_task_converts_it(self, fight) -> None:
        # 08.10 and 04.3.3: the Cultural Virtue names both the task and the stance, so the
        # hook context has to carry the stance for it to see one.
        from tor.rules.combat.tasks import CombatTask, TaskDeclaration, apply_task, resolve_task

        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.DEFENSIVE)
        ally = f.hero(build_hero("a"), stance=Stance.FORWARD)
        f.foe(build_adversary(f.pack, MINION))
        f.register(hero.id, "example_cv_guard", EffectSource.acquired("cultural_virtue"))

        outcome = resolve_task(
            f.state,
            TaskDeclaration(
                actor=hero.id,
                task=CombatTask.PROTECT_COMPANION,
                companion=ally.id,
                as_secondary=True,
            ),
            ctx=f.ctx,
            rng=ScriptedRandomness(feats=[10], successes=[4, 4]),
        )
        apply_task(f.state, outcome, ctx=f.ctx)
        assert f.state.combatant(hero.id).secondary_action_used

    def test_a_grant_for_another_task_does_not(self, fight) -> None:
        import pytest

        from tor.errors import RuleViolation
        from tor.rules.combat.tasks import CombatTask, TaskDeclaration, resolve_task

        f = fight()
        hero = f.hero(build_hero("h"), stance=Stance.OPEN)
        f.foe(build_adversary(f.pack, MINION))
        effect_on(
            f,
            hero.id,
            "swift_shot",
            Hook.SECONDARY_ACTION_OPTIONS,
            ActionContribution(
                source=EffectId("swift_shot"),
                action="secondary_action",
                payload={"task": "prepare_shot"},
            ),
        )
        with pytest.raises(RuleViolation, match="nothing converts"):
            resolve_task(
                f.state,
                TaskDeclaration(actor=hero.id, task=CombatTask.RALLY_COMRADES, as_secondary=True),
                ctx=f.ctx,
                rng=ScriptedRandomness(),
            )


class TestAdversaryRating:
    def test_it_answers_with_the_best_attack_form(self, fight, pack) -> None:
        # 12.1: an adversary has attack forms, not abilities. This is what makes an
        # AdversaryInstance satisfy RollingCharacter (01.5), so one roll pipeline serves
        # both sheets.
        from tor.model.ids import AbilityId

        foe = build_adversary(pack, MINION)
        assert foe.rating(AbilityId("swords")) == max(a.rating for a in foe.template.attacks)

    def test_a_creature_with_no_attacks_rates_zero(self, pack) -> None:
        from dataclasses import replace

        from tor.model.adversary import AdversaryInstance
        from tor.model.ids import AbilityId, AdversaryInstanceId

        unarmed = replace(pack.stat_block(MINION), attacks=())
        instance = AdversaryInstance.of(unarmed, AdversaryInstanceId("quiet"))
        assert instance.rating(AbilityId("swords")) == 0
