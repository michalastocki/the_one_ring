"""Effects and the bus that dispatches them (``04.1``, ``04.2``).

Seam B. Cultural Blessings, Virtues, Cultural Virtues, Rewards, Enchanted Rewards, item
Blessings, Curses, Fell Abilities, Flaws, Distinctive Features and Conditions are **all the
same thing**: named packages of listeners on named hooks.

Get this right and the rest of the engine stays small. Get it wrong and you will write
``if virtue == "..."`` in forty places.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tor.effects.hooks import (
    Contribution,
    Hook,
    HookContext,
    NumericContribution,
    ReplacementContribution,
)
from tor.errors import RuleViolation
from tor.model.derive import DerivedStat, Modifier
from tor.model.ids import EffectId

__all__ = [
    "Effect",
    "EffectBus",
    "EffectKind",
    "EffectSource",
    "Listener",
    "Predicate",
    "UsagePolicy",
    "UsageScope",
]


class EffectKind(StrEnum):
    CULTURAL_BLESSING = "cultural_blessing"
    VIRTUE = "virtue"
    CULTURAL_VIRTUE = "cultural_virtue"
    REWARD = "reward"
    ENCHANTED_REWARD = "enchanted_reward"
    ITEM_BLESSING = "item_blessing"
    CURSE = "curse"
    FELL_ABILITY = "fell_ability"
    FLAW = "flaw"
    DISTINCTIVE_FEATURE = "distinctive_feature"
    CONDITION = "condition"
    PATRON_BENEFIT = "patron_benefit"
    UNDERTAKING_BENEFIT = "undertaking_benefit"


#: A fixed ordering, so ``collect()`` is deterministic regardless of registration order.
#: Two runs with the same seed and the same state must produce byte-identical results —
#: the replay guarantee of ``17.4`` rests on it.
_KIND_RANK: Mapping[EffectKind, int] = {kind: index for index, kind in enumerate(EffectKind)}


class UsageScope(StrEnum):
    """How long a usage budget lasts (pattern P16)."""

    ROLL = "roll"
    ROUND = "round"
    COMBAT = "combat"
    SCENE = "scene"
    JOURNEY = "journey"
    COUNCIL = "council"
    ADVENTURING_PHASE = "adventuring_phase"
    FELLOWSHIP_PHASE = "fellowship_phase"
    #: Once ever. At least one Cultural Virtue saves a hero from a deadly Wound exactly
    #: once, then is spent forever.
    CAREER = "career"


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    scope: UsageScope
    #: A callable for the budgets phrased as "a number of times equal to your WISDOM".
    limit: int | Callable[[HookContext], int]

    def limit_for(self, ctx: HookContext) -> int:
        return self.limit(ctx) if callable(self.limit) else self.limit


Predicate = Callable[[HookContext], bool]
Listener = Callable[[HookContext], Contribution | Sequence[Contribution] | None]


@dataclass(frozen=True, slots=True)
class Effect:
    """A named package of listeners on named hooks.

    ``params`` carries the player's choices at acquisition — which two Skills *Mastery*
    favours, which Attribute *Prowess* lowers, which creature type a Hatred names. That is
    what lets an effect be a shared, stateless definition and still be personal.

    ``predicate`` gates it on live state: "if you are not Miserable", "while in darkness",
    "when the target has Might 2 or more". Predicates are pure functions of a
    :class:`~tor.effects.hooks.HookContext` and never mutate.
    """

    id: EffectId
    kind: EffectKind
    listeners: Mapping[Hook, Listener]
    params: Mapping[str, Any] = field(default_factory=dict)
    usage: UsagePolicy | None = None
    predicate: Predicate | None = None
    #: Lower runs first within a kind. Ordinary effects leave it at 0.
    priority: int = 0

    def applies(self, ctx: HookContext) -> bool:
        return self.predicate is None or self.predicate(ctx)


@dataclass(frozen=True, slots=True)
class EffectSource:
    """Where an effect came from, so it can be unregistered wholesale.

    This is how a smashed shield drops its Parry bonus and a dropped helm drops its
    Protection: unregister by source, and every derived stat that read it recomputes
    without it.
    """

    kind: str
    ref: str = ""

    @classmethod
    def culture(cls, culture_id: str) -> EffectSource:
        return cls("culture", culture_id)

    @classmethod
    def item(cls, instance_ref: str) -> EffectSource:
        return cls("item", instance_ref)

    @classmethod
    def condition(cls, name: str) -> EffectSource:
        return cls("condition", name)

    @classmethod
    def acquired(cls, what: str = "") -> EffectSource:
        """A Virtue, Reward or Distinctive Feature the hero holds permanently."""
        return cls("acquired", what)


@dataclass(slots=True)
class _Registration:
    effect: Effect
    source: EffectSource


class EffectBus:
    """Every character and every adversary instance owns one."""

    __slots__ = ("_registrations", "_usage")

    def __init__(self) -> None:
        self._registrations: list[_Registration] = []
        self._usage: dict[tuple[EffectId, UsageScope], int] = {}

    # -- registration ----------------------------------------------------------------

    def register(self, effect: Effect, source: EffectSource) -> None:
        if any(r.effect.id == effect.id and r.source == source for r in self._registrations):
            raise RuleViolation(
                f"{effect.id} is already registered from {source.kind}:{source.ref}",
                rule_reference="effect_registered_once_per_source",
            )
        self._registrations.append(_Registration(effect, source))

    def unregister(self, effect_id: EffectId, source: EffectSource | None = None) -> None:
        """Remove one effect, or one effect from one source."""
        self._registrations = [
            r
            for r in self._registrations
            if not (r.effect.id == effect_id and (source is None or r.source == source))
        ]

    def unregister_source(self, source: EffectSource) -> None:
        """Drop everything a source contributed — a smashed shield, a dropped helm."""
        self._registrations = [r for r in self._registrations if r.source != source]

    def registered(self, effect_id: EffectId) -> bool:
        return any(r.effect.id == effect_id for r in self._registrations)

    def effects(self) -> tuple[Effect, ...]:
        return tuple(r.effect for r in self._registrations)

    # -- dispatch --------------------------------------------------------------------

    def _eligible(self, hook: Hook, ctx: HookContext) -> list[Effect]:
        """Effects listening on ``hook`` whose predicate passes and whose budget allows.

        Sorted by ``(priority, kind rank, id)``. Never insertion order, never ``set``
        iteration — determinism here is what makes replay possible.
        """
        candidates = [
            r.effect
            for r in self._registrations
            if hook in r.effect.listeners
            and r.effect.applies(ctx)
            and self._has_budget(r.effect, ctx)
        ]
        return sorted(candidates, key=lambda e: (e.priority, _KIND_RANK[e.kind], str(e.id)))

    def collect(self, hook: Hook, ctx: HookContext) -> list[Contribution]:
        """Fire every eligible listener and return their contributions.

        **Pure.** Collection never mutates — not even a usage counter. A caller that
        commits to using a budgeted effect calls :meth:`consume` explicitly afterwards
        (``04.7``).
        """
        gathered: list[Contribution] = []
        for effect in self._eligible(hook, ctx):
            produced = effect.listeners[hook](ctx)
            if produced is None:
                continue
            if isinstance(produced, Sequence):
                gathered.extend(produced)
            else:
                gathered.append(produced)
        return gathered

    def apply_numeric(self, hook: Hook, ctx: HookContext, base: int) -> DerivedStat:
        """Collect a hook's value-shaped contributions into a :class:`DerivedStat`.

        Additive contributions become deltas. An ``int``-valued replacement becomes a
        :class:`Modifier` with ``replacement`` set, which ``DerivedStat.value`` resolves
        ahead of the base — that is how Mithril Make, which ``04.3.2`` names explicitly for
        ``MODIFY_ITEM_LOAD``, replaces an item's Load outright rather than adjusting it.
        A replacement carrying anything but an ``int`` is not a value for this stat and is
        left to whichever caller collects that hook directly.
        """
        modifiers: list[Modifier] = []
        for contribution in self.collect(hook, ctx):
            if isinstance(contribution, NumericContribution):
                modifiers.append(Modifier(source=contribution.source, delta=contribution.delta))
            elif isinstance(contribution, ReplacementContribution) and isinstance(
                contribution.value, int
            ):
                modifiers.append(
                    Modifier(source=contribution.source, replacement=contribution.value)
                )
        return DerivedStat(base=base, modifiers=tuple(modifiers))

    # -- usage budgets ---------------------------------------------------------------

    def _has_budget(self, effect: Effect, ctx: HookContext) -> bool:
        if effect.usage is None:
            return True
        used = self._usage.get((effect.id, effect.usage.scope), 0)
        return used < effect.usage.limit_for(ctx)

    def consume(self, effect_id: EffectId, ctx: HookContext) -> None:
        """Record one use of a budgeted effect, after the caller commits to it."""
        effect = next((e for e in self.effects() if e.id == effect_id), None)
        if effect is None:
            raise RuleViolation(
                f"{effect_id} is not registered on this bus",
                rule_reference="effect_not_registered",
            )
        if effect.usage is None:
            return
        key = (effect_id, effect.usage.scope)
        if self._usage.get(key, 0) >= effect.usage.limit_for(ctx):
            raise RuleViolation(
                f"{effect_id} has no uses left this {effect.usage.scope}",
                rule_reference="effect_usage_budget",
            )
        self._usage[key] = self._usage.get(key, 0) + 1

    def uses(self, effect_id: EffectId, scope: UsageScope) -> int:
        return self._usage.get((effect_id, scope), 0)

    def reset_scope(self, scope: UsageScope) -> None:
        """Clear the budgets for one scope. Called from the session layer's boundaries."""
        self._usage = {key: n for key, n in self._usage.items() if key[1] is not scope}

    def reset_scopes(self, scopes: Iterable[UsageScope]) -> None:
        for scope in scopes:
            self.reset_scope(scope)
