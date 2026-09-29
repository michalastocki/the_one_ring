"""Onset, volleys, stance, ordering and the boundaries (spec 08.2, 08.14).

The round boundary lives in one place, so the tests that matter here are the ones that
cross it: what a task made pending applies now, what a Fend Off bought has expired, and
what an adversary's drive pool means at the start of a round rather than in the middle.
"""

from __future__ import annotations

import pytest
from conftest import MINION, build_adversary, build_hero

from tor.dice import ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.effects.hooks import Environment
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.model.ids import AbilityId
from tor.rules.combat.sequence import (
    SurpriseInput,
    action_order,
    begin_combat,
    begin_round,
    end_combat,
    end_round,
    opening_volley_count,
    resolve_surprise,
    set_stance,
)
from tor.rules.combat.state import CombatPhase, Stance


def band(fight, *, heroes=1, foes=1, stance=Stance.OPEN, template=MINION):
    f = fight()
    for n in range(heroes):
        f.hero(build_hero(f"h{n}"), stance=stance)
    for n in range(foes):
        f.foe(build_adversary(f.pack, template, f"f{n}"))
    return f


class TestBeginCombat:
    def test_it_opens_at_onset_with_the_loremasters_volleys(self, fight) -> None:
        f = band(fight)
        events = begin_combat(f.state, volleys=2, ctx=f.ctx)
        assert f.state.phase is CombatPhase.ONSET
        assert f.state.round_number == 0
        assert f.state.volleys_allowed == 2
        assert events[0].kind is EventKind.COMBAT_BEGAN
        assert events[0].payload["volleys"] == 2

    def test_zero_volleys_is_legal(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        assert f.state.volleys_allowed == 0

    def test_a_negative_count_is_a_caller_bug(self, fight) -> None:
        f = band(fight)
        with pytest.raises(StateError, match="cannot have -1 opening volleys"):
            begin_combat(f.state, volleys=-1, ctx=f.ctx)

    def test_a_fight_needs_two_sides(self, fight) -> None:
        f = band(fight, foes=0)
        with pytest.raises(StateError, match="combatants on both sides"):
            begin_combat(f.state, ctx=f.ctx)

    def test_adversaries_start_with_their_weariness_derived(self, fight) -> None:
        f = band(fight)
        foe = f.state.active_adversaries()[0]
        foe.adversary.drive.spend(foe.adversary.drive.current)
        begin_combat(f.state, ctx=f.ctx)
        assert foe.adversary.weary_this_round


class TestSurprise:
    def test_no_ambush_resolves_nothing(self, fight) -> None:
        f = band(fight)
        rolls, events = resolve_surprise(
            f.state, SurpriseInput(), ctx=f.ctx, rng=ScriptedRandomness()
        )
        assert rolls == {} and events == []

    def test_being_ambushed_is_resolved_per_hero(self, fight) -> None:
        # 08.2.1: those who *fail* may make no opening volley and take no action in the
        # first round. The ones who passed are unaffected.
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        rng = ScriptedRandomness(feats=[10, 1], successes=[4, 4, 1, 1])
        _rolls, events = resolve_surprise(
            f.state, SurpriseInput(direction="company_ambushed"), ctx=f.ctx, rng=rng
        )
        assert rng.exhausted
        assert not a.surprised and b.surprised
        assert events[0].payload["took_effect"]

    def test_ambushing_is_all_or_nothing(self, fight) -> None:
        # 08.2.1: *all* must succeed for the surprise to take effect.
        f = band(fight, heroes=2, foes=2)
        rng = ScriptedRandomness(feats=[10, 1], successes=[4, 4, 1, 1])
        _, events = resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushes", ability=AbilityId("stealth")),
            ctx=f.ctx,
            rng=rng,
        )
        assert rng.exhausted
        assert not events[0].payload["took_effect"]
        assert not any(c.surprised for c in f.state.active_adversaries())

    def test_a_clean_ambush_surprises_every_enemy(self, fight) -> None:
        f = band(fight, heroes=2, foes=2)
        rng = ScriptedRandomness(feats=[10, 10], successes=[4, 4, 4, 4])
        resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushes", ability=AbilityId("stealth")),
            ctx=f.ctx,
            rng=rng,
        )
        assert rng.exhausted
        assert all(c.surprised for c in f.state.active_adversaries())

    def test_the_loremaster_may_rule_it_automatic(self, fight) -> None:
        # 08.2.1: they may also rule that no roll is needed at all.
        f = band(fight, foes=2)
        rolls, events = resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushes", automatic=True),
            ctx=f.ctx,
            rng=ScriptedRandomness(),
        )
        assert rolls == {}
        assert events[0].payload["took_effect"]
        assert all(c.surprised for c in f.state.active_adversaries())

    def test_the_skill_is_the_loremasters_choice(self, fight) -> None:
        # 08.2.1 names four, and leaves the choice open.
        f = band(fight)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        rolls, _ = resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushed", ability=AbilityId("battle")),
            ctx=f.ctx,
            rng=rng,
        )
        assert rng.exhausted
        assert next(iter(rolls.values())).request.ability == "battle"

    def test_the_loremaster_may_set_the_target_number(self, fight) -> None:
        f = band(fight)
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        rolls, _ = resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushed", target_number=9),
            ctx=f.ctx,
            rng=rng,
        )
        assert next(iter(rolls.values())).request.target_number == 9

    def test_only_the_named_participants_roll(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        rng = ScriptedRandomness(feats=[1], successes=[1, 1])
        rolls, _ = resolve_surprise(
            f.state,
            SurpriseInput(direction="company_ambushed"),
            ctx=f.ctx,
            rng=rng,
            participants=[a.ref],
        )
        assert rng.exhausted
        assert set(rolls) == {a.ref}
        assert a.surprised and not b.surprised

    def test_a_fallen_hero_does_not_roll(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        b.hero.endurance = 0
        rng = ScriptedRandomness(feats=[10], successes=[4, 4])
        rolls, _ = resolve_surprise(
            f.state, SurpriseInput(direction="company_ambushed"), ctx=f.ctx, rng=rng
        )
        assert rng.exhausted
        assert set(rolls) == {a.ref}


class TestOpeningVolleys:
    def test_the_loremasters_figure_is_the_default(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, volleys=2, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        assert opening_volley_count(f.state, hero.ref, ctx=f.ctx) == 2

    def test_an_effect_may_grant_one_where_none_were_allowed(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, volleys=0, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        f.register(hero.ref, "example_cv_long_bow", EffectSource.acquired("cultural_virtue"))
        assert opening_volley_count(f.state, hero.ref, ctx=f.ctx) == 1

    def test_but_not_when_the_wielder_is_surprised(self, fight) -> None:
        # 08.2.2's one condition on the hook.
        f = band(fight)
        begin_combat(f.state, volleys=2, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        f.register(hero.ref, "example_cv_long_bow", EffectSource.acquired("cultural_virtue"))
        hero.surprised = True
        assert opening_volley_count(f.state, hero.ref, ctx=f.ctx) == 0

    def test_a_fallen_combatant_looses_none(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, volleys=2, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        hero.hero.endurance = 0
        assert opening_volley_count(f.state, hero.ref, ctx=f.ctx) == 0


class TestStance:
    def test_a_close_stance_may_be_taken_freely(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        events = set_stance(f.state, hero.ref, Stance.FORWARD, ctx=f.ctx)
        assert hero.stance is Stance.FORWARD
        assert events[0].kind is EventKind.STANCE_CHOSEN

    def test_rearward_is_refused_without_the_companions(self, fight) -> None:
        f = band(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        with pytest.raises(RuleViolation, match="may not take Rearward"):
            set_stance(f.state, hero.ref, Stance.REARWARD, ctx=f.ctx)

    def test_the_loremaster_may_waive_the_requirements(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        set_stance(f.state, hero.ref, Stance.REARWARD, ctx=f.ctx, rearward_override=True)
        assert hero.stance is Stance.REARWARD

    def test_a_cultural_virtue_may_relax_the_companion_requirement(self, fight) -> None:
        # 08.2.3: relaxed to one, and it comes through STANCE_OPTIONS.
        from tor.effects.bus import Effect, EffectKind
        from tor.effects.hooks import FlagContribution, Hook
        from tor.model.ids import EffectId

        f = band(fight, heroes=2, foes=2)
        a, b = f.state.active_heroes()
        b.stance = Stance.FORWARD
        with pytest.raises(RuleViolation, match="may not take Rearward"):
            set_stance(f.state, a.ref, Stance.REARWARD, ctx=f.ctx)

        f.buses[a.ref].register(
            Effect(
                id=EffectId("keen_eyed"),
                kind=EffectKind.CULTURAL_VIRTUE,
                listeners={
                    Hook.STANCE_OPTIONS: lambda _c: FlagContribution(
                        source=EffectId("keen_eyed"), flag="rearward_relaxed"
                    )
                },
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        set_stance(f.state, a.ref, Stance.REARWARD, ctx=f.ctx)
        assert a.stance is Stance.REARWARD

    def test_the_relaxation_still_needs_one_companion(self, fight) -> None:
        from tor.effects.bus import Effect, EffectKind
        from tor.effects.hooks import FlagContribution, Hook
        from tor.model.ids import EffectId

        f = band(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        f.buses[hero.ref].register(
            Effect(
                id=EffectId("keen_eyed"),
                kind=EffectKind.CULTURAL_VIRTUE,
                listeners={
                    Hook.STANCE_OPTIONS: lambda _c: FlagContribution(
                        source=EffectId("keen_eyed"), flag="rearward_relaxed"
                    )
                },
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        with pytest.raises(RuleViolation, match="may not take Rearward"):
            set_stance(f.state, hero.ref, Stance.REARWARD, ctx=f.ctx)

    def test_a_seized_hero_is_pinned_in_forward(self, fight) -> None:
        # 12.5.
        f = band(fight)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        hero.seized_by = foe.ref
        with pytest.raises(RuleViolation, match="only fight in Forward"):
            set_stance(f.state, hero.ref, Stance.DEFENSIVE, ctx=f.ctx)
        set_stance(f.state, hero.ref, Stance.FORWARD, ctx=f.ctx)
        assert hero.stance is Stance.FORWARD

    def test_an_adversary_has_no_stance_to_set(self, fight) -> None:
        f = band(fight)
        foe = f.state.active_adversaries()[0]
        with pytest.raises(StateError, match="not a hero in this fight"):
            set_stance(f.state, foe.ref, Stance.FORWARD, ctx=f.ctx)


class TestActionOrder:
    def test_all_heroes_act_then_all_adversaries(self, fight) -> None:
        f = band(fight, heroes=2, foes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        order = action_order(f.state)
        assert [c.is_hero for c in order] == [True, True, False, False]

    def test_the_company_orders_by_stance(self, fight) -> None:
        f = band(fight, heroes=4, foes=1)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        heroes = f.state.active_heroes()
        heroes[0].stance = Stance.REARWARD
        heroes[1].stance = Stance.DEFENSIVE
        heroes[2].stance = Stance.FORWARD
        heroes[3].stance = Stance.OPEN
        order = [c.stance for c in action_order(f.state) if c.is_hero]
        assert order == [Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE, Stance.REARWARD]

    def test_the_opposition_orders_by_the_stance_it_is_attacking(self, fight) -> None:
        f = band(fight, heroes=2, foes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        quick, slow = f.state.active_heroes()
        quick.stance = Stance.FORWARD
        slow.stance = Stance.DEFENSIVE
        first, second = f.state.active_adversaries()
        first.attacking = slow.ref
        second.attacking = quick.ref
        order = [c.ref for c in action_order(f.state) if not c.is_hero]
        assert order == [second.ref, first.ref]

    def test_one_that_stood_back_resolves_last(self, fight) -> None:
        # 08.2.5: adversaries that stood back unengaged with ranged weapons go last.
        f = band(fight, heroes=1, foes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        hero.stance = Stance.FORWARD
        archer, melee = f.state.active_adversaries()
        archer.stood_back = True
        archer.attacking = hero.ref
        melee.attacking = hero.ref
        order = [c.ref for c in action_order(f.state) if not c.is_hero]
        assert order == [melee.ref, archer.ref]

    def test_a_surprised_combatant_takes_no_action_in_the_first_round(self, fight) -> None:
        f = band(fight, heroes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        a, b = f.state.active_heroes()
        b.surprised = True
        assert [c.ref for c in action_order(f.state) if c.is_hero] == [a.ref]

    def test_but_acts_normally_in_the_second(self, fight) -> None:
        f = band(fight, heroes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        b = f.state.active_heroes()[1]
        b.surprised = True
        end_round(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert b.ref in {c.ref for c in action_order(f.state)}

    def test_a_fallen_combatant_never_acts(self, fight) -> None:
        f = band(fight, heroes=2)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        a, b = f.state.active_heroes()
        b.hero.endurance = 0
        assert [c.ref for c in action_order(f.state) if c.is_hero] == [a.ref]


class TestRoundBoundary:
    def test_the_round_number_advances(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert f.state.round_number == 1
        end_round(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert f.state.round_number == 2

    def test_a_decided_fight_starts_no_further_round(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        f.state.active_adversaries()[0].adversary.apply_wound()
        with pytest.raises(StateError, match="already decided"):
            begin_round(f.state, ctx=f.ctx)

    def test_rally_is_available_again_each_round(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        f.state.rallied_this_round = True
        end_round(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert not f.state.rallied_this_round

    def test_a_fend_off_expires_and_a_rally_arrives(self, fight) -> None:
        # The one place any of this expires (08.1).
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        hero.round_flags.parry_bonus = 2
        hero.pending_flags.attack_bonus_dice = 1
        end_round(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        assert hero.round_flags.parry_bonus == 0
        assert hero.round_flags.attack_bonus_dice == 1

    def test_next_attack_interference_lapses_at_the_round_end(self, fight) -> None:
        from tor.rules.combat.state import Advantage, Duration, Interference

        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        f.state.advantages.append(
            Advantage(level=Interference.MODERATE_ADVANTAGE, duration=Duration.NEXT_ATTACK)
        )
        end_round(f.state, ctx=f.ctx)
        assert f.state.advantages == []

    def test_the_round_events_bracket_it(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        assert begin_round(f.state, ctx=f.ctx)[0].kind is EventKind.ROUND_BEGAN
        assert end_round(f.state, ctx=f.ctx)[0].kind is EventKind.ROUND_ENDED


class TestEndOfCombat:
    def test_every_flag_is_cleared(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        begin_round(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        hero.round_flags.parry_bonus = 3
        hero.pending_flags.weary = True
        end_combat(f.state, ctx=f.ctx)
        assert hero.round_flags.parry_bonus == 0
        assert not hero.pending_flags.weary
        assert f.state.phase is CombatPhase.RESOLVED

    def test_a_moderate_wound_clears(self, fight) -> None:
        # 08.8: recovers fully within hours at the end of the combat.
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0].hero
        hero.conditions.wounded = True
        events = end_combat(f.state, ctx=f.ctx)
        assert not hero.conditions.wounded
        assert any(e.kind is EventKind.WOUND_HEALED for e in events)

    def test_a_severe_wound_does_not(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0].hero
        hero.conditions.wounded = True
        hero.injury_days = 7
        end_combat(f.state, ctx=f.ctx)
        assert hero.conditions.wounded and hero.injury_days == 7

    def test_a_dying_hero_keeps_everything(self, fight) -> None:
        # Their clock belongs to 08.9.
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0].hero
        hero.conditions.wounded = True
        hero.dying = True
        end_combat(f.state, ctx=f.ctx)
        assert hero.conditions.wounded

    def test_the_summary_records_who_was_taken_out_and_how(self, fight) -> None:
        # 12.3: kept apart so the Loremaster may rule them alive but incapacitated.
        f = band(fight, foes=2)
        begin_combat(f.state, ctx=f.ctx)
        slain, spent = f.state.active_adversaries()
        slain.adversary.apply_wound()
        spent.adversary.take_endurance_loss(spent.adversary.endurance)
        events = end_combat(f.state, ctx=f.ctx)
        summary = events[-1]
        assert summary.kind is EventKind.COMBAT_ENDED
        assert summary.payload["taken_out"] == {slain.ref: "slain", spent.ref: "endurance"}
        assert summary.payload["adversaries_standing"] == []

    def test_an_on_combat_end_effect_fires(self, fight) -> None:
        f = band(fight)
        begin_combat(f.state, ctx=f.ctx)
        hero = f.state.active_heroes()[0]
        f.register(hero.ref, "second_cv_second_wind", EffectSource.acquired("cultural_virtue"))
        end_combat(f.state, ctx=f.ctx)  # the hook fires; applying it belongs to 15


class TestEnvironment:
    def test_the_scene_reaches_the_attack_roll(self, fight) -> None:
        f = band(fight, template=MINION)
        f.state.environment = Environment(in_darkness=True)
        foe = f.state.active_adversaries()[0]
        f.register(foe.ref, "denizen_of_the_dark", EffectSource.acquired("fell_ability"))
        from test_combat_attack import strike

        hero = f.state.active_heroes()[0].hero
        attack, _ = strike(f, foe.adversary, hero, feats=(10, 10), successes=(4, 4, 4))
        assert attack.roll.request.favoured_sources == ("denizen_of_the_dark",)
