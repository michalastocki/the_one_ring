"""`tor.rules.combat.state` — stances, combatants and the round's expiring flags (spec 08.1).

No dice here: this module is the shape the rest of the package moves through.
"""

from __future__ import annotations

import pytest
from conftest import BRUTE, MINION, build_adversary, build_hero

from tor.errors import RuleViolation, StateError
from tor.model.adversary import AdversarySize
from tor.model.ids import HeroId
from tor.rules.combat.state import (
    CLOSE_STANCES,
    STANCE_ORDER,
    Advantage,
    CombatState,
    Complication,
    Duration,
    Interference,
    RoundFlags,
    Stance,
    stance_order_key,
)


class TestStances:
    def test_three_are_close_and_rearward_is_not(self) -> None:
        assert set(CLOSE_STANCES) == {Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE}
        assert Stance.REARWARD not in CLOSE_STANCES

    def test_the_action_order_is_forward_open_defensive_rearward(self) -> None:
        # 08.2.5: strict stance order within the Company.
        assert STANCE_ORDER == (Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE, Stance.REARWARD)
        assert [stance_order_key(s) for s in STANCE_ORDER] == [0, 1, 2, 3]

    def test_no_stance_sorts_last(self) -> None:
        # An adversary that stood back unengaged resolves after everyone (08.2.5).
        assert stance_order_key(None) > stance_order_key(Stance.REARWARD)


class TestInterference:
    def test_the_four_levels_of_8_11(self) -> None:
        assert Interference.MODERATELY_HINDERED.dice == -1
        assert Interference.SEVERELY_HINDERED.dice == -2
        assert Interference.MODERATE_ADVANTAGE.dice == 1
        assert Interference.GREATER_ADVANTAGE.dice == 2

    def test_a_complication_must_hinder(self) -> None:
        with pytest.raises(StateError, match="helps rather than hinders"):
            Complication(level=Interference.MODERATE_ADVANTAGE)

    def test_an_advantage_must_help(self) -> None:
        with pytest.raises(StateError, match="hinders rather than helps"):
            Advantage(level=Interference.SEVERELY_HINDERED)

    def test_they_net_against_one_another(self, fight) -> None:
        f = fight()
        f.state.complications.append(Complication(level=Interference.SEVERELY_HINDERED))
        f.state.advantages.append(Advantage(level=Interference.MODERATE_ADVANTAGE))
        assert f.state.hero_dice_modifier() == -1

    def test_only_the_next_attack_entries_are_consumed(self, fight) -> None:
        f = fight()
        lasting = Complication(level=Interference.MODERATELY_HINDERED)
        fleeting = Advantage(level=Interference.GREATER_ADVANTAGE, duration=Duration.NEXT_ATTACK)
        f.state.complications.append(lasting)
        f.state.advantages.append(fleeting)
        f.state.consume_next_attack_interference()
        assert f.state.complications == [lasting]
        assert f.state.advantages == []


class TestRoundFlags:
    def test_a_fresh_set_is_empty(self) -> None:
        flags = RoundFlags()
        assert (flags.parry_bonus, flags.attack_bonus_dice, flags.weary) == (0, 0, False)

    def test_clearing_restores_every_default(self) -> None:
        flags = RoundFlags(parry_bonus=3, weary=True, knockback_used=True, prepared_shot=2)
        flags.clear()
        assert flags == RoundFlags()


