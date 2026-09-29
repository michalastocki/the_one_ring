"""Piercing Blows, PROTECTION, Wounds and knockback (spec 08.7-08.9, 08.12, 12.3, 12.6).

The half of the attack pipeline that runs *after* the icons are spent, and where four of
19.4's mandatory vectors live: PROTECTION timing, knockback rounding, the Wound Severity
table, and a Might 2 creature surviving its first Wound.
"""

from __future__ import annotations

import pytest
from conftest import (
    BRUTE,
    HELM,
    LURKER,
    MAIL,
    MINION,
    RUFFIAN,
)
from test_combat_attack import blade_form, duel, strike, swing

from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.errors import StateError
from tor.events import EventKind
from tor.model.conditions import CreatureType
from tor.model.gear import WeaponInstance
from tor.model.ids import EffectId
from tor.rules.combat.attack import (
    SpecialDamage,
    SpendPlan,
    WoundSeverity,
    adversary_attack_form,
    apply_attack,
    resolve_attack,
    roll_attack,
)
from tor.rules.resources import recompute_load


class TestPiercingBlow:
    def test_a_ten_scores_one(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.piercing_blow

    def test_a_nine_does_not(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = swing(f, hero, foe, feats=(9,), successes=(6, 6, 6))
        assert not outcome.piercing_blow

    def test_the_auto_success_face_always_qualifies(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = swing(f, hero, foe, feats=(FeatFace.RUNE,), successes=(6, 6, 6))
        assert outcome.piercing_blow

    def test_an_effect_lowers_the_threshold(self, fight) -> None:
        # 08.7: one Reward makes it 9+, and it flows through PIERCING_THRESHOLD.
        f, hero, foe = duel(fight)
        f.register(hero.id, "keen", EffectSource.item(hero.gear.weapons[0].ref))
        _, outcome = swing(f, hero, foe, feats=(9,), successes=(6, 6, 6))
        assert outcome.piercing_blow

    def test_a_weapon_that_cannot_pierce_short_circuits(self, fight) -> None:
        # 08.7: check can_cause_piercing_blow first.
        f, hero, foe = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id="unarmed")]
        assert not f.pack.weapon("unarmed").can_cause_piercing_blow
        _, outcome = swing(f, hero, foe, feats=(FeatFace.RUNE,), successes=(6, 6, 6))
        assert not outcome.piercing_blow
        assert outcome.protection is None

    def test_a_weapon_with_no_injury_rating_cannot_wound(self, fight) -> None:
        f, hero, foe = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id="example_blade", two_handed=False)]
        assert f.pack.weapon("example_blade").injury_two_handed is None
        hero.gear.weapons[0].two_handed = True
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.piercing_blow
        assert outcome.protection is None, "no Injury rating is no Target Number to roll against"


