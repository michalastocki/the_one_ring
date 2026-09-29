"""`tor.rules.combat.attack` — the attack roll and everything hanging off it (spec 08.5-08.9).

Carries six of 19.4's mandatory vectors: the asymmetric Target Number, PROTECTION timing,
Pierce before the threshold, Pierce on an icon face, Heavy Blow with a two-handed weapon,
knockback, the Wound Severity table, and Break Shield against a Reward-bearing shield.

Every test scripts its dice exactly and asserts the queue drains (19.2). Because an attack
rolls twice — once to hit and once for PROTECTION — the two are scripted separately, which
is also what makes the ordering assertions possible.
"""

from __future__ import annotations

import pytest
from conftest import (
    BLADE,
    BOW,
    BRUTE,
    BUCKLER,
    GREAT_AXE,
    MINION,
    RUFFIAN,
    SPEAR,
    build_adversary,
    build_hero,
)

from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.errors import RuleViolation, StateError
from tor.model.gear import WeaponInstance
from tor.model.ids import AbilityId, EffectId
from tor.rolls import attribute_tn
from tor.rules.combat.attack import (
    AttackDeclaration,
    SpecialDamage,
    SpendPlan,
    adversary_attack_form,
    apply_attack,
    attack_target_number,
    hero_attack_form,
    offered_special_damage,
    resolve_attack,
    roll_attack,
    shield_parry,
)
from tor.rules.combat.state import Stance

SWORDS = AbilityId("swords")


def duel(fight, *, template: str = MINION, hero_kwargs=None, stance=Stance.OPEN):
    """One hero and one foe, with the hero's blade already in hand."""
    f = fight()
    hero = f.hero(build_hero(**(hero_kwargs or {})), stance=stance)
    foe = f.foe(build_adversary(f.pack, template))
    return f, hero, foe


def blade_form(f, hero, **kw):
    return hero_attack_form(hero, hero.gear.weapons[0], ctx=f.ctx, **kw)


def armour_dice(f, combatant) -> int:
    """The Success dice a PROTECTION roll asks for (08.7): body armour plus helm, summed."""
    if not combatant.is_hero:
        return combatant.adversary.template.armour
    gear = combatant.hero.gear
    return sum(
        f.pack.armour_type(slot.type_id).protection
        for slot in (gear.armour, gear.helm)
        if slot is not None and not slot.dropped
    )


def attack_with(
    f,
    attacker_ref,
    target_ref,
    form,
    *,
    feats,
    successes,
    plan=None,
    prot_feat=1,
    prot_face=1,
    severity_feat=1,
    prot_feats=None,
    **decl_kw,
):
    """Roll and resolve one attack, scripting both rolls exactly (19.2).

    The PROTECTION queue is sized from the target's own armour rather than written out per
    test: what is under test is the rule, and a hand-counted queue would only ever restate
    `armour_dice`. It is still asserted drained whenever a roll happened.
    """
    decl = AttackDeclaration(attacker=attacker_ref, target=target_ref, form=form, **decl_kw)
    rng = ScriptedRandomness(feats=list(feats), successes=list(successes))
    attack = roll_attack(f.state, decl, ctx=f.ctx, rng=rng)
    assert rng.exhausted, f"{rng.remaining} dice left over on the attack roll"

    target = f.state.combatant(target_ref)
    armour = armour_dice(f, target)
    # Two Feat dice are scripted because resolving a hit can roll twice more: PROTECTION,
    # and then the Wound Severity table (08.7, 08.8). Rather than hand-counting which of
    # those happened, the helper asserts afterwards that exactly as many dice were taken
    # as the outcome says were rolled — the same 19.2 discipline, stated over the result.
    # One Feat die for PROTECTION and one for Wound Severity by default. A test whose
    # roll is Favoured or Ill-favoured keeps two of one of them, and says so explicitly
    # with `prot_feats` — the dice a roll asks for is exactly what 19.2 wants visible.
    queue = list(prot_feats) if prot_feats is not None else [prot_feat, severity_feat]
    prot = ScriptedRandomness(feats=queue, successes=[prot_face] * armour)
    outcome = resolve_attack(
        f.state,
        attack,
        plan or SpendPlan(),
        ctx=f.ctx,
        rng=prot,
        severity_table=f.pack.table("wound_severity"),
    )
    rolled_protection = outcome.protection is not None and outcome.protection.roll is not None
    rolled_severity = outcome.wound is not None and outcome.wound.roll is not None
    expected = (len(outcome.protection.roll.feat_dice) if rolled_protection else 0) + (
        len(outcome.wound.roll.feat_dice) if rolled_severity else 0
    )
    feats_left, successes_left = prot.remaining
    assert len(queue) - feats_left == expected, "the Feat dice the two rolls asked for"
    assert armour - successes_left == (armour if rolled_protection else 0)
    return attack, outcome


