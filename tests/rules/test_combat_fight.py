"""A whole fight, driven through the public surface (spec 08.2).

The unit tests above each assert one rule. This one asserts that the rules compose: a
Company built by `tor.rules.creation` from `content/example/`, mustered against real stat
blocks, taken through onset, stance, engagement, action resolution and the round boundary
until one side is gone.

19.8's golden transcripts wait on the event log of build step 16; this is the same fight
without the recorded transcript, which is what can be asserted today.
"""

from __future__ import annotations

import pytest

from tor.dice import ScriptedRandomness
from tor.effects.bus import EffectBus, EffectSource
from tor.model.adversary import AdversaryInstance
from tor.model.ids import (
    AbilityId,
    AdversaryInstanceId,
    CallingId,
    CultureId,
    EffectId,
    HeroId,
    ItemId,
)
from tor.rules import creation
from tor.rules.combat import (
    AttackDeclaration,
    CombatPhase,
    CombatState,
    EngagementPlan,
    SpendPlan,
    Stance,
    action_order,
    apply_attack,
    begin_combat,
    begin_round,
    end_combat,
    end_round,
    resolve_attack,
    resolve_engagement,
    roll_attack,
    set_stance,
)
from tor.rules.combat.attack import hero_attack_form
from tor.rules.combat.tasks import CombatTask, TaskDeclaration, apply_task, resolve_task
from tor.rules.context import RulesContext


def created_hero(pack, hero_id: str, *, shield: bool):
    """A hero off the real creation pipeline, not hand-assembled."""
    draft = creation.begin_hero()
    draft, _ = creation.choose_culture(draft, CultureId("example_folk"), pack)
    draft, _ = creation.choose_attributes(draft, creation.AttributeChoice(set_index=6), pack)
    draft, _ = creation.choose_skills(
        draft,
        creation.SkillChoice(
            favoured=(AbilityId("hunting"),),
            proficiencies=(AbilityId("swords"), AbilityId("bows")),
        ),
        pack,
    )
    draft, _ = creation.choose_features(
        draft,
        [creation.FeatureChoice(EffectId("bold")), creation.FeatureChoice(EffectId("eager"))],
        pack,
    )
    draft, _ = creation.choose_calling(
        draft,
        creation.CallingChoice(
            CallingId("example_calling"), favoured=(AbilityId("battle"), AbilityId("enhearten"))
        ),
        pack,
    )
    draft, _ = creation.spend_previous_experience(
        draft, creation.ExperienceChoice(targets={AbilityId("swords"): 3}), pack
    )
    draft, _ = creation.choose_gear(
        draft,
        creation.GearChoice(
            weapons=(creation.WeaponSelection(ItemId("example_blade")),),
            armour=ItemId("example_mail"),
            helm=ItemId("example_helm"),
            shield=ItemId("example_buckler") if shield else None,
        ),
        pack,
    )
    draft, _ = creation.choose_reward_and_virtue(
        draft,
        creation.RewardAndVirtueChoice(
            reward=creation.RewardChoice(EffectId("keen"), ItemId("example_blade")),
            virtue=creation.VirtueChoice(EffectId("hardiness")),
        ),
        pack,
    )
    draft, _ = creation.choose_identity(
        draft, creation.IdentityChoice(name=hero_id.title(), age=30), pack
    )
    bus = EffectBus()
    return creation.build_hero(draft, pack, bus=bus, hero_id=HeroId(hero_id)), bus


@pytest.fixture
def skirmish(pack):
    """Two created heroes against three minions, with a bus apiece."""
    state = CombatState()
    buses: dict[object, EffectBus] = {}
    heroes = []
    for name, shield in (("aldor", True), ("beren", False)):
        hero, bus = created_hero(pack, name, shield=shield)
        state.add_hero(hero, stance=Stance.OPEN)
        buses[hero.id] = bus
        heroes.append(hero)

    foes = []
    for n in range(3):
        instance = AdversaryInstance.of(
            pack.stat_block("example_minion"), AdversaryInstanceId(f"orc{n}")
        )
        state.add_adversary(instance)
        bus = EffectBus()
        for ability in instance.template.fell_abilities:
            bus.register(pack.instantiate(ability), EffectSource.acquired("fell_ability"))
        buses[instance.instance_id] = bus
        foes.append(instance)

    ctx = RulesContext(gear=pack, buses=buses)
    return state, ctx, heroes, foes, pack


