"""Combat Tasks and the BATTLE roll for the odds (spec 08.10, 08.11).

Every task grades by icon tiers (pattern P4). Two of the four grant their benefit to the
**next** round, which is the whole reason `RoundFlags` has two buckets, so each of those
is asserted across a round boundary rather than inside one.
"""

from __future__ import annotations

import pytest
from conftest import BRUTE, MINION, build_adversary, build_hero

from tor.dice import FeatFace, ScriptedRandomness
from tor.effects.bus import EffectSource
from tor.errors import RuleViolation, StateError
from tor.events import EventKind
from tor.rules.combat.state import (
    Complication,
    Duration,
    Interference,
    Stance,
)
from tor.rules.combat.tasks import (
    TASK_ABILITY,
    TASK_STANCE,
    CombatTask,
    TaskDeclaration,
    apply_task,
    battle_for_interference,
    resolve_task,
)


def band(fight, *, heroes=1, foes=1, stance=Stance.OPEN, template=MINION):
    f = fight()
    for n in range(heroes):
        f.hero(build_hero(f"h{n}"), stance=stance)
    for n in range(foes):
        f.foe(build_adversary(f.pack, template, f"f{n}"))
    return f


def attempt(f, ref, task, *, feats=(10,), successes=(4, 4), **kw):
    """Roll and apply one task, scripting the dice exactly (19.2)."""
    decl = TaskDeclaration(actor=ref, task=task, **kw)
    rng = ScriptedRandomness(feats=list(feats), successes=list(successes))
    outcome = resolve_task(f.state, decl, ctx=f.ctx, rng=rng)
    assert rng.exhausted, f"{rng.remaining} dice left over"
    events = apply_task(f.state, outcome, ctx=f.ctx)
    return outcome, events


