"""Derived stats (``03.4.2``, pattern P18).

Every derived value in the game — max Endurance, max Hope, Parry, Load, Attribute TNs,
Fellowship rating, a weapon's effective Damage — is *a base plus the sum of modifiers
contributed by effects*. One implementation, here.

**Derived values are never stored on the entity.** Storing a computed Parry invites drift
the moment a shield is smashed or a Virtue is taken. Recompute instead; ``17.5`` requires
the save file to omit them for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from tor.model.ids import EffectId

__all__ = ["DerivedStat", "Modifier", "PhaseBoundary"]


class PhaseBoundary(StrEnum):
    """When an expiring modifier lapses."""

    NEXT_FELLOWSHIP_PHASE = "next_fellowship_phase"
    NEXT_ADVENTURING_PHASE = "next_adventuring_phase"
    END_OF_COMBAT = "end_of_combat"
    END_OF_ROUND = "end_of_round"
    END_OF_JOURNEY = "end_of_journey"


@dataclass(frozen=True, slots=True)
class Modifier:
    """One contribution to a derived stat, with the effect that supplied it.

    Keeping ``source`` is what lets a UI explain a number and what lets
    ``EffectBus.unregister`` remove a smashed shield's Parry without touching base Parry.
    """

    source: EffectId
    delta: int = 0
    #: Overrides the value outright rather than adjusting it — Mithril replacing an
    #: item's Load. Two replacements on one stat are a ``ContentError`` at load (``04.2.2``).
    replacement: int | None = None
    #: ``None`` for a permanent modifier; otherwise the boundary at which it lapses.
    expires_at: PhaseBoundary | None = None


@dataclass(frozen=True, slots=True)
class DerivedStat:
    """A base value plus its modifiers. Immutable; recompute rather than mutate."""

    base: int
    modifiers: tuple[Modifier, ...] = ()

    @property
    def value(self) -> int:
        """The effective value.

        A replacement wins over the base and over any additive modifier; deltas then apply
        on top of whatever the base resolved to.
        """
        replacements = [m for m in self.modifiers if m.replacement is not None]
        base = replacements[-1].replacement if replacements else self.base
        assert base is not None
        return base + sum(m.delta for m in self.modifiers)

    def with_modifiers(self, extra: Iterable[Modifier]) -> DerivedStat:
        return DerivedStat(base=self.base, modifiers=(*self.modifiers, *extra))

    def expire(self, boundary: PhaseBoundary) -> DerivedStat:
        """Drop the modifiers scoped to ``boundary``."""
        return DerivedStat(
            base=self.base,
            modifiers=tuple(m for m in self.modifiers if m.expires_at is not boundary),
        )

    def explain(self) -> str:
        """A one-line derivation, for the CLI's audit output (``18.4``)."""
        parts = [str(self.base)]
        for modifier in self.modifiers:
            if modifier.replacement is not None:
                parts = [f"{modifier.replacement} (replaced by {modifier.source})"]
            elif modifier.delta:
                parts.append(f"{modifier.delta:+d} ({modifier.source})")
        return f"{' '.join(parts)} = {self.value}"