class TestAWholeFight:
    def test_the_sequence_runs_to_a_decision(self, skirmish) -> None:
        state, ctx, heroes, _foes, pack = skirmish
        log = list(begin_combat(state, volleys=1, ctx=ctx))
        assert state.phase is CombatPhase.ONSET

        rounds = 0
        # Two queues, because the two rolls want opposite luck: the Company lands every
        # blow, and every PROTECTION roll under them fails. What is under test is the
        # sequence, not the dice.
        attacks = ScriptedRandomness(feats=[10] * 40, successes=[6] * 200)
        defence = ScriptedRandomness(feats=[1] * 40, successes=[1] * 200)
        while not state.over and rounds < 5:
            rounds += 1
            log += begin_round(state, ctx=ctx)

            for hero in state.active_heroes():
                log += set_stance(state, hero.ref, Stance.FORWARD, ctx=ctx)

            standing = state.active_adversaries()
            resolve_engagement(
                state,
                EngagementPlan(
                    pairs=tuple(
                        (hero.ref, foe.ref)
                        for hero, foe in zip(state.active_heroes(), standing, strict=False)
                    )
                ),
                ctx=ctx,
            )

            for combatant in action_order(state):
                if not combatant.is_hero or state.over:
                    continue
                target = next(iter(state.active_adversaries()), None)
                if target is None:
                    break
                form = hero_attack_form(combatant.hero, combatant.hero.gear.weapons[0], ctx=ctx)
                decl = AttackDeclaration(attacker=combatant.ref, target=target.ref, form=form)
                attack = roll_attack(state, decl, ctx=ctx, rng=attacks)
                outcome = resolve_attack(
                    state,
                    attack,
                    SpendPlan(),
                    ctx=ctx,
                    rng=defence,
                    severity_table=pack.table("wound_severity"),
                )
                log += apply_attack(state, outcome, ctx=ctx)
                combatant.spend_main_action()

            log += end_round(state, ctx=ctx)

        assert state.over, f"undecided after {rounds} rounds"
        assert not state.active_adversaries()
        assert rounds == 2, "two heroes, three foes, one kill each per round"
        log += end_combat(state, ctx=ctx)
        assert state.phase is CombatPhase.RESOLVED
        assert log[-1].payload["adversaries_standing"] == []
        assert sorted(log[-1].payload["taken_out"]) == ["orc0", "orc1", "orc2"]
        for hero in heroes:
            hero.validate()

    def test_a_task_in_one_round_pays_off_in_the_next(self, skirmish) -> None:
        # The two-bucket RoundFlags, end to end: Rally Comrades grants +1d *next* round,
        # and the extra die shows up in the attack that round rolls.
        state, ctx, _heroes, _foes, _pack = skirmish
        begin_combat(state, ctx=ctx)
        begin_round(state, ctx=ctx)

        rallier, fighter = state.active_heroes()
        set_stance(state, rallier.ref, Stance.OPEN, ctx=ctx)
        set_stance(state, fighter.ref, Stance.FORWARD, ctx=ctx)
        outcome = resolve_task(
            state,
            TaskDeclaration(actor=rallier.ref, task=CombatTask.RALLY_COMRADES),
            ctx=ctx,
            rng=ScriptedRandomness(feats=[10], successes=[4, 4]),
        )
        apply_task(state, outcome, ctx=ctx)
        assert fighter.ref in outcome.affected
        assert fighter.round_flags.attack_bonus_dice == 0

        end_round(state, ctx=ctx)
        begin_round(state, ctx=ctx)
        set_stance(state, fighter.ref, Stance.FORWARD, ctx=ctx)
        assert fighter.round_flags.attack_bonus_dice == 1

        form = hero_attack_form(fighter.hero, fighter.hero.gear.weapons[0], ctx=ctx)
        decl = AttackDeclaration(
            attacker=fighter.ref, target=state.active_adversaries()[0].ref, form=form
        )
        # swords 3, +1 Forward, +1 rallied.
        rng = ScriptedRandomness(feats=[2], successes=[4] * 5)
        roll_attack(state, decl, ctx=ctx, rng=rng)
        assert rng.exhausted

    def test_a_slain_foe_releases_the_hero_it_held(self, skirmish) -> None:
        # 08.2.4's "unengaged mid-round", through the real pipeline.
        state, ctx, _heroes, _foes, pack = skirmish
        begin_combat(state, ctx=ctx)
        begin_round(state, ctx=ctx)
        hero = state.active_heroes()[0]
        set_stance(state, hero.ref, Stance.FORWARD, ctx=ctx)
        doomed = state.active_adversaries()[0]
        resolve_engagement(state, EngagementPlan(pairs=((hero.ref, doomed.ref),)), ctx=ctx)
        assert hero.engaged_with == {doomed.ref}

        form = hero_attack_form(hero.hero, hero.hero.gear.weapons[0], ctx=ctx)
        decl = AttackDeclaration(attacker=hero.ref, target=doomed.ref, form=form)
        rng = ScriptedRandomness(feats=[10], successes=[6] * 4)
        attack = roll_attack(state, decl, ctx=ctx, rng=rng)
        outcome = resolve_attack(
            state,
            attack,
            SpendPlan(),
            ctx=ctx,
            rng=ScriptedRandomness(feats=[1], successes=[1] * 3),
            severity_table=pack.table("wound_severity"),
        )
        apply_attack(state, outcome, ctx=ctx)
        assert not doomed.active
        assert hero.engaged_with == set(), "the hero may now pick another to attack"