class TestTaskTable:
    def test_each_task_is_tied_to_a_stance_and_a_skill(self) -> None:
        assert TASK_STANCE == {
            CombatTask.INTIMIDATE_FOE: Stance.FORWARD,
            CombatTask.RALLY_COMRADES: Stance.OPEN,
            CombatTask.PROTECT_COMPANION: Stance.DEFENSIVE,
            CombatTask.PREPARE_SHOT: Stance.REARWARD,
        }
        assert [str(TASK_ABILITY[t]) for t in CombatTask] == [
            "awe",
            "enhearten",
            "battle",
            "scan",
        ]

    def test_a_task_from_the_wrong_stance_is_refused(self, fight) -> None:
        f = band(fight, stance=Stance.FORWARD)
        hero = f.state.active_heroes()[0]
        with pytest.raises(RuleViolation, match="attempted from open"):
            attempt(f, hero.ref, CombatTask.RALLY_COMRADES)

    def test_an_adversary_cannot_attempt_one(self, fight) -> None:
        f = band(fight)
        foe = f.state.active_adversaries()[0]
        with pytest.raises(RuleViolation, match="belong to the heroes"):
            attempt(f, foe.ref, CombatTask.RALLY_COMRADES)

    def test_a_fallen_hero_cannot_attempt_one(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        hero.hero.endurance = 0
        with pytest.raises(StateError, match="no longer in the fight"):
            attempt(f, hero.ref, CombatTask.RALLY_COMRADES)

    def test_a_task_spends_the_main_action(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        attempt(f, hero.ref, CombatTask.RALLY_COMRADES)
        assert hero.main_action_used and not hero.secondary_action_used

    def test_an_effect_may_convert_one_to_a_secondary_action(self, fight) -> None:
        # 08.10: a main action unless an effect converts it.
        f = band(fight, stance=Stance.DEFENSIVE, heroes=2)
        a, b = f.state.active_heroes()
        f.register(a.ref, "example_cv_guard", EffectSource.acquired("cultural_virtue"))
        with pytest.raises(RuleViolation, match="nothing converts"):
            attempt(f, b.ref, CombatTask.PROTECT_COMPANION, companion=a.ref, as_secondary=True)

    def test_the_converted_task_spends_the_secondary_action(self, fight) -> None:
        from tor.effects.bus import Effect, EffectKind
        from tor.effects.hooks import ActionContribution, Hook
        from tor.model.ids import EffectId

        f = band(fight, stance=Stance.DEFENSIVE, heroes=2)
        a, b = f.state.active_heroes()
        f.buses[a.ref].register(
            Effect(
                id=EffectId("swift_guard"),
                kind=EffectKind.CULTURAL_VIRTUE,
                listeners={
                    Hook.SECONDARY_ACTION_OPTIONS: lambda _c: ActionContribution(
                        source=EffectId("swift_guard"),
                        action="secondary_task",
                        payload={"task": "protect_companion"},
                    )
                },
            ),
            EffectSource.acquired("cultural_virtue"),
        )
        attempt(f, a.ref, CombatTask.PROTECT_COMPANION, companion=b.ref, as_secondary=True)
        assert a.secondary_action_used and not a.main_action_used


class TestIntimidateFoe:
    def setup_band(self, fight):
        f = band(fight, stance=Stance.FORWARD, foes=0)
        f.foe(build_adversary(f.pack, MINION, "weak"))
        f.foe(build_adversary(f.pack, BRUTE, "strong"))
        return f, f.state.active_heroes()[0]

    def test_a_bare_success_reaches_might_one(self, fight) -> None:
        f, hero = self.setup_band(fight)
        outcome, _ = attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(10,), successes=(4, 4))
        assert outcome.tier == 1
        assert set(outcome.affected) == {"weak"}

    def test_one_icon_reaches_might_two(self, fight) -> None:
        f, hero = self.setup_band(fight)
        outcome, _ = attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(10,), successes=(6, 4))
        assert outcome.tier == 2
        assert set(outcome.affected) == {"weak", "strong"}

    def test_two_icons_reach_everything(self, fight) -> None:
        f, hero = self.setup_band(fight)
        outcome, _ = attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(10,), successes=(6, 6))
        assert outcome.tier == 3
        assert set(outcome.affected) == {"weak", "strong"}

    def test_the_weariness_lands_on_the_next_round(self, fight) -> None:
        # 08.10: Weary on their *next* attack roll.
        f, hero = self.setup_band(fight)
        attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(10,), successes=(4, 4))
        weak = f.state.combatant("weak")
        assert not weak.round_flags.weary
        assert weak.pending_flags.weary
        weak.begin_round()
        assert weak.round_flags.weary

    def test_a_failure_reaches_nobody(self, fight) -> None:
        f, hero = self.setup_band(fight)
        outcome, _ = attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(1,), successes=(1, 1))
        assert outcome.tier == 0 and outcome.affected == ()
        assert not f.state.combatant("weak").pending_flags.weary

    def test_a_creature_may_be_immune(self, fight) -> None:
        # 08.10: an ActionContribution from its own Fell Ability, not a branch here.
        f, hero = self.setup_band(fight)
        f.register("strong", "unshakeable_menace", EffectSource.acquired("fell_ability"))
        outcome, _ = attempt(f, hero.ref, CombatTask.INTIMIDATE_FOE, feats=(10,), successes=(6, 6))
        assert set(outcome.affected) == {"weak"}


