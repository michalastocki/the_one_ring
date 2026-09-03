"""Unit tests for Combat Round orchestration (Combatant, CombatRound, CombatEncounter)."""

import pytest

from engine.dice import (
    BattleStance,
    Combatant,
    CombatRound,
    CombatEncounter,
    RangeBand,
    Stance,
    TestOutcome,
    Weapon,
)


class SequentialRandom:
    """Returns values from a predetermined sequence on each randint() call."""

    def __init__(self, values: list[int]):
        self._values = values
        self._index = 0

    def randint(self, _a: int, _b: int) -> int:
        v = self._values[self._index % len(self._values)]
        self._index += 1
        return v


def make_combatant(name: str, **overrides) -> Combatant:
    defaults = dict(
        name=name,
        ability_level=0,
        strength_attribute=10,  # strength TN = 20-10 = 10
        defence=0,
        armor_rating=0,
        weapon=Weapon(damage=5, piercing_value=10),
        endurance_max=10,
        load=2,
        hope_max=10,
    )
    defaults.update(overrides)
    return Combatant(**defaults)


# ---------------------------------------------------------------------------
# Combatant
# ---------------------------------------------------------------------------

class TestCombatant:

    def test_endurance_and_hope_default_to_max(self):
        c = make_combatant("Hero", endurance_max=20, hope_max=8)
        assert c.endurance_current == 20
        assert c.hope_current == 8

    def test_explicit_current_values_are_kept(self):
        c = make_combatant("Hero", endurance_max=20, endurance_current=7, hope_max=8, hope_current=1)
        assert c.endurance_current == 7
        assert c.hope_current == 1

    def test_is_weary_when_endurance_at_or_below_load(self):
        c = make_combatant("Hero", endurance_max=10, load=4, endurance_current=4)
        assert c.is_weary is True

    def test_is_not_weary_above_load(self):
        c = make_combatant("Hero", endurance_max=10, load=4, endurance_current=5)
        assert c.is_weary is False

    def test_is_miserable_when_shadow_at_or_above_hope(self):
        c = make_combatant("Hero", hope_max=5, hope_current=5, shadow_points=5)
        assert c.is_miserable is True

    def test_is_not_miserable_below_hope(self):
        c = make_combatant("Hero", hope_max=5, hope_current=5, shadow_points=4)
        assert c.is_miserable is False

    def test_take_damage_floors_at_zero(self):
        c = make_combatant("Hero", endurance_max=5, endurance_current=3)
        c.take_damage(10)
        assert c.endurance_current == 0

    def test_take_damage_reduces_endurance(self):
        c = make_combatant("Hero", endurance_max=10)
        c.take_damage(4)
        assert c.endurance_current == 6

    def test_out_of_action_at_zero_endurance(self):
        c = make_combatant("Hero", endurance_max=10, endurance_current=0)
        assert c.is_out_of_action is True

    def test_not_out_of_action_above_zero(self):
        c = make_combatant("Hero", endurance_max=10, endurance_current=1)
        assert c.is_out_of_action is False

    def test_take_wound_increments_and_flags_wounded(self):
        c = make_combatant("Hero")
        assert c.is_wounded is False
        c.take_wound()
        assert c.wounds == 1
        assert c.is_wounded is True


# ---------------------------------------------------------------------------
# BattleStance modifiers (Forward / Open / Defensive / Rearward)
# ---------------------------------------------------------------------------

class TestBattleStanceModifiers:

    def test_forward_grants_bonus_die_and_drops_parry(self):
        c = make_combatant("Hero", battle_stance=BattleStance.FORWARD, defence=3, parry_rating=4)
        assert c.attack_bonus_dice == 1
        assert c.attack_penalty_dice == 0
        assert c.effective_defence == 3  # parry not counted

    def test_open_is_the_no_modifier_baseline(self):
        c = make_combatant("Hero", battle_stance=BattleStance.OPEN, defence=3, parry_rating=4)
        assert c.attack_bonus_dice == 0
        assert c.attack_penalty_dice == 0
        assert c.effective_defence == 7  # 3 + 1*4

    def test_defensive_grants_penalty_die_and_doubles_parry(self):
        c = make_combatant("Hero", battle_stance=BattleStance.DEFENSIVE, defence=3, parry_rating=4)
        assert c.attack_bonus_dice == 0
        assert c.attack_penalty_dice == 1
        assert c.effective_defence == 11  # 3 + 2*4

    def test_rearward_grants_heavy_penalty_and_flat_defence_bonus(self):
        c = make_combatant("Hero", battle_stance=BattleStance.REARWARD, defence=3, parry_rating=4)
        assert c.attack_bonus_dice == 0
        assert c.attack_penalty_dice == 2
        assert c.effective_defence == 9  # 3 + 1*4 + 2

    def test_default_battle_stance_is_open(self):
        c = make_combatant("Hero", defence=3, parry_rating=4)
        assert c.battle_stance == BattleStance.OPEN
        assert c.effective_defence == 7