def swing(f, hero, foe, *, feats=(10,), successes=(6,), form=None, **kw):
    """One hero attack, with the blade in hand by default."""
    return attack_with(
        f,
        hero.id,
        foe.instance_id,
        form or blade_form(f, hero),
        feats=feats,
        successes=successes,
        **kw,
    )


def strike(f, foe, hero, *, feats=(10,), successes=(6, 6, 6), attack_index=0, **kw):
    """One adversary attack with the named attack form."""
    form = adversary_attack_form(foe, foe.template.attacks[attack_index])
    return attack_with(f, foe.instance_id, hero.id, form, feats=feats, successes=successes, **kw)


class TestTargetNumber:
    def test_a_hero_rolls_strength_tn_plus_the_foes_parry(self, fight) -> None:
        # 08.5's asymmetry, and the common implementation error it warns about.
        f, hero, foe = duel(fight)
        expected = attribute_tn(hero.attributes.strength) + foe.template.parry
        assert (
            attack_target_number(
                f.state.combatant(hero.id), f.state.combatant(foe.instance_id), ctx=f.ctx
            )
            == expected
        )

    def test_an_adversary_rolls_against_the_heros_parry_score(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"shield": BUCKLER})
        shield = f.pack.shield_type(BUCKLER).parry_bonus
        assert (
            attack_target_number(
                f.state.combatant(foe.instance_id), f.state.combatant(hero.id), ctx=f.ctx
            )
            == hero.parry.base + shield
        )

    def test_the_heros_strength_tn_never_enters_an_adversarys_attack(self, fight) -> None:
        strong, _, _ = duel(fight, hero_kwargs={"strength": 7})
        weak, _, _ = duel(fight, hero_kwargs={"strength": 2})
        assert attack_target_number(
            strong.state.active_adversaries()[0], strong.state.active_heroes()[0], ctx=strong.ctx
        ) == attack_target_number(
            weak.state.active_adversaries()[0], weak.state.active_heroes()[0], ctx=weak.ctx
        )

    def test_an_effect_may_shift_the_target_number(self, fight) -> None:
        f, hero, foe = duel(fight)
        before = attack_target_number(
            f.state.combatant(hero.id), f.state.combatant(foe.instance_id), ctx=f.ctx
        )
        f.register(hero.id, "unnerving_bulk", EffectSource.acquired("curse"))
        assert (
            attack_target_number(
                f.state.combatant(hero.id), f.state.combatant(foe.instance_id), ctx=f.ctx
            )
            == before + 2
        )

    def test_a_target_number_is_never_below_one(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"parry": 0})
        assert (
            attack_target_number(
                f.state.combatant(foe.instance_id), f.state.combatant(hero.id), ctx=f.ctx
            )
            >= 1
        )


