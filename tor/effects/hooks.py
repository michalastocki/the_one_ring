"""The hook registry, contributions, and hook context (``04-effects-and-hooks.md`` §4.3-4.4).

Adding a hook is a spec change; implementing an effect is not. The complete set from
``04.3`` and ``20.10`` is enumerated here so that a content pack can never name a hook the
engine does not know, and so the ``19.7`` test asserting every hook is exercised has
something to enumerate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tor.model.gear import WeaponInstance
from tor.model.ids import AbilityId, EffectId

__all__ = [
    "DEFAULT_ENVIRONMENT",
    "ActionContribution",
    "Contribution",
    "Environment",
    "FlagContribution",
    "Hook",
    "HookContext",
    "NumericContribution",
    "ReplacementContribution",
    "SceneKind",
    "Terrain",
    "Weather",
]


class Hook(StrEnum):
    """Every point at which an effect may intervene."""

    # -- roll pipeline (04.3.1) ----------------------------------------------------
    MODIFY_ROLL_REQUEST = "MODIFY_ROLL_REQUEST"
    #: The only hook that runs *after* the dice are known. Player-triggered, offered as an
    #: ActionContribution and applied on confirmation — never auto-fired.
    MODIFY_ROLL_RESULT = "MODIFY_ROLL_RESULT"
    MAGICAL_SUCCESS_ELIGIBLE = "MAGICAL_SUCCESS_ELIGIBLE"
    ON_ROLL_RESOLVED = "ON_ROLL_RESOLVED"

    # -- derived stats (04.3.2) ----------------------------------------------------
    MODIFY_MAX_ENDURANCE = "MODIFY_MAX_ENDURANCE"
    MODIFY_MAX_HOPE = "MODIFY_MAX_HOPE"
    MODIFY_PARRY = "MODIFY_PARRY"
    MODIFY_ATTRIBUTE_TN = "MODIFY_ATTRIBUTE_TN"
    MODIFY_ITEM_LOAD = "MODIFY_ITEM_LOAD"
    MODIFY_SHIELD_PARRY = "MODIFY_SHIELD_PARRY"
    MODIFY_FELLOWSHIP_RATING = "MODIFY_FELLOWSHIP_RATING"
    MODIFY_MOUNT_VIGOUR = "MODIFY_MOUNT_VIGOUR"
    MODIFY_USEFUL_ITEM_LIMIT = "MODIFY_USEFUL_ITEM_LIMIT"

    # -- combat (04.3.3) -----------------------------------------------------------
    MODIFY_ATTACK_TN = "MODIFY_ATTACK_TN"
    MODIFY_DAMAGE_RATING = "MODIFY_DAMAGE_RATING"
    MODIFY_INJURY_RATING = "MODIFY_INJURY_RATING"
    PIERCING_THRESHOLD = "PIERCING_THRESHOLD"
    MODIFY_PROTECTION_ROLL = "MODIFY_PROTECTION_ROLL"
    MODIFY_TARGET_PROTECTION_ROLL = "MODIFY_TARGET_PROTECTION_ROLL"
    SPECIAL_DAMAGE_OPTIONS = "SPECIAL_DAMAGE_OPTIONS"
    MODIFY_SPECIAL_DAMAGE = "MODIFY_SPECIAL_DAMAGE"
    ON_ATTACK_HIT = "ON_ATTACK_HIT"
    ON_PIERCING_BLOW = "ON_PIERCING_BLOW"
    ON_KILL = "ON_KILL"
    MODIFY_WOUND_SEVERITY_ROLL = "MODIFY_WOUND_SEVERITY_ROLL"
    ON_WOUND_RECEIVED = "ON_WOUND_RECEIVED"
    ON_ROUND_START = "ON_ROUND_START"
    ON_ROUND_END = "ON_ROUND_END"
    ON_COMBAT_END = "ON_COMBAT_END"
    STANCE_OPTIONS = "STANCE_OPTIONS"
    SECONDARY_ACTION_OPTIONS = "SECONDARY_ACTION_OPTIONS"
    OPENING_VOLLEY_COUNT = "OPENING_VOLLEY_COUNT"
    MODIFY_ENGAGEMENT = "MODIFY_ENGAGEMENT"
    #: The only two hooks that may *veto* a state change rather than modify a value
    #: (``12.6``). Keep them narrow: an interceptor returns PROCEED or a replacement
    #: outcome, never arbitrary mutation.
    DAMAGE_INTERCEPT = "DAMAGE_INTERCEPT"
    WOUND_INTERCEPT = "WOUND_INTERCEPT"

    # -- journey (04.3.4) ----------------------------------------------------------
    MODIFY_FATIGUE_GAIN = "MODIFY_FATIGUE_GAIN"
    MODIFY_JOURNEY_EVENT_ROLL = "MODIFY_JOURNEY_EVENT_ROLL"
    JOURNEY_ROLE_LIMITS = "JOURNEY_ROLE_LIMITS"
    ON_JOURNEY_END = "ON_JOURNEY_END"

    # -- shadow and recovery (04.3.5) ----------------------------------------------
    MODIFY_SHADOW_GAIN = "MODIFY_SHADOW_GAIN"
    MODIFY_SHADOW_TEST = "MODIFY_SHADOW_TEST"
    MODIFY_SHADOW_REMOVAL_CAP = "MODIFY_SHADOW_REMOVAL_CAP"
    #: A floor on the Shadow score, not a one-off gain — the Shadow Taint curse (``13.8``).
    MODIFY_SHADOW_FLOOR = "MODIFY_SHADOW_FLOOR"
    MODIFY_SHORT_REST_RECOVERY = "MODIFY_SHORT_REST_RECOVERY"
    MODIFY_PROLONGED_REST_RECOVERY = "MODIFY_PROLONGED_REST_RECOVERY"
    SHORT_REST_COUNTS_AS_PROLONGED = "SHORT_REST_COUNTS_AS_PROLONGED"
    MODIFY_HOPE_RECOVERY = "MODIFY_HOPE_RECOVERY"
    MODIFY_INSPIRATION = "MODIFY_INSPIRATION"

    # -- council and social (04.3.6) -----------------------------------------------
    MODIFY_COUNCIL_ATTEMPTS = "MODIFY_COUNCIL_ATTEMPTS"
    MODIFY_AUDIENCE_ATTITUDE = "MODIFY_AUDIENCE_ATTITUDE"
    MODIFY_COUNCIL_ROLL = "MODIFY_COUNCIL_ROLL"

    # -- progression and phase (04.3.7) --------------------------------------------
    FREE_UNDERTAKINGS = "FREE_UNDERTAKINGS"
    ON_VALOUR_GAIN = "ON_VALOUR_GAIN"
    ON_WISDOM_GAIN = "ON_WISDOM_GAIN"
    ON_PHASE_START = "ON_PHASE_START"
    ON_PHASE_END = "ON_PHASE_END"


class SceneKind(StrEnum):
    COMBAT = "combat"
    COUNCIL = "council"
    JOURNEY = "journey"
    ENDEAVOUR = "endeavour"
    FREE = "free"


class Terrain(StrEnum):
    EASY = "easy"
    HARD = "hard"
    ROAD = "road"


class Weather(StrEnum):
    FAIR = "fair"
    POOR = "poor"
    SEVERE = "severe"


@dataclass(frozen=True, slots=True)
class Environment:
    """The scene's physical circumstances, set by the Loremaster (``04.4``).

    A first-class object rather than loose flags, which is what stops "fighting underground
    grants +2 Parry" from becoming a special case inside the combat module.
    """

    in_darkness: bool = False
    underground: bool = False
    cramped: bool = False
    full_sunlight: bool = False
    outdoors: bool = True
    terrain: Terrain | None = None
    weather: Weather = Weather.FAIR


#: The neutral scene: nothing dark, cramped, sunlit or underground. A module-level
#: singleton because Environment is frozen, so every caller can share one.
DEFAULT_ENVIRONMENT = Environment()


@dataclass(frozen=True, slots=True)
class HookContext:
    """Everything a predicate or listener may consult. Never mutated."""

    hook: Hook
    #: The acting character. Typed loosely because both Hero and AdversaryInstance
    #: qualify — the read-only view of ``01.5`` rather than a concrete class.
    actor: Any = None
    target: Any = None
    scene: SceneKind | None = None
    environment: Environment = DEFAULT_ENVIRONMENT
    ability: AbilityId | None = None
    purpose: str | None = None
    stance: str | None = None
    weapon: WeaponInstance | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NumericContribution:
    """An additive delta: +2 max Endurance, +1 Parry, -1 Attribute TN, +1d, +2 Injury."""

    source: EffectId
    delta: int


@dataclass(frozen=True, slots=True)
class FlagContribution:
    """A boolean or a set member: a Favoured source, "you are Inspired", "never gain Fatigue"."""

    source: EffectId
    flag: str
    value: Any = True


@dataclass(frozen=True, slots=True)
class ReplacementContribution:
    """Overrides a value outright — Mithril replacing an item's Load.

    Used sparingly. Two replacements colliding on the same hook for the same item type is
    a ``ContentError`` detected when the pack is validated (``05.1.1`` check 5).
    """

    source: EffectId
    value: Any


@dataclass(frozen=True, slots=True)
class ActionContribution:
    """Offers a new legal action or option.

    "You may attempt Protect Companion as a *secondary* action", "a Company including this
    Calling may choose this undertaking for free", and every player-triggered reroll.
    """

    source: EffectId
    action: str
    payload: Mapping[str, Any] = field(default_factory=dict)


Contribution = NumericContribution | FlagContribution | ReplacementContribution | ActionContribution