class TestRallyComrades:
    def test_a_bare_success_reaches_forward(self, fight) -> None:
        f = band(fight, heroes=3)
        a, b, c = f.state.active_heroes()
        a.stance = Stance.OPEN
        b.stance = Stance.FORWARD
        c.stance = Stance.DEFENSIVE
        outcome, _ = attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(4, 4))
        assert set(outcome.affected) == {b.ref}

    def test_one_icon_also_reaches_open(self, fight) -> None:
        f = band(fight, heroes=3)
        a, b, c = f.state.active_heroes()
        b.stance = Stance.FORWARD
        c.stance = Stance.DEFENSIVE
        outcome, _ = attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(6, 4))
        assert set(outcome.affected) == {a.ref, b.ref}

    def test_two_icons_reach_every_close_stance(self, fight) -> None:
        f = band(fight, heroes=3)
        a, b, c = f.state.active_heroes()
        b.stance = Stance.FORWARD
        c.stance = Stance.DEFENSIVE
        outcome, _ = attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(6, 6))
        assert set(outcome.affected) == {a.ref, b.ref, c.ref}

    def test_a_rearward_hero_is_never_rallied(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        b.stance = Stance.REARWARD
        outcome, _ = attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(6, 6))
        assert b.ref not in outcome.affected

    def test_the_dice_land_on_the_next_round(self, fight) -> None:
        # 08.10: +1d on attack rolls *next round*.
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        b.stance = Stance.FORWARD
        attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(4, 4))
        assert b.round_flags.attack_bonus_dice == 0
        assert b.pending_flags.attack_bonus_dice == 1
        b.begin_round()
        assert b.round_flags.attack_bonus_dice == 1

    def test_only_one_hero_may_attempt_it_in_a_round(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(4, 4))
        with pytest.raises(RuleViolation, match="only one hero"):
            attempt(f, b.ref, CombatTask.RALLY_COMRADES)

    def test_a_failed_attempt_still_uses_the_round_up(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        attempt(f, a.ref, CombatTask.RALLY_COMRADES, feats=(1,), successes=(1, 1))
        assert f.state.rallied_this_round
        with pytest.raises(RuleViolation, match="only one hero"):
            attempt(f, b.ref, CombatTask.RALLY_COMRADES)


class TestProtectCompanion:
    def test_the_next_attack_on_the_companion_loses_a_die_per_icon(self, fight) -> None:
        f = band(fight, heroes=2, stance=Stance.DEFENSIVE)
        a, b = f.state.active_heroes()
        outcome, _ = attempt(
            f,
            a.ref,
            CombatTask.PROTECT_COMPANION,
            companion=b.ref,
            feats=(10,),
            successes=(6, 4),
        )
        assert outcome.dice == 2  # one, plus one per Success icon
        assert b.round_flags.incoming_attack_penalty == 2

    def test_it_names_another_hero(self, fight) -> None:
        f = band(fight, stance=Stance.DEFENSIVE)
        a = f.state.active_heroes()[0]
        with pytest.raises(RuleViolation, match="names another hero"):
            attempt(f, a.ref, CombatTask.PROTECT_COMPANION)
        with pytest.raises(RuleViolation, match=r"names \*another\* hero"):
            attempt(f, a.ref, CombatTask.PROTECT_COMPANION, companion=a.ref)

    def test_the_companion_must_be_in_close_combat(self, fight) -> None:
        f = band(fight, heroes=2, stance=Stance.DEFENSIVE)
        a, b = f.state.active_heroes()
        b.stance = Stance.REARWARD
        with pytest.raises(RuleViolation, match="not in a close combat stance"):
            attempt(f, a.ref, CombatTask.PROTECT_COMPANION, companion=b.ref)

    def test_an_adversary_is_not_a_companion(self, fight) -> None:
        f = band(fight, stance=Stance.DEFENSIVE)
        a = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        with pytest.raises(RuleViolation, match=r"names \*another\* hero"):
            attempt(f, a.ref, CombatTask.PROTECT_COMPANION, companion=foe.ref)


class TestPrepareShot:
    def test_it_grants_dice_on_the_next_ranged_attack(self, fight) -> None:
        f = band(fight, heroes=3, stance=Stance.REARWARD)
        heroes = f.state.active_heroes()
        heroes[1].stance = Stance.FORWARD
        heroes[2].stance = Stance.FORWARD
        outcome, _ = attempt(
            f, heroes[0].ref, CombatTask.PREPARE_SHOT, feats=(10,), successes=(6, 6)
        )
        assert outcome.dice == 3
        assert heroes[0].round_flags.prepared_shot == 3

    def test_a_failure_grants_nothing(self, fight) -> None:
        f = band(fight, stance=Stance.REARWARD)
        hero = f.state.active_heroes()[0]
        outcome, _ = attempt(f, hero.ref, CombatTask.PREPARE_SHOT, feats=(1,), successes=(1, 1))
        assert outcome.dice == 0
        assert hero.round_flags.prepared_shot == 0


class TestTaskEvents:
    def test_one_event_carries_the_whole_result(self, fight) -> None:
        f = band(fight, heroes=2)
        a, b = f.state.active_heroes()
        b.stance = Stance.FORWARD
        outcome, events = attempt(
            f, a.ref, CombatTask.RALLY_COMRADES, feats=(10,), successes=(4, 4)
        )
        assert len(events) == 1
        event = events[0]
        assert event.kind is EventKind.COMBAT_TASK_RESOLVED
        assert event.payload["task"] == "rally_comrades"
        assert event.payload["tier"] == outcome.tier
        assert event.rolls == (outcome.roll,)


class TestBattleForInterference:
    """08.11. Note that a complication penalises the very roll that removes it: it
    modifies *all* rolls made by the heroes, the BATTLE roll included."""

    def battle(self, f, ref, *, feats, successes, **kw):
        rng = ScriptedRandomness(feats=list(feats), successes=list(successes))
        roll, events = battle_for_interference(f.state, ref, ctx=f.ctx, rng=rng, **kw)
        assert rng.exhausted, f"{rng.remaining} dice left over"
        return roll, events

    def test_a_bare_success_lasts_until_the_next_attack(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        roll, events = self.battle(
            f, hero.ref, feats=(10,), successes=(4, 4), gain=Interference.MODERATE_ADVANTAGE
        )
        assert roll.succeeded
        assert f.state.advantages[0].duration is Duration.NEXT_ATTACK
        assert events[0].kind is EventKind.INTERFERENCE_CHANGED

    def test_one_icon_makes_it_last_the_fight(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        self.battle(f, hero.ref, feats=(10,), successes=(6, 4), gain=Interference.GREATER_ADVANTAGE)
        assert f.state.advantages[0].duration is Duration.REST_OF_FIGHT

    def test_removing_a_complication_for_the_fight_drops_it(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        mud = Complication(level=Interference.MODERATELY_HINDERED)
        f.state.complications.append(mud)
        # One Success die, not two: the mud costs a die on the roll to get out of it.
        self.battle(f, hero.ref, feats=(10,), successes=(6,), remove=mud)
        assert f.state.complications == []
        assert f.state.hero_dice_modifier() == 0

    def test_removing_it_for_one_attack_cancels_without_deleting(self, fight) -> None:
        # A cancellation that lapses has to come back, so it is recorded as an equal and
        # opposite advantage that the next attack consumes.
        f = band(fight)
        hero = f.state.active_heroes()[0]
        mud = Complication(level=Interference.MODERATELY_HINDERED)
        f.state.complications.append(mud)
        self.battle(f, hero.ref, feats=(FeatFace.RUNE,), successes=(4,), remove=mud)
        assert f.state.hero_dice_modifier() == 0
        f.state.consume_next_attack_interference()
        assert f.state.hero_dice_modifier() == -1, "the mud is still there"

    def test_a_severe_complication_can_leave_the_feat_die_alone(self, fight) -> None:
        # 02.3.3: a character can be reduced to rolling only the Feat die, never fewer.
        f = band(fight)
        hero = f.state.active_heroes()[0]
        deep = Complication(level=Interference.SEVERELY_HINDERED)
        f.state.complications.append(deep)
        roll, _ = self.battle(f, hero.ref, feats=(FeatFace.RUNE,), successes=(), remove=deep)
        assert roll.request.dice_count == 0
        assert f.state.hero_dice_modifier() == 0

    def test_a_failure_changes_nothing(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        roll, events = self.battle(
            f, hero.ref, feats=(1,), successes=(1, 1), gain=Interference.MODERATE_ADVANTAGE
        )
        assert not roll.succeeded
        assert f.state.advantages == []
        assert events[0].payload["changed"] is False

    def test_it_spends_the_main_action(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        self.battle(
            f, hero.ref, feats=(10,), successes=(4, 4), gain=Interference.MODERATE_ADVANTAGE
        )
        assert hero.main_action_used

    def test_it_does_one_thing_or_the_other(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        with pytest.raises(StateError, match="either removes"):
            battle_for_interference(f.state, hero.ref, ctx=f.ctx, rng=ScriptedRandomness())
        with pytest.raises(StateError, match="either removes"):
            battle_for_interference(
                f.state,
                hero.ref,
                ctx=f.ctx,
                rng=ScriptedRandomness(),
                remove=Complication(level=Interference.MODERATELY_HINDERED),
                gain=Interference.MODERATE_ADVANTAGE,
            )

    def test_removing_a_complication_that_is_not_in_play_is_refused(self, fight) -> None:
        f = band(fight)
        hero = f.state.active_heroes()[0]
        with pytest.raises(RuleViolation, match="not in play"):
            self.battle(
                f,
                hero.ref,
                feats=(10,),
                successes=(4, 4),
                remove=Complication(level=Interference.MODERATELY_HINDERED),
            )