class TestShieldParry:
    def test_a_shield_adds_its_bonus(self, fight) -> None:
        f, hero, _ = duel(fight, hero_kwargs={"shield": BUCKLER})
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == (
            f.pack.shield_type(BUCKLER).parry_bonus
        )

    def test_a_volley_doubles_it_for_an_aware_hero(self, fight) -> None:
        # 08.2.2, implemented as a context flag rather than a hardcoded x2 in the attack.
        f, hero, _ = duel(fight, hero_kwargs={"shield": BUCKLER})
        plain = shield_parry(f.state.combatant(hero.id), ctx=f.ctx)
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx, doubled=True) == plain * 2

    def test_no_shield_is_no_bonus(self, fight) -> None:
        f, hero, _ = duel(fight)
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == 0

    def test_a_dropped_or_smashed_shield_contributes_nothing(self, fight) -> None:
        f, hero, _ = duel(fight, hero_kwargs={"shield": BUCKLER})
        hero.gear.shield.dropped = True
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == 0
        hero.gear.shield.dropped = False
        hero.gear.shield.smashed = True
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == 0

    def test_an_adversary_has_no_shield(self, fight) -> None:
        f, _, foe = duel(fight)
        assert shield_parry(f.state.combatant(foe.instance_id), ctx=f.ctx) == 0

    def test_a_reward_raises_it(self, fight) -> None:
        f, hero, _ = duel(fight, hero_kwargs={"shield": BUCKLER})
        f.register(hero.id, "reinforced", EffectSource.item(hero.gear.shield.ref))
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == (
            f.pack.shield_type(BUCKLER).parry_bonus + 1
        )


class TestAttackForm:
    def test_a_hero_reads_their_weapons_numbers(self, fight) -> None:
        f, hero, _ = duel(fight)
        form = blade_form(f, hero)
        kind = f.pack.weapon(BLADE)
        assert (form.damage, form.injury, form.proficiency) == (
            kind.damage,
            kind.injury_one_handed,
            SWORDS,
        )
        assert form.rating == hero.proficiencies[SWORDS]

    def test_brawling_resolves_at_the_highest_proficiency(self, fight) -> None:
        # 03.3: a mode, not a proficiency, and the -1d is a penalty die not a lower rating.
        f, hero, _ = duel(fight)
        form = blade_form(f, hero, brawling=True)
        assert form.rating == hero.brawling_rating()
        assert form.rating == max(hero.proficiencies.values())

    def test_a_two_handed_grip_selects_the_other_injury_rating(self, fight) -> None:
        f, hero, _ = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id=SPEAR, two_handed=True)]
        form = blade_form(f, hero)
        assert form.injury == f.pack.weapon(SPEAR).injury_two_handed
        assert form.two_handed

    def test_an_adversary_reads_its_attack_form(self, fight) -> None:
        _f, _, foe = duel(fight)
        attack = foe.template.attacks[0]
        form = adversary_attack_form(foe, attack)
        assert (form.damage, form.injury, form.rating) == (
            attack.damage,
            attack.injury,
            attack.rating,
        )

    def test_heavy_blow_is_added_implicitly_and_never_listed(self, fight) -> None:
        # 12.5: every adversary can always choose it, so content must not carry it.
        _f, _, foe = duel(fight)
        for attack in foe.template.attacks:
            assert "heavy_blow" not in attack.special_damage
            assert SpecialDamage.HEAVY_BLOW in adversary_attack_form(foe, attack).special_damage


