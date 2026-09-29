"""Combat Tasks, and the BATTLE roll that shifts the odds (``08.10``, ``08.11``).

Every task is graded by icon tiers — pattern P4, which ``RollResult.tier`` owns — and
every one is tied to a stance. Each is a **main action** unless an effect has converted
it, which is what ``SECONDARY_ACTION_OPTIONS`` is for.

Two of the four grant their benefit to the **next** round, not this one. That is why
:class:`~tor.rules.combat.state.RoundFlags` comes in two buckets: everything here writes
to ``pending_flags`` when the spec says "next round", and to ``round_flags`` when it says
"this round" or "next attack".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tor.dice import Randomness
from tor.effects.hooks import ActionContribution, Hook
from tor.errors import RuleViolation, StateError
from tor.events import Event, EventKind
from tor.model.ids import AbilityId, CombatantId
from tor.rolls import RollPurpose, RollResult, attribute_tn, build_request, resolve
from tor.rules.combat.state import (
    CLOSE_STANCES,
    Advantage,
    Combatant,
    CombatState,
    Complication,
    Duration,
    Interference,
    Stance,
)
from tor.rules.context import RulesContext

__all__ = [
    "TASK_ABILITY",
    "TASK_STANCE",
    "CombatTask",
    "TaskDeclaration",
    "TaskOutcome",
    "apply_task",
    "battle_for_interference",
    "resolve_task",
]

BATTLE = AbilityId("battle")


class CombatTask(StrEnum):
    """The four of ``08.10``, one per stance."""

    INTIMIDATE_FOE = "intimidate_foe"
    RALLY_COMRADES = "rally_comrades"
    PROTECT_COMPANION = "protect_companion"
    PREPARE_SHOT = "prepare_shot"


#: ``08.10``: each task is tied to a stance, and each rolls one named Skill.
TASK_STANCE: dict[CombatTask, Stance] = {
    CombatTask.INTIMIDATE_FOE: Stance.FORWARD,
    CombatTask.RALLY_COMRADES: Stance.OPEN,
    CombatTask.PROTECT_COMPANION: Stance.DEFENSIVE,
    CombatTask.PREPARE_SHOT: Stance.REARWARD,
}
TASK_ABILITY: dict[CombatTask, AbilityId] = {
    CombatTask.INTIMIDATE_FOE: AbilityId("awe"),
    CombatTask.RALLY_COMRADES: AbilityId("enhearten"),
    CombatTask.PROTECT_COMPANION: BATTLE,
    CombatTask.PREPARE_SHOT: AbilityId("scan"),
}

#: ``08.10``: Intimidate Foe reaches Might 1 on a bare success, Might 2 with one icon, and
#: everything with two. Read as "the highest Might the tier reaches"; ``None`` is all.
_INTIMIDATE_REACH: dict[int, int | None] = {1: 1, 2: 2, 3: None}

#: ``08.10``: Rally Comrades reaches Forward, then Open, then every close-combat stance.
_RALLY_REACH: dict[int, tuple[Stance, ...]] = {
    1: (Stance.FORWARD,),
    2: (Stance.FORWARD, Stance.OPEN),
    3: CLOSE_STANCES,
}


@dataclass(frozen=True, slots=True)
class TaskDeclaration:
    """One attempt at a Combat Task."""

    actor: CombatantId
    task: CombatTask
    #: Protect Companion names another hero in a close combat stance.
    companion: CombatantId | None = None
    spend_hope: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0
    #: ``08.10``: an effect may convert a task to a secondary action.
    as_secondary: bool = False


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """What a task achieved, applied to nothing (``01.4``)."""

    declaration: TaskDeclaration
    roll: RollResult
    tier: int
    #: Intimidate Foe: the combatants the tier reached.
    affected: tuple[CombatantId, ...] = ()
    #: Rally Comrades and Prepare Shot: dice granted, and Protect Companion: dice denied.
    dice: int = 0

    @property
    def succeeded(self) -> bool:
        return self.tier > 0


def resolve_task(
    state: CombatState,
    decl: TaskDeclaration,
    *,
    ctx: RulesContext,
    rng: Randomness,
) -> TaskOutcome:
    """Roll one Combat Task (``08.10``). Applies nothing.

    The stance requirement is checked first and refused outright: a task is *tied* to its
    stance, so attempting Rally Comrades from Forward is an illegal move rather than an
    unlucky one. Only one hero may attempt Rally Comrades in a given round, which the state
    tracks rather than the caller.
    """
    actor = state.combatant(decl.actor)
    _check_task_is_legal(state, actor, decl, ctx=ctx)

    hero = actor.hero
    ability = TASK_ABILITY[decl.task]
    request = build_request(
        hero,
        ability,
        bus=ctx.bus(actor.ref),
        target_number=attribute_tn(
            hero.attributes.score(hero.attribute_for(ability)),
            short_campaign=ctx.short_campaign,
        ),
        purpose=RollPurpose.COMBAT_TASK,
        spend_hope=decl.spend_hope,
        bonus_dice=decl.bonus_dice + max(0, state.hero_dice_modifier()),
        penalty_dice=decl.penalty_dice + max(0, -state.hero_dice_modifier()),
        weary=hero.conditions.weary or actor.round_flags.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=state.environment,
        extra={"task": decl.task.value},
    )
    roll = resolve(request, rng)
    tier = roll.tier()
    if decl.task is CombatTask.INTIMIDATE_FOE:
        return TaskOutcome(
            declaration=decl, roll=roll, tier=tier, affected=_intimidated(state, tier, ctx=ctx)
        )
    if decl.task is CombatTask.RALLY_COMRADES:
        return TaskOutcome(
            declaration=decl, roll=roll, tier=tier, affected=_rallied(state, tier), dice=1
        )
    # Protect Companion and Prepare Shot both scale one die per icon beyond the first.
    return TaskOutcome(declaration=decl, roll=roll, tier=tier, dice=roll.magnitude(1))


def _check_task_is_legal(
    state: CombatState, actor: Combatant, decl: TaskDeclaration, *, ctx: RulesContext
) -> None:
    if not actor.is_hero:
        raise RuleViolation(
            f"{actor.ref} is an adversary; ``08.10``'s tasks belong to the heroes",
            rule_reference="combat_task_is_a_hero_action",
        )
    if not actor.active:
        raise StateError(f"{actor.ref} is no longer in the fight")
    required = TASK_STANCE[decl.task]
    if actor.stance is not required:
        raise RuleViolation(
            f"{decl.task.value} is attempted from {required.value}, not "
            f"{actor.stance.value if actor.stance else 'no stance'}",
            rule_reference="combat_task_stance",
        )
    if decl.task is CombatTask.RALLY_COMRADES and state.rallied_this_round:
        raise RuleViolation(
            "only one hero may attempt Rally Comrades in a given round",
            rule_reference="rally_once_per_round",
        )
    if decl.task is CombatTask.PROTECT_COMPANION:
        _check_companion(state, actor, decl)
    if decl.as_secondary and not _convertible_to_secondary(actor, decl, ctx=ctx):
        raise RuleViolation(
            f"nothing converts {decl.task.value} to a secondary action for {actor.ref}",
            rule_reference="combat_task_is_a_main_action",
        )


def _check_companion(state: CombatState, actor: Combatant, decl: TaskDeclaration) -> None:
    if decl.companion is None:
        raise RuleViolation(
            "Protect Companion names another hero",
            rule_reference="protect_companion_target",
        )
    companion = state.combatant(decl.companion)
    if companion.ref == actor.ref or not companion.is_hero:
        raise RuleViolation(
            "Protect Companion names *another* hero",
            rule_reference="protect_companion_target",
        )
    if companion.stance not in CLOSE_STANCES:
        raise RuleViolation(
            f"{companion.ref} is not in a close combat stance",
            rule_reference="protect_companion_target",
        )


def _convertible_to_secondary(
    actor: Combatant, decl: TaskDeclaration, *, ctx: RulesContext
) -> bool:
    """``08.10``: a task is a main action unless an effect converts it (``04.3``)."""
    hook_ctx = ctx.hook_context(
        Hook.SECONDARY_ACTION_OPTIONS,
        actor.actor,
        stance=None if actor.stance is None else actor.stance.value,
        task=decl.task.value,
    )
    for contribution in ctx.bus(actor.ref).collect(Hook.SECONDARY_ACTION_OPTIONS, hook_ctx):
        if isinstance(contribution, ActionContribution) and (
            contribution.payload.get("task") == decl.task.value
        ):
            return True
    return False


def _intimidated(state: CombatState, tier: int, *, ctx: RulesContext) -> tuple[CombatantId, ...]:
    """Which adversaries Intimidate Foe reaches (``08.10``).

    Some creatures are immune unless a magical success is obtained, and some lose a drive
    point when intimidated; both are ``ActionContribution``s from their own Fell Abilities,
    never branches here (``08.10``).
    """
    if tier == 0:
        return ()
    reach = _INTIMIDATE_REACH[tier]
    affected: list[CombatantId] = []
    for foe in state.active_adversaries():
        if reach is not None and foe.adversary.template.might > reach:
            continue
        if _immune_to_task(foe, CombatTask.INTIMIDATE_FOE, ctx=ctx):
            continue
        affected.append(foe.ref)
    return tuple(affected)


def _immune_to_task(foe: Combatant, task: CombatTask, *, ctx: RulesContext) -> bool:
    hook_ctx = ctx.hook_context(Hook.STANCE_OPTIONS, foe.actor, task=task.value)
    for contribution in ctx.bus(foe.ref).collect(Hook.STANCE_OPTIONS, hook_ctx):
        if isinstance(contribution, ActionContribution) and (
            contribution.action == "immune_to_task"
            and contribution.payload.get("task") == task.value
        ):
            return True
    return False


def _rallied(state: CombatState, tier: int) -> tuple[CombatantId, ...]:
    if tier == 0:
        return ()
    stances = _RALLY_REACH[tier]
    return tuple(c.ref for c in state.active_heroes() if c.stance in stances)


def apply_task(state: CombatState, outcome: TaskOutcome, *, ctx: RulesContext) -> list[Event]:
    """Commit a Combat Task (``08.10``, ``01.4``).

    Note which bucket each writes to. Intimidate Foe and Rally Comrades land in
    ``pending_flags`` — both say "next round" — while Protect Companion and Prepare Shot
    land in ``round_flags``, because both are consumed by the next attack.
    """
    decl = outcome.declaration
    actor = state.combatant(decl.actor)
    if decl.as_secondary:
        actor.spend_secondary_action()
    else:
        actor.spend_main_action()
    if decl.task is CombatTask.RALLY_COMRADES:
        state.rallied_this_round = True

    if outcome.succeeded:
        if decl.task is CombatTask.INTIMIDATE_FOE:
            for ref in outcome.affected:
                state.combatant(ref).pending_flags.weary = True
        elif decl.task is CombatTask.RALLY_COMRADES:
            for ref in outcome.affected:
                state.combatant(ref).pending_flags.attack_bonus_dice += outcome.dice
        elif decl.task is CombatTask.PROTECT_COMPANION:
            assert decl.companion is not None
            state.combatant(decl.companion).round_flags.incoming_attack_penalty += outcome.dice
        else:
            actor.round_flags.prepared_shot += outcome.dice

    return [
        Event(
            kind=EventKind.COMBAT_TASK_RESOLVED,
            actor=decl.actor,
            payload={
                "task": decl.task.value,
                "tier": outcome.tier,
                "affected": list(outcome.affected),
                "dice": outcome.dice if outcome.succeeded else 0,
                "companion": decl.companion,
            },
            rolls=(outcome.roll,),
        )
    ]


# -- complications and advantages ------------------------------------------------------


def battle_for_interference(
    state: CombatState,
    actor_ref: CombatantId,
    *,
    ctx: RulesContext,
    rng: Randomness,
    remove: Complication | None = None,
    gain: Interference | None = None,
    spend_hope: bool = False,
) -> tuple[RollResult, list[Event]]:
    """Spend a main action on a BATTLE roll to shift the odds (``08.11``).

    Success alone lasts only until the **next attack roll**; one or more Success icons make
    it last the **remainder of the fight**. That is the whole grading, and it is the same
    whether the roll removes a complication or gains an advantage.
    """
    if (remove is None) == (gain is None):
        raise StateError("a BATTLE roll either removes a complication or gains an advantage")
    actor = state.combatant(actor_ref)
    hero = actor.hero
    actor.spend_main_action()

    request = build_request(
        hero,
        BATTLE,
        bus=ctx.bus(actor_ref),
        target_number=attribute_tn(
            hero.attributes.score(hero.attribute_for(BATTLE)),
            short_campaign=ctx.short_campaign,
        ),
        purpose=RollPurpose.COMBAT_TASK,
        spend_hope=spend_hope,
        bonus_dice=max(0, state.hero_dice_modifier()),
        penalty_dice=max(0, -state.hero_dice_modifier()),
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=state.environment,
        extra={"interference": True},
    )
    roll = resolve(request, rng)
    if not roll.succeeded:
        return roll, [_interference_event(actor_ref, roll, changed=False)]

    duration = Duration.REST_OF_FIGHT if roll.icons else Duration.NEXT_ATTACK
    if remove is not None:
        _remove_complication(state, remove, duration)
    else:
        assert gain is not None
        state.advantages.append(Advantage(level=gain, duration=duration))
    return roll, [_interference_event(actor_ref, roll, changed=True, duration=duration)]


def _remove_complication(
    state: CombatState, complication: Complication, duration: Duration
) -> None:
    """Cancel one complication.

    A cancellation that lasts only until the next attack cannot simply drop the
    complication — it has to come back. It is recorded as an advantage of equal and
    opposite size, which the next attack consumes like any other ``NEXT_ATTACK`` entry, so
    the netting in ``CombatState.hero_dice_modifier`` does the work.
    """
    if complication not in state.complications:
        raise RuleViolation(
            "that complication is not in play", rule_reference="complication_not_present"
        )
    if duration is Duration.REST_OF_FIGHT:
        state.complications.remove(complication)
        return
    state.advantages.append(
        Advantage(
            level=_MIRROR[complication.level],
            duration=Duration.NEXT_ATTACK,
            description=f"cancels {complication.level.value}",
        )
    )


#: The advantage that exactly offsets each complication, for a cancellation that lapses.
_MIRROR: dict[Interference, Interference] = {
    Interference.MODERATELY_HINDERED: Interference.MODERATE_ADVANTAGE,
    Interference.SEVERELY_HINDERED: Interference.GREATER_ADVANTAGE,
}


def _interference_event(
    actor: CombatantId,
    roll: RollResult,
    *,
    changed: bool,
    duration: Duration | None = None,
) -> Event:
    return Event(
        kind=EventKind.INTERFERENCE_CHANGED,
        actor=actor,
        payload={"changed": changed, "duration": None if duration is None else str(duration)},
        rolls=(roll,),
    )
