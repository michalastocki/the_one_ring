"""Effects and hooks (``04-effects-and-hooks.md``).

Seam B of the three cross-cutting seams. Cultural Blessings, Virtues, Cultural Virtues,
Rewards, Enchanted Rewards, item Blessings, Curses, Fell Abilities, Flaws, Distinctive
Features and Conditions are all the same thing: named packages of listeners on named
hooks. There is no ``if culture == ...`` anywhere in the engine.
"""

from __future__ import annotations

from tor.effects.bus import (
    Effect,
    EffectBus,
    EffectKind,
    EffectSource,
    Listener,
    Predicate,
    UsagePolicy,
    UsageScope,
)
from tor.effects.hooks import (
    DEFAULT_ENVIRONMENT,
    ActionContribution,
    Contribution,
    Environment,
    FlagContribution,
    Hook,
    HookContext,
    NumericContribution,
    ReplacementContribution,
    SceneKind,
    Terrain,
    Weather,
)
from tor.effects.library import (
    EFFECT_FACTORIES,
    build_effect,
    build_predicate,
    register_factory,
)

__all__ = [
    "DEFAULT_ENVIRONMENT",
    "EFFECT_FACTORIES",
    "ActionContribution",
    "Contribution",
    "Effect",
    "EffectBus",
    "EffectKind",
    "EffectSource",
    "Environment",
    "FlagContribution",
    "Hook",
    "HookContext",
    "Listener",
    "NumericContribution",
    "Predicate",
    "ReplacementContribution",
    "SceneKind",
    "Terrain",
    "UsagePolicy",
    "UsageScope",
    "Weather",
    "build_effect",
    "build_predicate",
    "register_factory",
]