class TestOfferedSpecialDamage:
    def test_a_hero_is_gated_by_war_gear(self, fight) -> None:
        f, hero, _ = duel(fight, hero_kwargs={"shield": BUCKLER})
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert offered == {
            SpecialDamage.HEAVY_BLOW,
            SpecialDamage.FEND_OFF,
            SpecialDamage.PIERCE,
            SpecialDamage.SHIELD_THRUST,
        }

    def test_no_shield_means_no_shield_thrust(self, fight) -> None:
        f, hero, _ = duel(fight)
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert SpecialDamage.SHIELD_THRUST not in offered

    def test_a_weapon_outside_the_pierce_list_offers_no_pierce(self, fight) -> None:
        f, hero, _ = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id=GREAT_AXE, two_handed=True)]
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert SpecialDamage.PIERCE not in offered
        assert SpecialDamage.FEND_OFF in offered

    def test_a_ranged_weapon_offers_no_fend_off(self, fight) -> None:
        f, hero, _ = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id=BOW, two_handed=True)]
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert SpecialDamage.FEND_OFF not in offered
        assert SpecialDamage.PIERCE in offered

    def test_a_seized_hero_may_buy_their_freedom(self, fight) -> None:
        f, hero, foe = duel(fight)
        f.state.combatant(hero.id).seized_by = foe.instance_id
        offered = offered_special_damage(f.state.combatant(hero.id), blade_form(f, hero), ctx=f.ctx)
        assert SpecialDamage.BREAK_FREE in offered

    def test_an_effect_may_grant_an_option_the_sheet_lacks(self, fight) -> None:
        f, _, foe = duel(fight)
        f.register(foe.instance_id, "sundering_stroke", EffectSource.acquired("fell_ability"))
        form = adversary_attack_form(foe, foe.template.attacks[1])
        offered = offered_special_damage(f.state.combatant(foe.instance_id), form, ctx=f.ctx)
        assert SpecialDamage.BREAK_SHIELD in offered


class TestRollingTheAttack:
    def test_a_hit_deals_the_weapons_damage(self, fight) -> None:
        f, hero, foe = duel(fight)
        attack, _ = swing(f, hero, foe, feats=(10,), successes=(6, 6, 6))
        assert attack.hit
        assert attack.endurance_loss == f.pack.weapon(BLADE).damage

    def test_a_miss_does_nothing_at_all(self, fight) -> None:
        # 08.5: there is no fumble mechanic in combat beyond the Miserable rule.
        f, hero, foe = duel(fight)
        attack, outcome = swing(f, hero, foe, feats=(1,), successes=(1, 1, 1))
        assert not attack.hit
        assert (outcome.endurance_loss, outcome.piercing_blow) == (0, False)
        assert outcome.protection is None and outcome.wound is None
        assert apply_attack(f.state, outcome, ctx=f.ctx)[0].payload["hit"] is False

    def test_forward_stance_adds_a_die(self, fight) -> None:
        f, hero, foe = duel(fight, stance=Stance.FORWARD)
        swing(f, hero, foe, feats=(10,), successes=(6, 6, 6, 6))

    def test_defensive_loses_a_die_per_opponent_engaging_you(self, fight) -> None:
        # 08.2.3: per opponent, not a flat penalty.
        f, hero, foe = duel(fight, stance=Stance.DEFENSIVE)
        f.state.combatant(hero.id).engaged_with = {foe.instance_id}
        swing(f, hero, foe, feats=(10,), successes=(6, 6))

    def test_brawling_costs_a_penalty_die(self, fight) -> None:
        f, hero, foe = duel(fight)
        form = blade_form(f, hero, brawling=True)
        swing(f, hero, foe, feats=(10,), successes=(6, 6), form=form, brawling=True)

    def test_complications_and_advantages_reach_every_hero_roll(self, fight) -> None:
        from tor.rules.combat.state import Complication, Interference

        f, hero, foe = duel(fight)
        f.state.complications.append(Complication(level=Interference.SEVERELY_HINDERED))
        swing(f, hero, foe, feats=(10,), successes=(6,))

    def test_a_rearward_hero_cannot_swing_a_blade(self, fight) -> None:
        f, hero, foe = duel(fight, stance=Stance.REARWARD)
        with pytest.raises(RuleViolation, match="ranged attacks only"):
            swing(f, hero, foe)

    def test_a_seized_hero_may_only_brawl(self, fight) -> None:
        # 12.5.
        f, hero, foe = duel(fight)
        f.state.combatant(hero.id).seized_by = foe.instance_id
        with pytest.raises(RuleViolation, match="only make Brawling attacks"):
            swing(f, hero, foe)

    def test_two_of_a_side_cannot_attack_each_other(self, fight) -> None:
        f = fight()
        a = f.hero(build_hero("a"))
        b = f.hero(build_hero("b"))
        with pytest.raises(RuleViolation, match="same side"):
            roll_attack(
                f.state,
                AttackDeclaration(attacker=a.id, target=b.id, form=blade_form(f, a)),
                ctx=f.ctx,
                rng=ScriptedRandomness(),
            )

    def test_a_target_already_out_cannot_be_attacked(self, fight) -> None:
        f, hero, foe = duel(fight)
        foe.apply_wound()
        with pytest.raises(RuleViolation, match="already out of the fight"):
            swing(f, hero, foe)

    def test_an_attacker_already_out_is_a_caller_bug(self, fight) -> None:
        f, hero, foe = duel(fight)
        hero.endurance = 0
        with pytest.raises(StateError, match="no longer in the fight"):
            swing(f, hero, foe)

    def test_an_adversary_spends_drive_for_a_die(self, fight) -> None:
        # 12.2, and the point is spent before the dice.
        f, hero, foe = duel(fight)
        form = adversary_attack_form(foe, foe.template.attacks[0])
        before = foe.total_drive
        decl = AttackDeclaration(attacker=foe.instance_id, target=hero.id, form=form, spend_drive=1)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4, 4, 4])
        roll_attack(f.state, decl, ctx=f.ctx, rng=rng)
        assert rng.exhausted, "rating 3 plus the drive die"
        assert foe.total_drive == before - 1

    def test_an_adversarys_icons_are_inverted(self, fight) -> None:
        # 12.4: one boolean, one code path.
        f, hero, foe = duel(fight)
        form = adversary_attack_form(foe, foe.template.attacks[0])
        decl = AttackDeclaration(attacker=foe.instance_id, target=hero.id, form=form)
        rng = ScriptedRandomness(feats=[FeatFace.EYE], successes=[1, 1, 1])
        attack = roll_attack(f.state, decl, ctx=f.ctx, rng=rng)
        assert rng.exhausted
        assert attack.hit, "the eye is the auto-success face for a servant of the Shadow"

    def test_a_non_shadow_adversary_rolls_normally(self, fight) -> None:
        f, hero, foe = duel(fight, template=RUFFIAN)
        assert not foe.template.icon_inverted
        form = adversary_attack_form(foe, foe.template.attacks[0])
        decl = AttackDeclaration(attacker=foe.instance_id, target=hero.id, form=form)
        rng = ScriptedRandomness(feats=[FeatFace.EYE], successes=[1, 1])
        attack = roll_attack(f.state, decl, ctx=f.ctx, rng=rng)
        assert rng.exhausted
        assert not attack.hit