# ---------------------------------------------------------------------------
# CombatRound.roll_initiative
# ---------------------------------------------------------------------------

class TestRollInitiative:

    def test_gandalf_first_normal_then_eye_last(self):
        a = make_combatant("A")  # raw 5 -> comparison_key 5
        b = make_combatant("B")  # raw 12 -> GANDALF, key 13
        c = make_combatant("C")  # raw 11 -> EYE, key 0
        rng = SequentialRandom([5, 12, 11])
        combat_round = CombatRound(round_number=1, combatants=[a, b, c])
        order = combat_round.roll_initiative(rng)
        assert [x.name for x in order] == ["B", "A", "C"]

    def test_out_of_action_combatants_excluded(self):
        a = make_combatant("A", endurance_current=0)
        b = make_combatant("B")
        rng = SequentialRandom([7])  # only B rolls
        combat_round = CombatRound(round_number=1, combatants=[a, b])
        order = combat_round.roll_initiative(rng)
        assert [x.name for x in order] == ["B"]

    def test_ties_preserve_input_order(self):
        a = make_combatant("A")
        b = make_combatant("B")
        rng = SequentialRandom([6, 6])
        combat_round = CombatRound(round_number=1, combatants=[a, b])
        order = combat_round.roll_initiative(rng)
        assert [x.name for x in order] == ["A", "B"]


# ---------------------------------------------------------------------------
# CombatRound.resolve
# ---------------------------------------------------------------------------

class TestCombatRoundResolve:

    def test_successful_attack_deals_damage(self):
        hero = make_combatant("Hero", ability_level=0, strength_attribute=15)  # TN=5
        orc = make_combatant("Orc", endurance_max=10)
        # initiative: hero=6, orc=3 -> hero acts first; attack AD=8 -> total 8 >= TN 5
        rng = SequentialRandom([6, 3, 8])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.attacker == "Hero"
        assert outcome.target == "Orc"
        assert outcome.result.outcome == TestOutcome.SUCCESS
        assert outcome.damage_dealt == 5
        assert orc.endurance_current == 5
        assert outcome.target_out_of_action is False

    def test_unassigned_combatant_holds_action(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc")
        rng = SequentialRandom([6, 3])  # initiative only, no attack rolled
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({}, rng)
        assert outcomes == []

    def test_attack_against_already_out_of_action_target_is_skipped(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc", endurance_current=0)
        rng = SequentialRandom([6])  # only hero (active) rolls initiative
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)
        assert outcomes == []

    def test_attack_against_unknown_target_name_is_skipped(self):
        hero = make_combatant("Hero")
        rng = SequentialRandom([6])
        combat_round = CombatRound(round_number=1, combatants=[hero])
        outcomes = combat_round.resolve({"Hero": "Ghost"}, rng)
        assert outcomes == []

    def test_break_defence_failure_inflicts_wound(self):
        hero = make_combatant(
            "Hero",
            ability_level=0,
            strength_attribute=10,  # TN=10
            weapon=Weapon(damage=5, piercing_value=5),
        )
        orc = make_combatant("Orc", armor_rating=1)
        # initiative: hero=3, orc=2 -> hero first; AD=10 (hit + triggers break defence);
        # armour roll raw=1 -> value 1 < piercing_value 5 -> armour fails -> wound
        rng = SequentialRandom([3, 2, 10, 1])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)

        outcome = outcomes[0]
        assert outcome.result.break_defense_triggered is True
        assert outcome.result.break_defense_result.outcome == TestOutcome.FAILURE
        assert orc.wounds == 1
        assert orc.endurance_current == 5  # damage still applied


# ---------------------------------------------------------------------------
# CombatRound.resolve — BattleStance wiring
# ---------------------------------------------------------------------------

