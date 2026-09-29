"""Combat (``08``), and the adversaries that fight in it (``12``).

A package rather than a module, as ``08``'s own header specifies: ``state``, ``sequence``,
``engagement``, ``attack``, ``tasks``. It is the largest subsystem, and every roll in it
goes through :func:`tor.rolls.resolve` (Seam A).

A **subsystem**, not a shared leaf: it may use ``resources``, ``injury``, ``shadow`` and
``contest``, and no other subsystem may use it (``01.1``).

Import the submodule you need. This package re-exports the public surface for convenience,
because a caller resolving one attack should not have to know which of five modules each
name lives in.
"""

from __future__ import annotations

from tor.rules.combat.attack import (
    AttackDeclaration,
    AttackOutcome,
    AttackRoll,
    ProtectionOutcome,
    SpecialDamage,
    SpendPlan,
    WoundOutcome,
    apply_attack,
    attack_target_number,
    offered_special_damage,
    resolve_attack,
    roll_attack,
    shield_parry,
)
from tor.rules.combat.engagement import (
    EngagementPlan,
    engagement_limit,
    may_engage,
    may_take_rearward,
    resolve_engagement,
    unengage,
)
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
from tor.rules.combat.state import (
    CLOSE_STANCES,
    STANCE_ORDER,
    Advantage,
    Combatant,
    CombatPhase,
    CombatState,
    Complication,
    Duration,
    Interference,
    RoundFlags,
    Stance,
)
from tor.rules.combat.tasks import (
    CombatTask,
    TaskDeclaration,
    TaskOutcome,
    apply_task,
    battle_for_interference,
    resolve_task,
)

__all__ = [
    "CLOSE_STANCES",
    "STANCE_ORDER",
    "Advantage",
    "AttackDeclaration",
    "AttackOutcome",
    "AttackRoll",
    "CombatPhase",
    "CombatState",
    "CombatTask",
    "Combatant",
    "Complication",
    "Duration",
    "EngagementPlan",
    "Interference",
    "ProtectionOutcome",
    "RoundFlags",
    "SpecialDamage",
    "SpendPlan",
    "Stance",
    "SurpriseInput",
    "TaskDeclaration",
    "TaskOutcome",
    "WoundOutcome",
    "action_order",
    "apply_attack",
    "apply_task",
    "attack_target_number",
    "battle_for_interference",
    "begin_combat",
    "begin_round",
    "end_combat",
    "end_round",
    "engagement_limit",
    "may_engage",
    "may_take_rearward",
    "offered_special_damage",
    "opening_volley_count",
    "resolve_attack",
    "resolve_engagement",
    "resolve_surprise",
    "resolve_task",
    "roll_attack",
    "set_stance",
    "shield_parry",
    "unengage",
]