class TestSpecialDamage:
    def test_heavy_blow_with_a_two_handed_weapon(self, fight) -> None:
        # 19.4: STRENGTH 5, two-handed -> additional loss of 6. With the Virtue -> 7.
        f, hero, foe = duel(fight, hero_kwargs={"strength": 5})
        hero.gear.weapons = [WeaponInstance(type_id=SPEAR, two_handed=True)]
        base = f.pack.weapon(SPEAR).damage
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,)),
        )
        assert outcome.endurance_loss == base + 6

        f.register(hero.id, "dour_handed", EffectSource.acquired("virtue"))
        _, with_virtue = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,)),
        )
        assert with_virtue.endurance_loss == base + 7

    def test_a_one_handed_weapon_adds_strength_alone(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"strength": 5})
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,)),
        )
        assert outcome.endurance_loss == f.pack.weapon(BLADE).damage + 5

    def test_an_adversary_adds_its_attribute_level(self, fight) -> None:
        # 12.5: Attribute Level rather than STRENGTH, and no two-handed bonus.
        f, hero, foe = duel(fight)
        _, outcome = strike(f, foe, hero, plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,)))
        form = adversary_attack_form(foe, foe.template.attacks[0])
        assert outcome.endurance_loss == form.damage + foe.template.attribute_level

    def test_pierce_before_the_threshold(self, fight) -> None:
        # 19.4: Feat 8, a Pierce value of +3, one icon on Pierce -> effective 11 -> a
        # Piercing Blow despite the raw roll being below 10.
        f, hero, foe = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id=SPEAR, two_handed=True)]
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(8,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.PIERCE,)),
        )
        assert outcome.piercing_blow

    def test_without_the_pierce_the_same_roll_scores_nothing(self, fight) -> None:
        f, hero, foe = duel(fight)
        hero.gear.weapons = [WeaponInstance(type_id=SPEAR, two_handed=True)]
        _, outcome = swing(f, hero, foe, feats=(8,), successes=(6, 6, 6))
        assert not outcome.piercing_blow

    def test_pierce_on_an_icon_face_changes_nothing(self, fight) -> None:
        # 19.4: the attack already auto-succeeds and already qualifies.
        f, hero, foe = duel(fight)
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(FeatFace.RUNE,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.PIERCE,)),
        )
        assert outcome.piercing_blow
        assert outcome.special_damage[0].amount == 1, "swords are +1, spent but inert"

    def test_fend_off_raises_the_attackers_own_parry_for_the_round(self, fight) -> None:
        # 08.6: defensive, and it goes into RoundFlags rather than the damage calculation.
        f, hero, foe = duel(fight)
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.FEND_OFF,)),
        )
        assert f.state.combatant(hero.id).round_flags.parry_bonus == 2  # swords
        assert outcome.endurance_loss == f.pack.weapon(BLADE).damage

    def test_shield_thrust_pushes_a_weaker_foe(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"strength": 7, "shield": BUCKLER})
        assert hero.attributes.strength > foe.template.attribute_level
        swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.SHIELD_THRUST,)),
        )
        assert f.state.combatant(foe.instance_id).round_flags.attack_penalty_dice == 1

    def test_shield_thrust_does_nothing_to_a_stronger_foe(self, fight) -> None:
        f, hero, foe = duel(fight, template=BRUTE, hero_kwargs={"strength": 5, "shield": BUCKLER})
        assert hero.attributes.strength < foe.template.attribute_level
        _, outcome = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.SHIELD_THRUST,)),
        )
        assert outcome.special_damage[0].amount == 0
        assert f.state.combatant(foe.instance_id).round_flags.attack_penalty_dice == 0

    def test_each_shield_thrust_must_name_a_different_opponent(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero(strength=7, shield=BUCKLER))
        a = f.foe(build_adversary(f.pack, MINION, "a"))
        b = f.foe(build_adversary(f.pack, MINION, "b"))
        swing(
            f,
            hero,
            a,
            feats=(2,),
            successes=(6, 6, 6),
            plan=SpendPlan(
                spends=(SpecialDamage.SHIELD_THRUST, SpecialDamage.SHIELD_THRUST),
                shield_thrust_targets=(a.instance_id, b.instance_id),
            ),
        )
        assert f.state.combatant(a.instance_id).round_flags.attack_penalty_dice == 1
        assert f.state.combatant(b.instance_id).round_flags.attack_penalty_dice == 1

    def test_shield_thrust_cannot_push_a_companion(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero("a", strength=7, shield=BUCKLER))
        ally = f.hero(build_hero("b"))
        foe = f.foe(build_adversary(f.pack))
        with pytest.raises(RuleViolation, match="pushes an opponent"):
            swing(
                f,
                hero,
                foe,
                feats=(2,),
                successes=(6, 6, 6),
                plan=SpendPlan(
                    spends=(SpecialDamage.SHIELD_THRUST,), shield_thrust_targets=(ally.id,)
                ),
            )

    def test_an_option_the_weapon_does_not_offer_is_refused(self, fight) -> None:
        f, hero, foe = duel(fight)
        with pytest.raises(RuleViolation, match="cannot spend an icon"):
            swing(
                f,
                hero,
                foe,
                feats=(2,),
                successes=(6, 6, 6),
                plan=SpendPlan(spends=(SpecialDamage.SEIZE,)),
            )

    def test_spending_more_icons_than_were_rolled_is_refused(self, fight) -> None:
        f, hero, foe = duel(fight)
        with pytest.raises(RuleViolation, match="only 0 left of 1"):
            swing(
                f,
                hero,
                foe,
                feats=(10,),
                successes=(6, 4, 4),
                plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW, SpecialDamage.HEAVY_BLOW)),
            )

    def test_spending_does_not_lower_the_degree(self, fight) -> None:
        # 02.3.6: a great success remains a great success after its icon is spent.
        from tor.rolls import Degree

        f, hero, foe = duel(fight)
        attack, outcome = swing(
            f,
            hero,
            foe,
            feats=(2,),
            successes=(6, 6, 4),
            plan=SpendPlan(spends=(SpecialDamage.HEAVY_BLOW,)),
        )
        assert attack.roll.degree is Degree.EXTRAORDINARY_SUCCESS
        assert outcome.icon_budget.remaining == 1


