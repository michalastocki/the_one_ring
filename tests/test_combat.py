"""Unit tests for Combat Round orchestration (Combatant, CombatRound, CombatEncounter)."""

import pytest

from engine.dice import (
    Combatant,
    CombatRound,
    CombatEncounter,
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
