"""The shape of a fight: onset, volleys, stance, ordering, and the boundaries (``08.2``).

Onset, opening volleys, then Close Quarters Rounds repeating stance → engagement → action
resolution. Everything the Loremaster decides — the circumstances, how many volleys, who
volleys first, whether surprise even needs a roll — arrives as an input.

The round boundary lives here and nowhere else. :func:`end_round` is the only place
``RoundFlags`` expire, which is what ``08.1`` means by never letting a subsystem
hand-manage them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from tor.dice import Randomness
from tor.effects.hooks import FlagContribution, Hook
from tor.errors import RuleViolation, StateError
from tor.events import Event, EventKind
from tor.model.hero import Hero
from tor.model.ids import AbilityId, CombatantId
from tor.rolls import RollPurpose, RollResult, attribute_tn, build_request, resolve
from tor.rules.combat.engagement import may_take_rearward
from tor.rules.combat.state import (
    Combatant,
    CombatPhase,
    CombatState,
    Duration,
    Stance,
    stance_order_key,
)
from tor.rules.context import RulesContext
from tor.rules.resources import recompute_conditions

#: The default Skill for an ambushed Company. ``08.2.1`` lets the Loremaster name
#: another where circumstances warrant, or rule that no roll is needed at all.
AWARENESS = AbilityId("awareness")

__all__ = [
    "AWARENESS",
    "SurpriseInput",
    "action_order",
    "begin_combat",
    "begin_round",
    "end_combat",
    "end_round",
    "opening_volley_count",
    "resolve_surprise",
    "set_stance",
]


@dataclass(frozen=True, slots=True)
class SurpriseInput:
    """``08.2.1``. The Skill is the Loremaster's choice, not the engine's.

    ``08.2.1`` names four the Loremaster may reach for — AWARENESS, STEALTH, BATTLE for
    prepared military action, HUNTING in wild terrain — and says they may also rule that
    no roll is needed at all, which is what ``automatic`` is.
    """

    direction: Literal["company_ambushed", "company_ambushes", "none"] = "none"
    ability: AbilityId = AWARENESS
    automatic: bool = False
    target_number: int | None = None


def begin_combat(
    state: CombatState,
    *,
    volleys: int = 0,
    ctx: RulesContext,
) -> list[Event]:
    """Open a fight (``08.2.1``).

    The Loremaster sets the circumstances, the number of opening volleys and who is an
    eligible target; none of it is an engine decision, so all of it arrives set up on the
    state.
    """
    if not state.heroes or not state.adversaries:
        raise StateError("a fight needs combatants on both sides")
    if volleys < 0:
        raise StateError(f"a fight cannot have {volleys} opening volleys")
    state.phase = CombatPhase.ONSET
    state.round_number = 0
    state.volleys_allowed = volleys
    for combatant in state.combatants.values():
        if not combatant.is_hero:
            combatant.adversary.begin_round()
    return [
        Event(
            kind=EventKind.COMBAT_BEGAN,
            payload={
                "heroes": sorted(str(r) for r in state.heroes),
                "adversaries": sorted(str(r) for r in state.adversaries),
                "volleys": volleys,
            },
        )
    ]


def resolve_surprise(
    state: CombatState,
    surprise: SurpriseInput,
    *,
    ctx: RulesContext,
    rng: Randomness,
    participants: Sequence[CombatantId] | None = None,
) -> tuple[Mapping[CombatantId, RollResult], list[Event]]:
    """Resolve the ambush (``08.2.1``).

    Skipped entirely when all sides are aware of each other. Otherwise the two directions
    differ in a way that matters: **being** ambushed is resolved per hero — those who fail
    are surprised and the rest are not — while **ambushing** is all-or-nothing, because
    ``08.2.1`` says *all* must succeed for the surprise to take effect. One hero who snaps
    a twig costs the whole Company its advantage.

    A surprised combatant makes no opening volley and takes no action in the first Close
    Quarters Round.
    """
    if surprise.direction == "none":
        return {}, []

    rolls: dict[CombatantId, RollResult] = {}
    if not surprise.automatic:
        for ref in participants if participants is not None else list(state.heroes):
            combatant = state.heroes[ref]
            if not combatant.active:
                continue
            rolls[ref] = _surprise_roll(combatant, surprise, ctx=ctx, rng=rng)

    if surprise.direction == "company_ambushed":
        surprised = [ref for ref, roll in rolls.items() if not roll.succeeded]
        for ref in surprised:
            state.heroes[ref].surprised = True
        took_effect = bool(surprised)
    else:
        took_effect = surprise.automatic or all(roll.succeeded for roll in rolls.values())
        if took_effect:
            for combatant in state.adversaries.values():
                combatant.surprised = True

    return rolls, [
        Event(
            kind=EventKind.SURPRISE_RESOLVED,
            payload={
                "direction": surprise.direction,
                "ability": str(surprise.ability),
                "automatic": surprise.automatic,
                "took_effect": took_effect,
                "surprised": sorted(str(c.ref) for c in state.combatants.values() if c.surprised),
            },
            rolls=tuple(rolls[ref] for ref in sorted(rolls)),
        )
    ]


def _surprise_roll(
    combatant: Combatant, surprise: SurpriseInput, *, ctx: RulesContext, rng: Randomness
) -> RollResult:
    hero = combatant.hero
    target_number = surprise.target_number
    if target_number is None:
        target_number = attribute_tn(
            hero.attributes.score(hero.attribute_for(surprise.ability)),
            short_campaign=ctx.short_campaign,
        )
    request = build_request(
        hero,
        surprise.ability,
        bus=ctx.bus(combatant.ref),
        target_number=target_number,
        purpose=RollPurpose.SKILL,
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=ctx.environment,
        extra={"surprise": surprise.direction},
    )
    return resolve(request, rng)


def opening_volley_count(state: CombatState, ref: CombatantId, *, ctx: RulesContext) -> int:
    """How many volleys this combatant may loose (``08.2.2``).

    The Loremaster's figure for the scene, which zero is a legal value of.
    ``OPENING_VOLLEY_COUNT`` lets an effect grant an extra volley even when none are
    allowed — **but not when the wielder is surprised**, which is the one condition
    ``08.2.2`` puts on it.
    """
    combatant = state.combatant(ref)
    if combatant.surprised or not combatant.active:
        return 0
    hook_ctx = ctx.hook_context(Hook.OPENING_VOLLEY_COUNT, combatant.actor)
    granted = ctx.bus(ref).apply_numeric(Hook.OPENING_VOLLEY_COUNT, hook_ctx, state.volleys_allowed)
    return max(0, granted.value)


def set_stance(
    state: CombatState,
    ref: CombatantId,
    stance: Stance,
    *,
    ctx: RulesContext,
    rearward_override: bool = False,
) -> list[Event]:
    """Choose a hero's stance for the round (``08.2.3``).

    Close-combat stances may be assumed freely. Rearward has entry requirements, which the
    Loremaster may waive — and which one Cultural Virtue relaxes through ``STANCE_OPTIONS``,
    so an effect that says so lowers the companion requirement without the engine knowing
    which Virtue it was.

    A seized hero has no choice at all: ``12.5`` pins them in Forward.
    """
    combatant = state.heroes.get(ref)
    if combatant is None:
        raise StateError(f"{ref!r} is not a hero in this fight")
    if combatant.seized_by is not None and stance is not Stance.FORWARD:
        raise RuleViolation(
            f"{ref} is seized and can only fight in Forward stance",
            rule_reference="seized_forward_only",
        )
    if stance is Stance.REARWARD and not may_take_rearward(
        state, ref, override=rearward_override or _rearward_relaxed(combatant, state, ctx=ctx)
    ):
        raise RuleViolation(
            f"{ref} may not take Rearward this round",
            rule_reference="rearward_requirements",
            suggestion=(
                "the enemy may not outnumber the Company more than two to one, and each "
                "hero in Rearward needs two others in close combat"
            ),
        )
    combatant.stance = stance
    return [
        Event(
            kind=EventKind.STANCE_CHOSEN,
            actor=ref,
            payload={"stance": stance.value},
        )
    ]


def _rearward_relaxed(combatant: Combatant, state: CombatState, *, ctx: RulesContext) -> bool:
    """``08.2.3``: at least one Cultural Virtue relaxes the companion requirement to one.

    Expressed as a ``STANCE_OPTIONS`` flag rather than a second copy of the arithmetic:
    the relaxation halves the companions needed, which for the one-hero case is the same
    as waiving it.
    """
    hook_ctx = ctx.hook_context(Hook.STANCE_OPTIONS, combatant.actor, stance=Stance.REARWARD.value)
    for contribution in ctx.bus(combatant.ref).collect(Hook.STANCE_OPTIONS, hook_ctx):
        if isinstance(contribution, FlagContribution) and contribution.flag == "rearward_relaxed":
            companions = sum(
                1 for c in state.active_heroes() if c.ref != combatant.ref and c.in_close_combat
            )
            return companions >= 1
    return False


def action_order(state: CombatState) -> list[Combatant]:
    """Who acts, in order (``08.2.5``).

    **All heroes act, then all adversaries.** Within the Company, strict stance order —
    Forward, Open, Defensive, Rearward — with ties resolved by player choice, which the
    engine renders as a stable sort so the caller's own ordering survives. Within the
    opposition, the same order using the stance of the hero each adversary is attacking;
    those that stood back unengaged with ranged weapons resolve last, which falls out of
    them having no inherited stance.

    A surprised combatant takes no action in the first Close Quarters Round.
    """

    def ready(combatant: Combatant) -> bool:
        return combatant.active and not (combatant.surprised and state.round_number <= 1)

    heroes = sorted(
        (c for c in state.active_heroes() if ready(c)),
        key=lambda c: stance_order_key(c.stance),
    )
    adversaries = sorted(
        (c for c in state.active_adversaries() if ready(c)),
        key=lambda c: stance_order_key(c.effective_stance(state)),
    )
    return [*heroes, *adversaries]


def begin_round(state: CombatState, *, ctx: RulesContext) -> list[Event]:
    """Open a Close Quarters Round (``08.2``).

    Swaps every combatant's flag buckets, so what the last round made pending applies now,
    and re-derives the adversaries' Weariness — ``12.2`` checks that at the *start* of the
    round, not at the moment a pool empties, which is why spending a creature's last point
    mid-round does not make it Weary until the next one.
    """
    if state.over:
        raise StateError("this fight is already decided")
    state.round_number += 1
    state.phase = CombatPhase.ROUND_STANCE
    state.rallied_this_round = False
    for combatant in state.combatants.values():
        combatant.begin_round()

    events: list[Event] = [Event(kind=EventKind.ROUND_BEGAN, payload={"round": state.round_number})]
    for combatant in state.combatants.values():
        if not combatant.active:
            continue
        hook_ctx = ctx.hook_context(Hook.ON_ROUND_START, combatant.actor)
        contributions = ctx.bus(combatant.ref).collect(Hook.ON_ROUND_START, hook_ctx)
        ctx.consume(combatant.ref, contributions, hook_ctx)
    return events


def end_round(state: CombatState, *, ctx: RulesContext) -> list[Event]:
    """Close a round (``08.1``, ``08.11``).

    Fires ``ON_ROUND_END``, drops the complications and advantages that lasted only until
    the next attack, and leaves the flag buckets alone — :func:`begin_round` swaps them, so
    that what this round made pending survives to be applied.
    """
    for combatant in state.combatants.values():
        if not combatant.active:
            continue
        hook_ctx = ctx.hook_context(Hook.ON_ROUND_END, combatant.actor)
        contributions = ctx.bus(combatant.ref).collect(Hook.ON_ROUND_END, hook_ctx)
        ctx.consume(combatant.ref, contributions, hook_ctx)
    state.consume_next_attack_interference()
    state.phase = CombatPhase.ROUND_ACTIONS
    return [Event(kind=EventKind.ROUND_ENDED, payload={"round": state.round_number})]


def end_combat(state: CombatState, *, ctx: RulesContext) -> list[Event]:
    """Close the fight (``08.14``).

    Clears every round flag, fires ``ON_COMBAT_END``, clears Moderate injuries — which
    ``08.8`` says recover fully within hours at the end of the combat — and recomputes the
    conditions for every participant. Adversaries reduced to zero Endurance keep their
    ``taken_out`` reason, so the Loremaster can still rule them alive but incapacitated.
    """
    events: list[Event] = []
    for combatant in state.combatants.values():
        combatant.round_flags.clear()
        combatant.pending_flags.clear()
        hook_ctx = ctx.hook_context(Hook.ON_COMBAT_END, combatant.actor)
        contributions = ctx.bus(combatant.ref).collect(Hook.ON_COMBAT_END, hook_ctx)
        ctx.consume(combatant.ref, contributions, hook_ctx)
        if combatant.is_hero:
            events += _recover_hero(combatant, ctx=ctx)

    state.complications = [c for c in state.complications if c.duration is not Duration.NEXT_ATTACK]
    state.advantages = [a for a in state.advantages if a.duration is not Duration.NEXT_ATTACK]
    state.phase = CombatPhase.RESOLVED
    events.append(
        Event(
            kind=EventKind.COMBAT_ENDED,
            payload={
                "rounds": state.round_number,
                "heroes_standing": sorted(str(c.ref) for c in state.active_heroes()),
                "adversaries_standing": sorted(str(c.ref) for c in state.active_adversaries()),
                "taken_out": {
                    str(c.ref): str(c.adversary.taken_out)
                    for c in state.adversaries.values()
                    if c.adversary.taken_out is not None
                },
            },
        )
    )
    return events


def _recover_hero(combatant: Combatant, *, ctx: RulesContext) -> list[Event]:
    """``08.8``: a Moderate Wound recovers fully within hours at the end of the combat.

    Recognised by carrying the Wounded flag with no mending days recorded — which is
    exactly what the Moderate row leaves behind, since only the Severe row writes days.
    A hero still Dying keeps everything: their clock belongs to ``08.9``.
    """
    hero: Hero = combatant.hero
    events: list[Event] = []
    if hero.conditions.wounded and hero.injury_days == 0 and not hero.dying:
        hero.conditions.wounded = False
        events.append(
            Event(
                kind=EventKind.WOUND_HEALED,
                actor=combatant.ref,
                payload={"severity": "moderate", "reason": "combat_ended"},
            )
        )
    events += recompute_conditions(hero, ctx=ctx)
    return events