class TestSeizeAndBreakShield:
    def test_seize_pins_a_hero_in_forward_with_brawling(self, fight) -> None:
        # 12.5.
        f, hero, foe = duel(fight, template=BRUTE, stance=Stance.OPEN)
        strike(
            f,
            foe,
            hero,
            successes=(6, 6, 6, 6),
            plan=SpendPlan(spends=(SpecialDamage.SEIZE,)),
        )
        assert f.state.combatant(hero.id).seized_by == foe.instance_id
        assert f.state.combatant(hero.id).stance is Stance.FORWARD

    def test_a_seized_hero_buys_their_freedom_with_an_icon(self, fight) -> None:
        f, hero, foe = duel(fight)
        f.state.combatant(hero.id).seized_by = foe.instance_id
        swing(
            f,
            hero,
            foe,
            feats=(10,),
            successes=(6, 6),
            form=blade_form(f, hero, brawling=True),
            brawling=True,
            plan=SpendPlan(spends=(SpecialDamage.BREAK_FREE,)),
        )
        assert f.state.combatant(hero.id).seized_by is None

    def test_break_shield_smashes_it_and_drops_its_parry(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"shield": BUCKLER})
        f.register(hero.id, "reinforced", EffectSource.item(hero.gear.shield.ref))
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) > 0
        strike(f, foe, hero, plan=SpendPlan(spends=(SpecialDamage.BREAK_SHIELD,)))
        assert hero.gear.shield.smashed
        # 12.5: Parry is a DerivedStat recomputed from what is registered, so nothing else
        # had to be told the shield is gone.
        assert shield_parry(f.state.combatant(hero.id), ctx=f.ctx) == 0

    def test_a_reward_bearing_shield_cannot_be_smashed(self, fight) -> None:
        # 19.4 / 15.4.1: an item bearing a Reward can never be lost, broken, or taken.
        f, hero, foe = duel(fight, hero_kwargs={"shield": BUCKLER})
        hero.gear.shield.upgrades.rewards.append(EffectId("reinforced"))
        _, outcome = strike(f, foe, hero, plan=SpendPlan(spends=(SpecialDamage.BREAK_SHIELD,)))
        assert not hero.gear.shield.smashed
        assert outcome.special_damage[0].amount == 0

    def test_breaking_a_shield_nobody_carries_does_nothing(self, fight) -> None:
        f, hero, foe = duel(fight)
        _, outcome = strike(f, foe, hero, plan=SpendPlan(spends=(SpecialDamage.BREAK_SHIELD,)))
        assert outcome.special_damage[0].amount == 0

    def test_a_shield_already_smashed_is_not_smashed_again(self, fight) -> None:
        f, hero, foe = duel(fight, hero_kwargs={"shield": BUCKLER})
        hero.gear.shield.smashed = True
        _, outcome = strike(f, foe, hero, plan=SpendPlan(spends=(SpecialDamage.BREAK_SHIELD,)))
        assert outcome.special_damage[0].amount == 0
