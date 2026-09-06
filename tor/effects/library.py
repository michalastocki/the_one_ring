"""Declarative effect factories (``04.5``).

Content packs reference effects by id and describe them declaratively; this module turns
those declarations into :class:`~tor.effects.bus.Effect` objects. **Most effects should be
declarative, not code** — the majority of the book's ~80 special abilities need no Python
at all.

The spec's own review rule, worth keeping in mind as this file grows: *if your effect
library exceeds ~15 hand-written effects, a factory is missing. Add the factory rather
than the fifteenth bespoke class.*
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tor.effects.bus import (
    Effect,
    EffectKind,
    Listener,
    Predicate,
    UsagePolicy,
    UsageScope,
)
from tor.effects.hooks import (
    ActionContribution,
    Contribution,
    FlagContribution,
    Hook,
    HookContext,
    NumericContribution,
    ReplacementContribution,
)
from tor.errors import ContentError
from tor.model.ids import EffectId

__all__ = [
    "EFFECT_FACTORIES",
    "REPLACEMENT_FACTORIES",
    "build_effect",
    "build_predicate",
    "register_factory",
]

Factory = Callable[[EffectId, EffectKind, Mapping[str, Any]], Effect]

_FACTORIES: dict[str, Factory] = {}
_REPLACING: set[str] = set()


def register_factory(name: str, *, replaces: bool = False) -> Callable[[Factory], Factory]:
    """Register a factory under the name a pack writes in its ``factory`` field.

    ``replaces=True`` marks a factory whose listener returns a
    :class:`~tor.effects.hooks.ReplacementContribution` — it overwrites a value rather than
    adding to one. Two such effects reaching the same hook for the same item type is a
    content bug, which is what ``05.1.1`` check 5 exists to catch; the flag lives here, next
    to the factory that does the replacing, so there is no parallel list to drift.
    """

    def decorate(factory: Factory) -> Factory:
        _FACTORIES[name] = factory
        if replaces:
            _REPLACING.add(name)
        return factory

    return decorate


# ----------------------------------------------------------------------------------
# Predicates
# ----------------------------------------------------------------------------------


def build_predicate(spec: Mapping[str, Any] | None) -> Predicate | None:
    """Compile a declarative predicate.

    Supported forms::

        {"in_darkness": true}                 an Environment flag
        {"environment": "underground"}        the same, named
        {"not_miserable": true}               an actor condition
        {"scene": "combat"}                   the current scene kind
        {"flag": "flaw_applies"}              a Loremaster ruling in ctx.extra
        {"any_of": [ ... ]} / {"all_of": [...]}
    """
    if not spec:
        return None

    if "any_of" in spec:
        parts = [build_predicate(s) for s in spec["any_of"]]
        return lambda ctx: any(p(ctx) for p in parts if p is not None)
    if "all_of" in spec:
        parts = [build_predicate(s) for s in spec["all_of"]]
        return lambda ctx: all(p(ctx) for p in parts if p is not None)

    checks: list[Predicate] = []
    for key, value in spec.items():
        checks.append(_single_predicate(key, value))
    return lambda ctx: all(check(ctx) for check in checks)


_ENVIRONMENT_FLAGS = frozenset(
    {"in_darkness", "underground", "cramped", "full_sunlight", "outdoors"}
)


def _single_predicate(key: str, value: Any) -> Predicate:
    if key in _ENVIRONMENT_FLAGS:
        return lambda ctx: getattr(ctx.environment, key) is bool(value)
    if key == "environment":
        return lambda ctx: getattr(ctx.environment, str(value), False) is True
    if key == "scene":
        return lambda ctx: ctx.scene is not None and str(ctx.scene) == str(value)
    if key == "purpose":
        return lambda ctx: ctx.purpose == value
    if key == "ability":
        return lambda ctx: ctx.ability == value
    if key == "stance":
        return lambda ctx: ctx.stance == value
    if key == "flag":
        return lambda ctx: bool(ctx.extra.get(str(value)))
    if key == "not_miserable":
        return lambda ctx: _condition(ctx, "miserable") is not bool(value)
    if key in {"miserable", "weary", "wounded"}:
        return lambda ctx: _condition(ctx, key) is bool(value)
    raise ContentError(f"unknown predicate key {key!r}")


def _condition(ctx: HookContext, name: str) -> bool:
    conditions = getattr(ctx.actor, "conditions", None)
    return bool(getattr(conditions, name, False))


# ----------------------------------------------------------------------------------
# Factories
# ----------------------------------------------------------------------------------


def _usage_from(spec: Mapping[str, Any] | None) -> UsagePolicy | None:
    if not spec:
        return None
    try:
        scope = UsageScope(spec["scope"])
    except (KeyError, ValueError) as exc:
        raise ContentError(f"invalid usage scope in {dict(spec)!r}") from exc
    limit = spec.get("limit", 1)
    if isinstance(limit, str):
        # "equal to your WISDOM" and similar, resolved against the actor at fire time.
        stat = limit
        return UsagePolicy(scope=scope, limit=lambda ctx: int(getattr(ctx.actor, stat, 0)))
    return UsagePolicy(scope=scope, limit=int(limit))


def _effect(
    effect_id: EffectId,
    kind: EffectKind,
    listeners: Mapping[Hook, Listener],
    params: Mapping[str, Any],
) -> Effect:
    return Effect(
        id=effect_id,
        kind=kind,
        listeners=listeners,
        params=params,
        usage=_usage_from(params.get("usage")),
        predicate=build_predicate(params.get("predicate")),
    )


@register_factory("numeric_modifier")
def numeric_modifier(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """``{"hook": "MODIFY_MAX_HOPE", "delta": 2}`` — the workhorse.

    Hardiness, Confidence, Nimbleness, Prowess, Cunning Make, Reinforced, Grievous, Fell,
    the Weakening curse, and every conditional stat bonus.
    """
    hook = _hook(params, effect_id)
    delta = int(params.get("delta", 0))

    def listen(ctx: HookContext) -> Contribution:
        return NumericContribution(source=effect_id, delta=delta)

    return _effect(effect_id, kind, {hook: listen}, params)


@register_factory("greater_of")
def greater_of(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """``{"hook": ..., "flat": 3, "stat": "valour"}`` — "+3, or your VALOUR, whichever is higher".

    A recurring shape among the Enchanted Rewards (``13.7.5``), so it gets a factory rather
    than a bespoke class each time.
    """
    hook = _hook(params, effect_id)
    flat = int(params.get("flat", 0))
    stat = str(params.get("stat", ""))

    def listen(ctx: HookContext) -> Contribution:
        from_stat = int(getattr(ctx.actor, stat, 0))
        return NumericContribution(source=effect_id, delta=max(flat, from_stat))

    return _effect(effect_id, kind, {hook: listen}, params)


@register_factory("favour_rolls")
def favour_rolls(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """``{"purpose": "valour_test"}`` — contribute a Favoured source to matching rolls.

    The engine never learns which culture a blessing belongs to: the predicate matches the
    roll's purpose and a FlagContribution names the source.
    """
    purpose = params.get("purpose")
    abilities = frozenset(params.get("abilities", ()))

    def listen(ctx: HookContext) -> Contribution | None:
        if purpose is not None and ctx.purpose != purpose:
            return None
        if abilities and ctx.ability not in abilities:
            return None
        return FlagContribution(source=effect_id, flag="favoured", value=str(effect_id))

    return _effect(effect_id, kind, {Hook.MODIFY_ROLL_REQUEST: listen}, params)


@register_factory("ill_favour_when_applicable")
def ill_favour_when_applicable(
    effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]
) -> Effect:
    """A Flaw (``11.7``).

    Makes a roll Ill-favoured when the Loremaster rules the Flaw plausibly applies. It is
    **not** automatic, because plausibility cannot be computed — the ruling arrives as
    ``ctx.extra["flaw_applies"]``.
    """
    trigger = str(params.get("flag", "flaw_applies"))

    def listen(ctx: HookContext) -> Contribution | None:
        if not ctx.extra.get(trigger):
            return None
        return FlagContribution(source=effect_id, flag="ill_favoured", value=str(effect_id))

    return _effect(effect_id, kind, {Hook.MODIFY_ROLL_REQUEST: listen}, params)


@register_factory("bonus_dice")
def bonus_dice(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """``{"purpose": "shadow_test", "source": "sorcery", "dice": 1}`` — +Nd on matching rolls."""
    purpose = params.get("purpose")
    against = params.get("source")
    dice = int(params.get("dice", 1))

    def listen(ctx: HookContext) -> Contribution | None:
        if purpose is not None and ctx.purpose != purpose:
            return None
        if against is not None and ctx.extra.get("source") != against:
            return None
        return NumericContribution(source=effect_id, delta=dice)

    return _effect(effect_id, kind, {Hook.MODIFY_ROLL_REQUEST: listen}, params)


@register_factory("grant_inspiration")
def grant_inspiration(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """Inspired: Hope spent for bonus dice yields +2d instead of +1d (``06.5``)."""

    def listen(ctx: HookContext) -> Contribution:
        return FlagContribution(source=effect_id, flag="inspired")

    return _effect(effect_id, kind, {Hook.MODIFY_INSPIRATION: listen}, params)


@register_factory("enable_magical_success")
def enable_magical_success(
    effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]
) -> Effect:
    """Declares which abilities may be pushed to a magical success (``02.3.7``).

    ``magical_success`` is only legal when this hook reports an effect granting it for that
    specific ability; otherwise the roll raises.
    """
    abilities = frozenset(params.get("abilities", ()))

    def listen(ctx: HookContext) -> Contribution | None:
        if abilities and ctx.ability not in abilities:
            return None
        return FlagContribution(source=effect_id, flag="magical_success_eligible")

    return _effect(effect_id, kind, {Hook.MAGICAL_SUCCESS_ELIGIBLE: listen}, params)


@register_factory("modify_piercing_threshold", replaces=True)
def modify_piercing_threshold(
    effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]
) -> Effect:
    """Keen and its Enchanted variants: a Piercing Blow on 9, or 8, or 10 minus VALOUR.

    A *computed* threshold, not a constant — hence ``stat`` alongside ``threshold``.
    """
    threshold = params.get("threshold")
    minus_stat = params.get("minus_stat")

    def listen(ctx: HookContext) -> Contribution:
        if minus_stat is not None:
            value = 10 - int(getattr(ctx.actor, str(minus_stat), 0))
        else:
            value = int(threshold if threshold is not None else 10)
        return ReplacementContribution(source=effect_id, value=value)

    return _effect(effect_id, kind, {Hook.PIERCING_THRESHOLD: listen}, params)


@register_factory("secondary_action_task")
def secondary_action_task(
    effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]
) -> Effect:
    """Converts a specific combat task into a secondary action in a specific stance."""
    task = str(params.get("task", ""))
    stance = params.get("stance")

    def listen(ctx: HookContext) -> Contribution | None:
        if stance is not None and ctx.stance != stance:
            return None
        return ActionContribution(
            source=effect_id, action="secondary_action", payload={"task": task}
        )

    return _effect(effect_id, kind, {Hook.SECONDARY_ACTION_OPTIONS: listen}, params)


@register_factory("spend_drive_for")
def spend_drive_for(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """A Fell Ability with a cost (``12.6``).

    Offered as an action rather than applied: the Loremaster decides whether to pay. Note
    that the trigger may fire on the *attacker's* roll while the cost is paid by the
    target — ``HookContext.target`` is what makes that expressible.
    """
    cost = int(params.get("cost", 1))
    trigger = str(params.get("trigger", "on_turn"))
    grant = params.get("grant", {})

    def listen(ctx: HookContext) -> Contribution:
        return ActionContribution(
            source=effect_id,
            action="spend_drive",
            payload={"cost": cost, "trigger": trigger, "grant": dict(grant)},
        )

    hook = _hook(params, effect_id, default=Hook.MODIFY_ROLL_REQUEST)
    return _effect(effect_id, kind, {hook: listen}, params)


@register_factory("shadow_on_hit")
def shadow_on_hit(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """Strike Fear and kin: Shadow inflicted on a hit, with an optional rider on failure."""
    points = int(params.get("points", 1))
    source = str(params.get("source", "dread"))
    on_fail = params.get("on_fail")

    def listen(ctx: HookContext) -> Contribution:
        return ActionContribution(
            source=effect_id,
            action="inflict_shadow",
            payload={"points": points, "source": source, "on_fail": on_fail},
        )

    return _effect(effect_id, kind, {Hook.ON_ATTACK_HIT: listen}, params)


@register_factory("drain_drive_on_hit")
def drain_drive_on_hit(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """Gleam of Wrath and kin: drain N drive, plus one per Success icon (pattern P3)."""
    amount = int(params.get("amount", 1))
    per_icon = int(params.get("per_icon", 0))

    def listen(ctx: HookContext) -> Contribution:
        icons = int(ctx.extra.get("icons", 0))
        return ActionContribution(
            source=effect_id,
            action="drain_drive",
            payload={"amount": amount + per_icon * icons},
        )

    return _effect(effect_id, kind, {Hook.ON_ATTACK_HIT: listen}, params)


@register_factory("make_favoured")
def make_favoured(effect_id: EffectId, kind: EffectKind, params: Mapping[str, Any]) -> Effect:
    """*Mastery*: choose Skills and mark them Favoured.

    The choice is captured in ``params`` at acquisition, which is what lets one shared
    definition serve every hero who takes it.
    """
    chosen = tuple(params.get("skills", ()))

    def listen(ctx: HookContext) -> Contribution | None:
        if ctx.ability not in chosen:
            return None
        return FlagContribution(source=effect_id, flag="favoured", value=str(effect_id))

    return _effect(effect_id, kind, {Hook.MODIFY_ROLL_REQUEST: listen}, params)


def _hook(params: Mapping[str, Any], effect_id: EffectId, default: Hook | None = None) -> Hook:
    raw = params.get("hook")
    if raw is None:
        if default is not None:
            return default
        raise ContentError(f"{effect_id} needs a 'hook' parameter", entity_id=str(effect_id))
    try:
        return Hook(raw)
    except ValueError as exc:
        raise ContentError(f"unknown hook {raw!r}", entity_id=str(effect_id)) from exc


# ----------------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------------

EFFECT_FACTORIES: Mapping[str, Factory] = _FACTORIES

#: The factories that replace a hook's value rather than adding to it (``05.1.1`` check 5).
REPLACEMENT_FACTORIES: frozenset[str] = frozenset(_REPLACING)


def build_effect(
    effect_id: EffectId,
    kind: EffectKind | str,
    factory: str,
    params: Mapping[str, Any] | None = None,
) -> Effect:
    """Instantiate an effect from a content declaration."""
    if factory not in _FACTORIES:
        raise ContentError(
            f"unknown effect factory {factory!r}; known: {sorted(_FACTORIES)}",
            entity_id=str(effect_id),
        )
    try:
        resolved_kind = EffectKind(kind)
    except ValueError as exc:
        raise ContentError(f"unknown effect kind {kind!r}", entity_id=str(effect_id)) from exc
    return _FACTORIES[factory](effect_id, resolved_kind, params or {})
