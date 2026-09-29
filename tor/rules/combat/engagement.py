"""Who may stand where, and who may close with whom (``08.2.3``, ``08.2.4``).

Engagement **persists** across rounds. It is re-run only for combatants who became
unengaged, which is why :func:`resolve_engagement` leaves existing pairings alone and
:func:`unengage` is the one way they come apart.

Nothing here rolls a die. Every judgement the Loremaster is entitled to make — waiving the
Rearward requirements on a narrow ledge, deciding which hero a foe standing back shoots at
— arrives as an input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tor.effects.hooks import Hook
from tor.errors import RuleViolation
from tor.model.adversary import AdversarySize
from tor.model.ids import CombatantId
from tor.rules.combat.state import CLOSE_STANCES, Combatant, CombatState, Stance
from tor.rules.context import RulesContext

__all__ = [
    "FOES_PER_HERO",
    "HEROES_PER_FOE",
    "EngagementPlan",
    "engagement_limit",
    "may_engage",
    "may_take_rearward",
    "resolve_engagement",
    "unengage",
]

#: ``08.2.4``: how many foes of each size may engage one hero.
FOES_PER_HERO: Mapping[AdversarySize, int] = {
    AdversarySize.HUMAN: 3,
    AdversarySize.LARGE: 2,
}

#: ``08.2.4``: how many heroes may engage one foe of each size. A large creature offers
#: more sides to attack from, so the numbers run the other way.
HEROES_PER_FOE: Mapping[AdversarySize, int] = {
    AdversarySize.HUMAN: 3,
    AdversarySize.LARGE: 6,
}

#: ``08.2.3``: Rearward is refused when the enemy outnumbers the Company more than this.
REARWARD_ENEMY_RATIO = 2
#: ``08.2.3``: and every hero in Rearward needs this many others in close combat.
REARWARD_COMPANIONS = 2


def engagement_limit(one: Combatant, other: Combatant, *, ctx: RulesContext) -> int:
    """How many of ``other``'s side may engage ``one`` (``08.2.4``).

    Keyed on the *size of the side doing the engaging* and on which side ``one`` is,
    because the two columns of ``08.2.4``'s table are not symmetric: three human-sized
    foes may surround a hero, and three heroes may surround a human-sized foe, but six
    heroes may surround a troll while that troll and one other are all that may reach a
    single hero.

    ``MODIFY_ENGAGEMENT`` adjusts the limit — a creature that fights as though it had more
    reach, a hero who holds a doorway. Never below one: a limit of zero would mean nobody
    could ever close, which no effect in ``04`` is describing.
    """
    base = HEROES_PER_FOE[one.size] if not one.is_hero else FOES_PER_HERO[other.size]
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_ENGAGEMENT, one.actor, target=other.actor, base_limit=base
    )
    adjusted = ctx.bus(one.ref).apply_numeric(Hook.MODIFY_ENGAGEMENT, hook_ctx, base)
    return max(1, adjusted.value)


def may_engage(
    state: CombatState, engager: CombatantId, target: CombatantId, *, ctx: RulesContext
) -> bool:
    """Whether ``engager`` may close with ``target`` (``08.2.4``).

    Only close-combat participants engage, and **heroes in Rearward cannot be engaged** —
    which is the protection Rearward buys, and the thing one Fell Ability spends a drive
    point to ignore.
    """
    one = state.combatant(engager)
    other = state.combatant(target)
    if not (one.active and other.active):
        return False
    if one.is_hero == other.is_hero:
        return False
    if target in one.engaged_with:
        return True
    for side in (one, other):
        if side.is_hero and side.stance not in CLOSE_STANCES:
            return False
    if len(other.engaged_with) >= engagement_limit(other, one, ctx=ctx):
        return False
    return len(one.engaged_with) < engagement_limit(one, other, ctx=ctx)


def may_take_rearward(state: CombatState, hero: CombatantId, *, override: bool = False) -> bool:
    """Whether a hero may assume Rearward this round (``08.2.3``).

    **Both** conditions must hold: the enemy may not outnumber the Company more than two
    to one, and for each hero in Rearward — this one included — two others must be in a
    close-combat stance.

    ``override`` is the Loremaster's waiver, which ``08.2.3`` grants for terrain that makes
    ranged attacks easier or for a Company that heavily outnumbers the foe. The engine does
    not judge the terrain; it takes the ruling.
    """
    if override:
        return True
    enemies = len(state.active_adversaries())
    company = len(state.active_heroes())
    if enemies > REARWARD_ENEMY_RATIO * company:
        return False
    rearward_after = (
        sum(1 for c in state.active_heroes() if c.stance is Stance.REARWARD and c.ref != hero) + 1
    )
    close_combat = sum(
        1 for c in state.active_heroes() if c.ref != hero and c.stance in CLOSE_STANCES
    )
    return close_combat >= REARWARD_COMPANIONS * rearward_after


@dataclass(frozen=True, slots=True)
class EngagementPlan:
    """Who closes with whom, and who stands back (``08.2.4``).

    Whether the Loremaster assigns the pairings or the players choose them is a branch of
    the *fiction*, not of the engine: either way what arrives is a set of pairs to validate
    and apply. ``stood_back`` names the foes that took a ranged weapon instead, and those
    may target **any** hero in the fight, Rearward included.
    """

    pairs: tuple[tuple[CombatantId, CombatantId], ...] = ()
    stood_back: tuple[CombatantId, ...] = ()
    #: Foes standing back name their mark, since a hero in Rearward is otherwise unreachable.
    ranged_targets: Mapping[CombatantId, CombatantId] = field(default_factory=dict)


def resolve_engagement(state: CombatState, plan: EngagementPlan, *, ctx: RulesContext) -> None:
    """Apply one round's engagement (``08.2.4``).

    Existing pairings are left alone — engagement persists until all opposition is defeated
    or a combatant leaves — so this only ever adds. Every pair is validated against the
    limits before **any** is applied, so a plan that breaches them halfway through does not
    leave the fight in a half-engaged state.
    """
    trial = {ref: set(c.engaged_with) for ref, c in state.combatants.items()}
    for engager, target in plan.pairs:
        one, other = state.combatant(engager), state.combatant(target)
        if target in trial[engager]:
            continue
        if not _pair_is_legal(state, one, other, trial, ctx=ctx):
            raise RuleViolation(
                f"{engager} cannot engage {target}",
                rule_reference="engagement_limits",
                suggestion=(
                    f"a {other.size.value}-sized combatant may be engaged by "
                    f"{engagement_limit(other, one, ctx=ctx)}, and {engager} may be "
                    f"engaged by {engagement_limit(one, other, ctx=ctx)}"
                ),
            )
        trial[engager].add(target)
        trial[target].add(engager)

    for ref, engaged in trial.items():
        state.combatant(ref).engaged_with = engaged
    for ref in plan.stood_back:
        foe = state.combatant(ref)
        if foe.is_hero:
            raise RuleViolation(
                f"{ref} is a hero; standing back with a ranged weapon is Rearward stance",
                rule_reference="stand_back_is_for_adversaries",
            )
        foe.stood_back = True
        foe.attacking = plan.ranged_targets.get(ref)
    for ref, mark in plan.ranged_targets.items():
        state.combatant(mark)  # a target that is not in the fight is a caller bug
        state.combatant(ref).attacking = mark


def _pair_is_legal(
    state: CombatState,
    one: Combatant,
    other: Combatant,
    trial: Mapping[CombatantId, set[CombatantId]],
    *,
    ctx: RulesContext,
) -> bool:
    if not (one.active and other.active) or one.is_hero == other.is_hero:
        return False
    for side in (one, other):
        if side.is_hero and side.stance not in CLOSE_STANCES:
            return False
    return len(trial[one.ref]) < engagement_limit(one, other, ctx=ctx) and len(
        trial[other.ref]
    ) < engagement_limit(other, one, ctx=ctx)


def unengage(state: CombatState, ref: CombatantId) -> None:
    """Take a combatant out of every pairing it is in.

    Called when one is slain, flees, or leaves the battlefield. The foes it was holding
    become unengaged, which is what lets ``08.2.4``'s "unengaged mid-round" rule apply:
    a hero whose opponent fell may pick another to attack when their turn comes.
    """
    combatant = state.combatant(ref)
    for other in tuple(combatant.engaged_with):
        state.combatant(other).engaged_with.discard(ref)
    combatant.engaged_with.clear()


def unengaged_heroes(state: CombatState) -> Sequence[Combatant]:
    """Heroes in a close stance with nobody on them — the ones ``08.2.4`` assigns first."""
    return [c for c in state.active_heroes() if c.in_close_combat and not c.engaged_with]