class TestProtection:
    def test_the_roll_uses_body_armour_plus_helm(self, fight) -> None:
        # 08.7: summed, and zero armour means a Feat die alone.
        f, hero, foe = duel(fight, hero_kwargs={"helm": HELM})
        expected = f.pack.armour_type(MAIL).protection + f.pack.armour_type(HELM).protection
        _, outcome = strike(f, foe, hero)
        assert outcome.protection is not None
        assert outcome.protection.roll.request.rating == expected

    def test_no_armour_rolls_the_feat_die_alone(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"armour": None})
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.rating == 0

    def test_the_target_number_is_the_weapons_injury_rating(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.roll.request.target_number == foe.template.attacks[0].injury

    def test_a_reward_adds_to_the_result_not_the_dice(self, fight) -> None:
        # 08.7: "+2 to the result". It can rescue a numeric near-miss.
        f, hero, foe = duel(fight, template=RUFFIAN)
        f.register(hero.id, "close_fitting", EffectSource.item(hero.gear.armour.ref))
        injury = foe.template.attacks[0].injury
        _, outcome = strike(f, foe, hero, successes=(6, 6), prot_feat=10, prot_face=1)
        assert outcome.protection.modifier == 2
        # 10 + 1 + 1 + 1 = 13, two short of 14; the Reward closes the gap.
        assert outcome.protection.roll.total == 13 and injury == 14
        assert not outcome.protection.wounded

    def test_a_modifier_cannot_rescue_an_automatic_failure(self, fight) -> None:
        f, hero, foe = duel(fight, template=RUFFIAN)
        hero.conditions.miserable = True
        f.register(hero.id, "close_fitting", EffectSource.item(hero.gear.armour.ref))
        _, outcome = strike(f, foe, hero, successes=(6, 6), prot_feat=FeatFace.EYE)
        assert outcome.protection.wounded

    def test_an_attacker_effect_makes_the_roll_ill_favoured(self, fight) -> None:
        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "withering_gaze", EffectSource.acquired("fell_ability"))
        _, outcome = strike(f, foe, hero, prot_feats=(1, 1, FeatFace.RUNE))
        assert outcome.protection.roll.request.ill_favoured_sources == ("withering_gaze",)
        assert len(outcome.protection.roll.feat_dice) == 2

    def test_foe_slaying_turns_an_ill_favoured_roll_into_an_automatic_wound(self, fight) -> None:
        # 08.7: check that before rolling — there is no roll at all.
        from tor.effects.bus import Effect, EffectKind
        from tor.effects.hooks import FlagContribution, Hook

        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "withering_gaze", EffectSource.acquired("fell_ability"))
        f.buses[foe.instance_id].register(
            Effect(
                id=EffectId("foe_slaying"),
                kind=EffectKind.ENCHANTED_REWARD,
                listeners={
                    Hook.MODIFY_TARGET_PROTECTION_ROLL: lambda _c: FlagContribution(
                        source=EffectId("foe_slaying"), flag="wound_if_ill_favoured"
                    )
                },
            ),
            EffectSource.item("bane_blade"),
        )
        _, outcome = strike(f, foe, hero)
        assert outcome.protection.automatic
        assert outcome.protection.roll is None
        assert outcome.protection.wounded

    def test_a_ranged_piercing_blow_can_ill_favour_it(self, fight) -> None:
        # 08.7 / 04.3.3: ON_PIERCING_BLOW fires between scoring the blow and rolling
        # against it, which is the only place it could still matter.
        f, hero, foe = duel(fight)
        f.register(hero.id, "example_cv_fierce_shot", EffectSource.acquired("cultural_virtue"))
        hero.gear.weapons = [WeaponInstance(type_id="example_bow", two_handed=True)]
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(10,),
            successes=(6, 6, 6),
            form=blade_form(f, hero),
            prot_feats=(1, 1),
        )
        assert outcome.protection.roll.request.ill_favoured_sources == ("example_cv_fierce_shot",)

    def test_it_does_not_fire_for_a_close_combat_blow(self, fight) -> None:
        f, hero, foe = duel(fight)
        f.register(hero.id, "example_cv_fierce_shot", EffectSource.acquired("cultural_virtue"))
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.protection.roll.request.ill_favoured_sources == ()


class TestProtectionTiming:
    def test_the_mandatory_vector(self, fight) -> None:
        # 19.4: Endurance 12, Load 11, hit for 4 (taking them to 8, which is <= Load, so
        # Weary) and scoring a Piercing Blow. The PROTECTION roll must not be made Weary,
        # and the hero must be Weary immediately afterwards.
        f, hero, foe = duel(fight, template=RUFFIAN, hero_kwargs={"endurance": 12})
        ctx = f.ctx
        assert recompute_load(hero, ctx=ctx) == 11
        assert not hero.conditions.weary
        assert foe.template.attacks[0].damage == 4

        _, outcome = strike(f, foe, hero, feats=(10,), successes=(4, 4))
        assert outcome.piercing_blow
        assert outcome.protection is not None
        assert not outcome.protection.roll.request.weary, "rolled against the pre-loss condition"

        apply_attack(f.state, outcome, ctx=ctx)
        assert hero.endurance == 8
        assert hero.conditions.weary, "and Weary the moment the loss lands"

    def test_an_already_weary_hero_rolls_weary(self, fight) -> None:
        f, hero, foe = duel(fight, template=RUFFIAN, hero_kwargs={"endurance": 4})
        recompute_load(hero, ctx=f.ctx)
        from tor.rules.resources import recompute_conditions

        recompute_conditions(hero, ctx=f.ctx)
        assert hero.conditions.weary
        _, outcome = strike(f, foe, hero, feats=(10,), successes=(4, 4))
        assert outcome.protection.roll.request.weary