class TestCombatant:
    def test_is_hero_is_derived_from_the_actor(self, fight, pack) -> None:
        # 08.1 stores it as a field; a stored flag can disagree with what it describes.
        f = fight()
        hero = f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        assert f.state.combatant(hero.id).is_hero
        assert not f.state.combatant(foe.instance_id).is_hero

    def test_asking_a_hero_for_its_adversary_is_a_caller_bug(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero())
        with pytest.raises(StateError, match="is a hero, not an adversary"):
            assert f.state.combatant(hero.id).adversary

    def test_asking_an_adversary_for_its_hero_is_a_caller_bug(self, fight, pack) -> None:
        f = fight()
        foe = f.foe(build_adversary(pack))
        with pytest.raises(StateError, match="is an adversary, not a hero"):
            assert f.state.combatant(foe.instance_id).hero

    def test_size_drives_the_engagement_limits(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero())
        small = f.foe(build_adversary(pack, MINION, "small"))
        large = f.foe(build_adversary(pack, BRUTE, "large"))
        assert f.state.combatant(hero.id).size is AdversarySize.HUMAN
        assert f.state.combatant(small.instance_id).size is AdversarySize.HUMAN
        assert f.state.combatant(large.instance_id).size is AdversarySize.LARGE

    def test_a_hero_at_zero_endurance_is_out(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero(endurance=0))
        assert not f.state.combatant(hero.id).active

    def test_a_dying_hero_is_out(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero())
        hero.dying = True
        assert not f.state.combatant(hero.id).active

    def test_a_fled_or_slain_combatant_is_out(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        f.state.combatant(hero.id).fled = True
        foe.apply_wound()
        assert not f.state.combatant(hero.id).active
        assert not f.state.combatant(foe.instance_id).active

    def test_only_a_hero_in_a_close_stance_is_in_close_combat(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero(), stance=Stance.REARWARD)
        foe = f.foe(build_adversary(pack))
        assert not f.state.combatant(hero.id).in_close_combat
        assert not f.state.combatant(foe.instance_id).in_close_combat
        f.state.combatant(hero.id).stance = Stance.FORWARD
        assert f.state.combatant(hero.id).in_close_combat

    def test_an_adversary_inherits_the_stance_of_the_hero_it_attacks(self, fight, pack) -> None:
        # 08.2.5: the stance system models the heroes' point of view.
        f = fight()
        hero = f.hero(build_hero(), stance=Stance.DEFENSIVE)
        foe = f.foe(build_adversary(pack))
        foe_c = f.state.combatant(foe.instance_id)
        foe_c.attacking = hero.id
        assert foe_c.effective_stance(f.state) is Stance.DEFENSIVE

    def test_one_that_stood_back_has_no_stance_at_all(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero(), stance=Stance.FORWARD)
        foe = f.foe(build_adversary(pack))
        foe_c = f.state.combatant(foe.instance_id)
        foe_c.attacking = hero.id
        foe_c.stood_back = True
        assert foe_c.effective_stance(f.state) is None

    def test_one_attacking_nobody_has_no_stance(self, fight, pack) -> None:
        f = fight()
        f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        assert f.state.combatant(foe.instance_id).effective_stance(f.state) is None

    def test_one_attacking_somebody_outside_the_fight_has_no_stance(self, fight, pack) -> None:
        f = fight()
        f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        f.state.combatant(foe.instance_id).attacking = HeroId("ghost")
        assert f.state.combatant(foe.instance_id).effective_stance(f.state) is None


class TestRoundBoundary:
    def test_pending_becomes_current_and_a_fresh_bucket_starts(self, fight) -> None:
        # 08.1: two tasks grant their benefit to the *next* round.
        f = fight()
        hero = f.hero(build_hero())
        combatant = f.state.combatant(hero.id)
        combatant.pending_flags.attack_bonus_dice = 1
        combatant.round_flags.parry_bonus = 3

        combatant.begin_round()
        assert combatant.round_flags.attack_bonus_dice == 1
        assert combatant.round_flags.parry_bonus == 0, "last round's Fend Off has expired"
        assert combatant.pending_flags == RoundFlags()

    def test_the_action_budget_resets(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero())
        combatant = f.state.combatant(hero.id)
        combatant.spend_main_action()
        combatant.spend_secondary_action()
        combatant.begin_round()
        assert not combatant.main_action_used
        assert not combatant.secondary_action_used

    def test_an_adversary_re_derives_its_weariness(self, fight, pack) -> None:
        # 12.2: checked at the start of the round, not when the pool empties.
        f = fight()
        foe = f.foe(build_adversary(pack))
        combatant = f.state.combatant(foe.instance_id)
        foe.drive.spend(foe.drive.current)
        assert not foe.weary_this_round, "spending the last point mid-round does not bite"
        combatant.begin_round()
        assert foe.weary_this_round

    def test_a_second_main_action_is_a_caller_bug(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero())
        combatant = f.state.combatant(hero.id)
        combatant.spend_main_action()
        with pytest.raises(StateError, match="already taken a main action"):
            combatant.spend_main_action()

    def test_a_second_secondary_action_is_a_caller_bug(self, fight) -> None:
        f = fight()
        hero = f.hero(build_hero())
        combatant = f.state.combatant(hero.id)
        combatant.spend_secondary_action()
        with pytest.raises(StateError, match="already taken a secondary action"):
            combatant.spend_secondary_action()


class TestCombatStateBookkeeping:
    def test_a_combatant_cannot_join_twice(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        with pytest.raises(RuleViolation, match="already in this fight"):
            f.state.add_hero(hero)
        with pytest.raises(RuleViolation, match="already in this fight"):
            f.state.add_adversary(foe)

    def test_an_unknown_combatant_is_a_caller_bug(self) -> None:
        with pytest.raises(StateError, match="is not in this fight"):
            CombatState().combatant(HeroId("nobody"))

    def test_the_fight_is_over_when_one_side_is_gone(self, fight, pack) -> None:
        f = fight()
        hero = f.hero(build_hero())
        foe = f.foe(build_adversary(pack))
        assert not f.state.over
        foe.apply_wound()
        assert f.state.over
        assert f.state.active_heroes() and not f.state.active_adversaries()
        assert [c.ref for c in f.state.all_active()] == [hero.id]

    def test_an_environment_is_scoped_without_touching_the_fight(self, fight) -> None:
        from tor.effects.hooks import Environment

        f = fight()
        f.hero(build_hero())
        dark = f.state.in_environment(Environment(in_darkness=True))
        assert dark.environment.in_darkness
        assert not f.state.environment.in_darkness
