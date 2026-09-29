"""The loaded content pack (``05.1``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar, cast

from tor.content.entities import (
    Adversary,
    Calling,
    Culture,
    EffectDefinition,
    Manifest,
    Patron,
    ShadowPath,
    SongTemplate,
    StandardOfLivingTier,
    Undertaking,
)
from tor.effects.bus import Effect
from tor.effects.library import build_effect
from tor.errors import ContentError
from tor.model.conditions import StandardOfLiving
from tor.model.gear import ArmourType, ShieldType, WeaponType
from tor.model.ids import EffectId
from tor.tables import CostLadder, LookupTable

__all__ = ["ContentPack"]

K = TypeVar("K")
V = TypeVar("V")


def _merge_section(
    base: Mapping[K, V],
    incoming: Mapping[K, V],
    *,
    allowed: frozenset[str],
    section: str,
) -> dict[K, V]:
    """Merge one section, refusing a collision the incoming pack has not declared.

    A supplement may add entries freely, but may override an existing id only if its
    manifest names it. A **silent** collision is a ``ContentError`` — that is what keeps a
    supplement from quietly changing a base rule.
    """
    merged = dict(base)
    for key, value in incoming.items():
        if key in merged and str(key) not in allowed:
            raise ContentError(
                f"{section} id {key!r} already exists; a pack that means to replace it "
                "must name it in its manifest 'overrides'",
                entity_id=str(key),
            )
        merged[key] = value
    return merged


@dataclass(frozen=True, slots=True)
class ContentPack:
    """Everything one or more packs supply, keyed by id."""

    manifest: Manifest
    cultures: Mapping[str, Culture] = field(default_factory=dict)
    callings: Mapping[str, Calling] = field(default_factory=dict)
    weapons: Mapping[str, WeaponType] = field(default_factory=dict)
    armour: Mapping[str, ArmourType] = field(default_factory=dict)
    shields: Mapping[str, ShieldType] = field(default_factory=dict)
    #: Every declarative effect, whatever file it came from — Virtues, Cultural Virtues,
    #: Rewards, Enchanted Rewards, Distinctive Features, Flaws, Fell Abilities, Patron
    #: benefits. One namespace, because ids are globally unique (``20.11``).
    effects: Mapping[str, EffectDefinition] = field(default_factory=dict)
    shadow_paths: Mapping[str, ShadowPath] = field(default_factory=dict)
    adversaries: Mapping[str, Adversary] = field(default_factory=dict)
    patrons: Mapping[str, Patron] = field(default_factory=dict)
    standards_of_living: tuple[StandardOfLivingTier, ...] = ()
    undertakings: Mapping[str, Undertaking] = field(default_factory=dict)
    tables: Mapping[str, LookupTable[Any]] = field(default_factory=dict)
    names: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)
    songs: Mapping[str, SongTemplate] = field(default_factory=dict)
    #: Display-name overrides only; the 18-skill grid itself is engine-fixed
    #: (``tor.model.abilities``), so a pack may rename a Skill but never add one.
    skill_names: Mapping[str, str] = field(default_factory=dict)

    def merge(self, other: ContentPack) -> ContentPack:
        """Layer ``other`` over this pack (``05.1``)."""
        allowed = other.manifest.overrides
        sections: dict[str, Any] = {}
        for name in (
            "cultures",
            "callings",
            "weapons",
            "armour",
            "shields",
            "effects",
            "shadow_paths",
            "adversaries",
            "patrons",
            "undertakings",
            "tables",
            "names",
            "songs",
            "skill_names",
        ):
            sections[name] = _merge_section(
                getattr(self, name), getattr(other, name), allowed=allowed, section=name
            )
        sections["standards_of_living"] = other.standards_of_living or self.standards_of_living
        return replace(self, manifest=other.manifest, **sections)

    # -- lookups ---------------------------------------------------------------------

    def culture(self, culture_id: str) -> Culture:
        return _require(self.cultures, culture_id, "culture")

    def calling(self, calling_id: str) -> Calling:
        return _require(self.callings, calling_id, "calling")

    def weapon(self, item_id: str) -> WeaponType:
        return _require(self.weapons, item_id, "weapon")

    def armour_type(self, item_id: str) -> ArmourType:
        """One armour or helm type. Named ``armour_type`` because ``armour`` is the field."""
        return _require(self.armour, item_id, "armour")

    def shield_type(self, item_id: str) -> ShieldType:
        """One shield type. Named for the same reason as :meth:`armour_type`."""
        return _require(self.shields, item_id, "shield")

    def effect(self, effect_id: str) -> EffectDefinition:
        return _require(self.effects, effect_id, "effect")

    def adversary(self, adversary_id: str) -> Adversary:
        return _require(self.adversaries, adversary_id, "adversary")

    def shadow_path(self, path_id: str) -> ShadowPath:
        return _require(self.shadow_paths, path_id, "shadow path")

    def patron(self, patron_id: str) -> Patron:
        return _require(self.patrons, patron_id, "patron")

    def undertaking(self, undertaking_id: str) -> Undertaking:
        return _require(self.undertakings, undertaking_id, "undertaking")

    def living_tier(self, tier: StandardOfLiving) -> StandardOfLivingTier:
        """One row of the Standard of Living table (``05.9``).

        The loader's completeness check guarantees all six tiers are present, so a missing
        one would be a pack that never loaded.
        """
        for row in self.standards_of_living:
            if row.tier is tier:
                return row
        raise ContentError(  # pragma: no cover - the loader rejects an incomplete ladder
            f"no such standard of living tier: {tier.name.lower()!r}", entity_id=tier.name.lower()
        )

    def living_ladder(self) -> tuple[tuple[StandardOfLiving, int | None], ...]:
        """The ``(tier, threshold)`` pairs ``standard_of_living_for`` reads (``03.7``)."""
        return tuple((row.tier, row.treasure_threshold) for row in self.standards_of_living)

    def experience_costs(self) -> Mapping[str, Any]:
        """The whole ``experience_costs`` row (``05.10``) — budgets and heir caps included."""
        return cast("Mapping[str, Any]", self.table("experience_costs").lookup(1))

    def cost_ladder(self, group: str, ladder: str) -> CostLadder:
        """One ladder from ``05.10``, e.g. ``("previous_experience", "skills")``.

        ``05.10`` opens with "Two separate ladders. Do not conflate them", so the group is
        never defaulted: a caller has to say which of the two it means.
        """
        costs = self.experience_costs()
        try:
            rows = costs[group][ladder]
        except (KeyError, TypeError) as exc:
            raise ContentError(
                f"experience_costs has no {group!r}/{ladder!r} ladder",
                entity_id="experience_costs",
            ) from exc
        return CostLadder.from_rows(rows, ladder_id=f"{group}.{ladder}")

    def table(self, table_id: str) -> LookupTable[Any]:
        return _require(self.tables, table_id, "table")

    def song(self, song_id: str) -> SongTemplate:
        return _require(self.songs, song_id, "song")

    def effects_of_kind(self, kind: str) -> dict[str, EffectDefinition]:
        return {k: v for k, v in self.effects.items() if v.kind == kind}

    def instantiate(self, effect_id: str, params: Mapping[str, Any] | None = None) -> Effect:
        """Build the live :class:`Effect` for a declaration (``04.5``).

        The bridge from a pack's declarative row to the object an ``EffectBus`` registers.
        Everything about *which* effects a hero carries is the caller's business — this
        only turns one id into one effect.

        ``params`` layers the player's choices at acquisition over the declaration's own —
        which two Skills *Mastery* favours, which Attribute *Prowess* lowers, which creature
        type a Hatred names. That is what ``EffectDefinition.requires_choice`` announces, and
        what lets one shared definition serve every hero who takes it (``04.2``).
        """
        definition = self.effect(effect_id)
        merged = {**definition.params, **(params or {})}
        return build_effect(EffectId(definition.id), definition.kind, definition.factory, merged)


def _require(section: Mapping[str, V], key: str, what: str) -> V:
    try:
        return section[key]
    except KeyError as exc:
        raise ContentError(f"no such {what}: {key!r}", entity_id=key) from exc