class TestWoundSeverity:
    def wound(self, f, hero, foe, severity_feat):
        return strike(f, foe, hero, severity_feat=severity_feat)[1]

    def test_the_rune_is_moderate(self, fight) -> None:
        f, hero, foe = duel(fight)
        outcome = self.wound(f, hero, foe, FeatFace.RUNE)
        assert outcome.wound.severity is WoundSeverity.MODERATE
        assert outcome.wound.injury_days == 0
        assert not outcome.wound.dying

    def test_a_seven_is_severe_with_seven_injury_days(self, fight) -> None:
        # 19.4: the numeric value is the number of days until the injury mends.
        f, hero, foe = duel(fight)
        outcome = self.wound(f, hero, foe, 7)
        assert outcome.wound.severity is WoundSeverity.SEVERE
        assert outcome.wound.injury_days == 7
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert hero.injury_days == 7
        assert hero.conditions.wounded

    def test_the_eye_is_grievous_and_dying(self, fight) -> None:
        f, hero, foe = duel(fight)
        outcome = self.wound(f, hero, foe, FeatFace.EYE)
        assert outcome.wound.severity is WoundSeverity.GRIEVOUS
        assert outcome.wound.dying
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert hero.dying
        assert hero.endurance == 0

    def test_a_second_wound_skips_the_severity_roll(self, fight) -> None:
        # 19.4 and 08.8: Endurance drops to zero, the hero falls unconscious, Dying.
        f, hero, foe = duel(fight)
        hero.conditions.wounded = True
        _, outcome = strike(f, foe, hero)
        assert outcome.wound is not None
        assert outcome.wound.roll is None, "no severity roll on a second Wound"
        assert outcome.wound.severity is None
        assert outcome.wound.dying
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert hero.dying and hero.endurance == 0

    def test_an_effect_may_shape_the_severity_roll(self, fight) -> None:
        f, hero, foe = duel(fight, template=LURKER)
        f.register(hero.id, "rending_claws", EffectSource.item("cursed_ring"))
        _, outcome = strike(f, foe, hero, prot_feats=(1, 7, 7))
        assert outcome.wound.roll.request.ill_favoured_sources == ("rending_claws",)
        assert len(outcome.wound.roll.feat_dice) == 2

    def test_building_a_wound_without_the_table_is_a_caller_bug(self, fight) -> None:
        # 08.8's table is content, and the engine must not carry it.
        f, hero, foe = duel(fight)
        form = adversary_attack_form(foe, foe.template.attacks[0])
        from tor.rules.combat.attack import AttackDeclaration

        decl = AttackDeclaration(attacker=foe.instance_id, target=hero.id, form=form)
        rng = ScriptedRandomness(feats=[10], successes=[6, 6, 6])
        attack = roll_attack(f.state, decl, ctx=f.ctx, rng=rng)
        with pytest.raises(StateError, match="must be injected"):
            resolve_attack(
                f.state,
                attack,
                SpendPlan(),
                ctx=f.ctx,
                rng=ScriptedRandomness(feats=[1], successes=[1, 1, 1]),
            )


class TestAdversaryWounds:
    def test_a_might_two_creature_survives_its_first_wound(self, fight) -> None:
        # 19.4's mandatory vector, and the assertion that no severity roll is made at all.
        f, hero, foe = duel(fight, template=BRUTE)
        assert foe.template.might == 2
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound is not None
        assert outcome.wound.roll is None, "adversaries never roll on the Wound Severity table"
        assert not outcome.wound.slain
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert foe.wounds_taken == 1 and foe.taken_out is None

        _, second = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert second.wound.slain
        apply_attack(f.state, second, ctx=f.ctx)
        assert foe.wounds_taken == 2 and str(foe.taken_out) == "slain"

    def test_a_might_one_creature_dies_on_its_first(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.slain

    def test_an_adversary_never_carries_the_wounded_condition(self, fight) -> None:
        f, hero, foe = duel(fight, template=BRUTE)
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert not hasattr(foe, "conditions")

    def test_zero_endurance_takes_a_creature_out_without_a_wound(self, fight) -> None:
        # 12.3: taken out at zero Endurance, and the reason is kept apart from slain so
        # the Loremaster may rule it alive but incapacitated.
        f, hero, foe = duel(fight, template=RUFFIAN)
        foe.endurance = 2
        _, outcome = swing(f, hero, foe, feats=(9,), successes=(6, 6, 6))
        assert outcome.wound is None
        events = apply_attack(f.state, outcome, ctx=f.ctx)
        assert str(foe.taken_out) == "endurance"
        assert EventKind.COMBATANT_OUT_OF_FIGHT in {e.kind for e in events}

    def test_adversaries_never_become_weary_from_endurance_loss(self, fight) -> None:
        # 12.3: a genuine asymmetry with heroes; only an empty drive pool does it.
        f, hero, foe = duel(fight, template=BRUTE)
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert foe.endurance < foe.template.endurance
        assert not foe.weary_this_round


class TestInterceptors:
    def test_hideous_toughness_replaces_the_killing_blow(self, fight) -> None:
        # 12.6: the loss instead causes a Piercing Blow, and the creature returns to full.
        f, hero, foe = duel(fight, template=BRUTE)
        f.register(foe.instance_id, "hideous_toughness", EffectSource.acquired("fell_ability"))
        foe.endurance = 3
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        assert outcome.intercepted_by == "hideous_toughness"
        assert outcome.piercing_blow, "the loss becomes a Piercing Blow instead"
        assert outcome.restore_full_endurance
        apply_attack(f.state, outcome, ctx=f.ctx)
        assert foe.endurance == foe.template.endurance

    def test_it_does_not_fire_on_a_survivable_blow(self, fight) -> None:
        f, hero, foe = duel(fight, template=BRUTE)
        f.register(foe.instance_id, "hideous_toughness", EffectSource.acquired("fell_ability"))
        _, outcome = swing(f, hero, foe, feats=(2,), successes=(6, 6, 6))
        assert outcome.intercepted_by is None

    def test_deathless_cancels_a_wound_for_a_drive_point(self, fight) -> None:
        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "deathless", EffectSource.acquired("fell_ability"))
        before = foe.total_drive
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.cancelled_by == "deathless"
        assert foe.total_drive == before - 1
        events = apply_attack(f.state, outcome, ctx=f.ctx)
        assert foe.wounds_taken == 0
        assert EventKind.WOUND_CANCELLED in {e.kind for e in events}

    def test_a_bane_weapon_suppresses_it(self, fight) -> None:
        # 12.6: disabled by a weapon enchanted against the creature's type, which is why
        # the attack context has to carry the weapon's banes.
        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "deathless", EffectSource.acquired("fell_ability"))
        hero.gear.weapons[0].banes = frozenset({CreatureType.SPIDERS})
        assert CreatureType.SPIDERS in foe.template.creature_types
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.cancelled_by is None
        assert outcome.wound.slain

    def test_an_interceptor_it_cannot_pay_for_does_not_fire(self, fight) -> None:
        f, hero, foe = duel(fight, template=LURKER)
        f.register(foe.instance_id, "deathless", EffectSource.acquired("fell_ability"))
        foe.drive.spend(foe.drive.current)
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert outcome.wound.cancelled_by is None


