"""What a rule needs that does not live on the aggregate it is handed.

``00-README.md`` §4.2 forbids global mutable state: no module-level RNG, no singletons,
every entry point taking its dependencies explicitly. A rule therefore cannot reach for an
``EffectBus`` or a gear table — both must arrive as parameters, and bundling them into one
context keeps the signatures from growing a tail of keyword arguments.

Two of those dependencies could not live anywhere else:

* **The bus cannot live on the aggregate.** An ``EffectBus`` field on ``Hero`` would make
  ``tor.model`` import ``tor.effects``, which imports ``tor.model.derive`` — a runtime
  import cycle, not merely a breach of ``01.1``'s layering.
* **Derived values may not be cached on the aggregate.** ``17.5`` requirement 2 lists Load,
  Parry, the maxima, Standard of Living and the condition flags as recomputed on load,
  never stored, and ``04.7`` names storing a computed derived stat as an anti-pattern. So
  the inputs to a recomputation have to be reachable at every call.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from tor.effects.bus import EffectBus
from tor.effects.hooks import (
    DEFAULT_ENVIRONMENT,
    Contribution,
    Environment,
    Hook,
    HookContext,
    SceneKind,
)
from tor.errors import StateError
from tor.model.gear import ArmourType, ShieldType, WeaponType
from tor.model.ids import CombatantId

__all__ = ["DerivedBases", "GearIndex", "RulesContext"]

#: Hooks that recompute a *derived* value rather than applying a one-off contribution.
#: These must never consume a usage budget: ``19.5`` requires recomputing a derived stat
#: twice to give the same answer, and a budgeted Load effect burned on every recompute
#: would make Load depend on how often it had been asked for.
DERIVED_STAT_HOOKS: frozenset[Hook] = frozenset(
    {
        Hook.MODIFY_MAX_ENDURANCE,
        Hook.MODIFY_MAX_HOPE,
        Hook.MODIFY_PARRY,
        Hook.MODIFY_ATTRIBUTE_TN,
        Hook.MODIFY_ITEM_LOAD,
        Hook.MODIFY_SHIELD_PARRY,
        Hook.MODIFY_FELLOWSHIP_RATING,
        Hook.MODIFY_MOUNT_VIGOUR,
        Hook.MODIFY_USEFUL_ITEM_LIMIT,
    }
)


class GearIndex(Protocol):
    """Base statistics for gear types, by item id (``03.5``).

    A narrow read-only view per ``01.5``, rather than the whole ``ContentPack`` — which
    satisfies it structurally, so the session layer passes the pack itself. Keeping it a
    Protocol is what lets a rules test stub three dicts instead of loading a pack, and what
    keeps ``tor.rules.resources`` free of any ``tor.content`` import.
    """

    def weapon(self, item_id: str) -> WeaponType: ...

    def armour_type(self, item_id: str) -> ArmourType: ...

    def shield_type(self, item_id: str) -> ShieldType: ...


class DerivedBases(Protocol):
    """The three per-culture constants behind the derived stats (``03.4.2``).

    ``tor.content.entities.CultureDerived`` satisfies it. The engine must never assume a
    value for any of them — different cultures use different ones, and ``05.2`` makes them
    pack data precisely so the engine cannot.
    """

    @property
    def endurance_bonus(self) -> int: ...

    @property
    def hope_bonus(self) -> int: ...

    @property
    def parry_bonus(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RulesContext:
    """One per campaign, built by the session layer and threaded down explicitly.

    Frozen, so a scene's environment can never leak back into the campaign-wide context;
    :meth:`in_scene` returns a copy instead.

    Parameters taking one of these are named ``ctx``. The per-dispatch
    :class:`~tor.effects.hooks.HookContext` values built from it are named ``hook_ctx``, so
    the two never blur at a call site.
    """

    gear: GearIndex
    buses: Mapping[CombatantId, EffectBus]
    scene: SceneKind | None = None
    environment: Environment = DEFAULT_ENVIRONMENT

    @classmethod
    def single(
        cls,
        actor_id: CombatantId,
        *,
        bus: EffectBus,
        gear: GearIndex,
        scene: SceneKind | None = None,
        environment: Environment = DEFAULT_ENVIRONMENT,
    ) -> RulesContext:
        """A context for one actor — the common shape outside combat."""
        return cls(gear=gear, buses={actor_id: bus}, scene=scene, environment=environment)

    def bus(self, actor_id: CombatantId) -> EffectBus:
        """The actor's effect bus.

        A missing bus is a :class:`StateError`, not a ``RuleViolation``: it means the caller
        built a context that does not cover the actor it then asked about, which is a wiring
        bug rather than an illegal move (``01.6``).
        """
        try:
            return self.buses[actor_id]
        except KeyError as exc:
            raise StateError(
                f"no effect bus registered for {actor_id!r}; the RulesContext was built without it"
            ) from exc

    def hook_context(self, hook: Hook, actor: object, **extra: Any) -> HookContext:
        """A :class:`HookContext` carrying this context's scene and environment."""
        return HookContext(
            hook=hook,
            actor=actor,
            scene=self.scene,
            environment=self.environment,
            extra=extra,
        )

    def in_scene(
        self, scene: SceneKind | None, environment: Environment = DEFAULT_ENVIRONMENT
    ) -> RulesContext:
        """A copy scoped to one scene, sharing the same buses and gear index."""
        return replace(self, scene=scene, environment=environment)

    def consume(
        self,
        actor_id: CombatantId,
        contributions: Iterable[Contribution],
        hook_ctx: HookContext,
    ) -> None:
        """Record one use of every effect that contributed (``04.7``).

        ``EffectBus.collect`` is deliberately pure — it never touches a usage counter — so a
        caller that *commits* to what it collected has to say so. This is the single place
        that happens, which is what stops each subsystem from having to remember.

        A contribution to a derived-stat hook is never consumed: see
        :data:`DERIVED_STAT_HOOKS`. Consuming is otherwise unconditional and safe, because
        ``collect`` has already filtered out any effect that was out of budget, and
        ``consume`` is a no-op for an effect that has no budget at all.
        """
        if hook_ctx.hook in DERIVED_STAT_HOOKS:
            return
        bus = self.bus(actor_id)
        for contribution in contributions:
            bus.consume(contribution.source, hook_ctx)