class TestCombatRoundBattleStanceWiring:

    def test_forward_attacker_rolls_an_extra_success_die(self):
        hero = make_combatant(
            "Hero", ability_level=1, strength_attribute=15, battle_stance=BattleStance.FORWARD
        )
        orc = make_combatant("Orc")
        # initiative: hero=6, orc=3 -> hero first; AD=2, then 2 success dice (1 base + 1 Forward)
        rng = SequentialRandom([6, 3, 2, 3, 4])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)

        result = outcomes[0].result
        assert len(result.success_dice) == 2
        assert result.total == 9  # 2 + 3 + 4

    def test_defensive_attacker_rolls_one_fewer_success_die(self):
        hero = make_combatant(
            "Hero", ability_level=1, strength_attribute=15, battle_stance=BattleStance.DEFENSIVE
        )
        orc = make_combatant("Orc")
        # initiative: hero=6, orc=3 -> hero first; pool = 1 base - 1 Defensive = 0 success dice
        rng = SequentialRandom([6, 3, 9])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)

        result = outcomes[0].result
        assert len(result.success_dice) == 0
        assert result.total == 9

    def test_defensive_target_is_harder_to_hit_than_open_target(self):
        hero = make_combatant("Hero", ability_level=1, strength_attribute=15)  # TN base=5

        def attack_open_vs_defensive(target_stance):
            target = make_combatant(
                "Orc", defence=0, parry_rating=2, battle_stance=target_stance
            )
            rng = SequentialRandom([6, 3, 5, 2])  # initiative, then AD=5 + SD=2 -> total 7
            combat_round = CombatRound(round_number=1, combatants=[hero, target])
            return combat_round.resolve({"Hero": "Orc"}, rng)[0].result

        open_result = attack_open_vs_defensive(BattleStance.OPEN)
        assert open_result.target_number == 7  # 5 + 1*2
        assert open_result.outcome == TestOutcome.SUCCESS

        defensive_result = attack_open_vs_defensive(BattleStance.DEFENSIVE)
        assert defensive_result.target_number == 9  # 5 + 2*2
        assert defensive_result.outcome == TestOutcome.FAILURE


# ---------------------------------------------------------------------------
# CombatRound.resolve — RangeBand wiring (melee vs. ranged weapons)
# ---------------------------------------------------------------------------

class TestCombatRoundRangeWiring:

    def test_melee_attack_at_short_range_is_unaffected(self):
        hero = make_combatant("Hero", ability_level=0, strength_attribute=15)  # TN=5
        orc = make_combatant("Orc", endurance_max=10)
        rng = SequentialRandom([6, 3, 8])  # initiative, then AD=8 -> 8 >= 5
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.SHORT}
        )

        outcome = outcomes[0]
        assert outcome.out_of_range is False
        assert outcome.result.outcome == TestOutcome.SUCCESS
        assert outcome.damage_dealt == 5

    def test_melee_attack_beyond_short_range_is_out_of_range(self):
        hero = make_combatant("Hero")  # default weapon is melee (is_ranged=False)
        orc = make_combatant("Orc", endurance_max=10)
        rng = SequentialRandom([6, 3])  # initiative only; no attack die is rolled
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.LONG}
        )

        outcome = outcomes[0]
        assert outcome.out_of_range is True
        assert outcome.result is None
        assert outcome.damage_dealt == 0
        assert orc.endurance_current == 10  # untouched

    def test_missing_range_defaults_to_short(self):
        hero = make_combatant("Hero", ability_level=0, strength_attribute=15)
        orc = make_combatant("Orc")
        rng = SequentialRandom([6, 3, 8])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve({"Hero": "Orc"}, rng)  # no ranges given
        assert outcomes[0].out_of_range is False

    def test_ranged_weapon_at_optimal_range_has_no_penalty(self):
        bow = Weapon(damage=5, piercing_value=10, is_ranged=True, optimal_range=RangeBand.MEDIUM)
        hero = make_combatant("Hero", ability_level=1, strength_attribute=15, weapon=bow)
        orc = make_combatant("Orc")
        rng = SequentialRandom([6, 3, 2, 3])  # initiative, AD=2, one success die SD=3
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.MEDIUM}
        )

        result = outcomes[0].result
        assert len(result.success_dice) == 1
        assert result.total == 5  # 2 + 3

    def test_ranged_weapon_one_band_off_optimal_loses_a_die(self):
        bow = Weapon(damage=5, piercing_value=10, is_ranged=True, optimal_range=RangeBand.MEDIUM)
        hero = make_combatant("Hero", ability_level=1, strength_attribute=15, weapon=bow)
        orc = make_combatant("Orc")
        # 1 base die - 1 range penalty (SHORT is one band off MEDIUM) = 0 success dice
        rng = SequentialRandom([6, 3, 9])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.SHORT}
        )

        result = outcomes[0].result
        assert len(result.success_dice) == 0
        assert result.total == 9

    def test_ranged_weapon_two_bands_off_optimal_loses_two_dice(self):
        bow = Weapon(damage=5, piercing_value=10, is_ranged=True, optimal_range=RangeBand.SHORT)
        hero = make_combatant("Hero", ability_level=2, strength_attribute=15, weapon=bow)
        orc = make_combatant("Orc")
        # 2 base dice - 2 range penalty (LONG is two bands off SHORT) = 0 success dice
        rng = SequentialRandom([6, 3, 7])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.LONG}
        )

        result = outcomes[0].result
        assert len(result.success_dice) == 0
        assert result.total == 7

    def test_ranged_weapon_can_attack_at_any_range(self):
        bow = Weapon(damage=5, piercing_value=10, is_ranged=True, optimal_range=RangeBand.LONG)
        hero = make_combatant("Hero", ability_level=0, strength_attribute=15, weapon=bow)
        orc = make_combatant("Orc")
        rng = SequentialRandom([6, 3, 8])
        combat_round = CombatRound(round_number=1, combatants=[hero, orc])
        outcomes = combat_round.resolve(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.SHORT}
        )
        assert outcomes[0].out_of_range is False