class TestKnockback:
    def test_the_mandatory_vector(self, fight) -> None:
        # 19.4: loss of 7, knockback chosen -> 4 (halved, rounding **up**). The next main
        # action is consumed, and a second knockback in the same round is rejected.
        f, hero, foe = duel(fight)
        assert foe.template.attacks[0].damage + foe.template.attribute_level == 7
        _, outcome = strike(
            f,
            foe,
            hero,
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,), knockback=True),
        )
        assert outcome.knockback_taken
        assert outcome.endurance_loss == 4

        apply_attack(f.state, outcome, ctx=f.ctx)
        combatant = f.state.combatant(hero.id)
        assert combatant.knocked_back, "the next main action is spent recovering"
        assert combatant.round_flags.knockback_used

        _, again = strike(
            f,
            foe,
            hero,
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,), knockback=True),
        )
        assert not again.knockback_taken, "once per round, not once per attack"
        assert again.endurance_loss == 7

    def test_an_adversary_cannot_choose_it(self, fight) -> None:
        # 08.12 and 12.3.
        f, hero, foe = duel(fight, template=BRUTE)
        _, outcome = swing(
            f, hero, foe, feats=(2,), successes=(6, 6, 6), plan=SpendPlan(knockback=True)
        )
        assert not outcome.knockback_taken

    def test_an_odd_loss_rounds_up(self, fight) -> None:
        f, hero, foe = duel(fight, template=RUFFIAN)
        _, outcome = strike(f, foe, hero, successes=(4, 4), plan=SpendPlan(knockback=True))
        assert outcome.endurance_loss == 2  # a loss of 4 halves exactly


class TestMisdeedPrompt:
    def test_killing_a_resolve_bearing_foe_prompts(self, fight) -> None:
        # 12.2: the engine prompts, it never decides.
        f, hero, foe = duel(fight, template=RUFFIAN)
        assert foe.template.misdeed_check_required
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        events = apply_attack(f.state, outcome, ctx=f.ctx)
        prompt = next(e for e in events if e.kind is EventKind.MISDEED_CHECK_PROMPT)
        assert prompt.gm_only
        assert len(prompt.payload["questions"]) == 3

    def test_killing_a_hate_bearing_foe_does_not(self, fight) -> None:
        f, hero, foe = duel(fight, template=MINION)
        assert not foe.template.misdeed_check_required
        _, outcome = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        events = apply_attack(f.state, outcome, ctx=f.ctx)
        assert EventKind.MISDEED_CHECK_PROMPT not in {e.kind for e in events}
