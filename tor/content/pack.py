"""The loaded content pack (``05.1``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

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
from tor.errors import ContentError
from tor.model.gear import ArmourType, ShieldType, WeaponType
from tor.tables import LookupTable

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

    def effect(self, effect_id: str) -> EffectDefinition:
        return _require(self.effects, effect_id, "effect")

    def adversary(self, adversary_id: str) -> Adversary:
        return _require(self.adversaries, adversary_id, "adversary")

    def table(self, table_id: str) -> LookupTable[Any]:
        return _require(self.tables, table_id, "table")

    def song(self, song_id: str) -> SongTemplate:
        return _require(self.songs, song_id, "song")

    def effects_of_kind(self, kind: str) -> dict[str, EffectDefinition]:
        return {k: v for k, v in self.effects.items() if v.kind == kind}


def _require(section: Mapping[str, V], key: str, what: str) -> V:
    try:
        return section[key]
    except KeyError as exc:
        raise ContentError(f"no such {what}: {key!r}", entity_id=key) from exc