# ---------------------------------------------------------------------------
# CombatEncounter
# ---------------------------------------------------------------------------

class TestCombatEncounter:

    def test_round_number_increments_after_run_round(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc")
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        assert encounter.round_number == 1
        rng = SequentialRandom([6, 3, 1])  # miss: total 1 < TN 10
        encounter.run_round({}, rng)
        assert encounter.round_number == 2
        assert len(encounter.history) == 1

    def test_not_over_when_both_sides_active(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc")
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        assert encounter.is_over is False
        assert encounter.winner is None

    def test_heroes_win_when_enemies_defeated(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc", endurance_current=0)
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        assert encounter.is_over is True
        assert encounter.winner == "heroes"

    def test_enemies_win_when_heroes_defeated(self):
        hero = make_combatant("Hero", endurance_current=0)
        orc = make_combatant("Orc")
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        assert encounter.is_over is True
        assert encounter.winner == "enemies"

    def test_mutual_defeat_has_no_winner(self):
        hero = make_combatant("Hero", endurance_current=0)
        orc = make_combatant("Orc", endurance_current=0)
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        assert encounter.is_over is True
        assert encounter.winner is None

    def test_run_round_raises_when_already_over(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc", endurance_current=0)
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        with pytest.raises(RuntimeError):
            encounter.run_round({})

    def test_run_round_raises_when_max_rounds_exceeded(self):
        hero = make_combatant("Hero")
        orc = make_combatant("Orc")
        encounter = CombatEncounter(heroes=[hero], enemies=[orc], max_rounds=0)
        with pytest.raises(RuntimeError):
            encounter.run_round({})

    def test_run_round_uses_both_sides_as_combatants(self):
        hero = make_combatant("Hero", ability_level=0, strength_attribute=15)  # TN=5
        orc = make_combatant("Orc", endurance_max=10)
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        rng = SequentialRandom([6, 3, 8])  # initiative, then hit
        outcomes = encounter.run_round({"Hero": "Orc"}, rng)
        assert outcomes[0].damage_dealt == 5
        assert orc.endurance_current == 5

    def test_run_round_forwards_ranges_to_combat_round(self):
        hero = make_combatant("Hero")  # melee weapon
        orc = make_combatant("Orc", endurance_max=10)
        encounter = CombatEncounter(heroes=[hero], enemies=[orc])
        rng = SequentialRandom([6, 3])  # initiative only; melee can't reach LONG
        outcomes = encounter.run_round(
            {"Hero": "Orc"}, rng, ranges={"Hero": RangeBand.LONG}
        )
        assert outcomes[0].out_of_range is True
        assert orc.endurance_current == 10
