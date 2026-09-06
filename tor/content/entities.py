"""Parsed content entities (``05-content-schemas.md``).

Frozen dataclasses mirroring the JSON a content pack supplies. The engine ships **zero**
game data: every culture, weapon, adversary and table here is loaded from a pack the
implementer transcribes from their own licensed copy of the rulebook.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tor.model.conditions import DriveKind, StandardOfLiving
from tor.model.gear import ArmourType, Craftsmanship, ShieldType, WeaponType
from tor.model.ids import AbilityId, AdversaryId, CallingId, CultureId, EffectId, ItemId

__all__ = [
    "Adversary",
    "AttributeRow",
    "Calling",
    "Culture",
    "CultureDerived",
    "EffectDefinition",
    "GearTypes",
    "Manifest",
    "Patron",
    "ProficiencyGrant",
    "ShadowPath",
    "SongTemplate",
    "StandardOfLivingTier",
    "Undertaking",
]


@dataclass(frozen=True, slots=True)
class Manifest:
    id: str
    name: str
    version: str = "0"
    #: Ids this pack is permitted to override. A collision that is *not* declared here is
    #: a ContentError — that is what keeps a supplement from quietly changing a base rule.
    overrides: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    """A declarative effect, as any of the Virtue/Reward/ability files supply it."""

    id: EffectId
    name: str
    factory: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    repeatable: bool = False
    #: Rewards only: which item kinds it may be applied to.
    applies_to: tuple[str, ...] = ()
    #: Cultural Virtues only.
    culture: CultureId | None = None
    min_wisdom: int = 0
    #: Enchanted Rewards only.
    craftsmanship: tuple[Craftsmanship, ...] = ()
    item_types: tuple[str, ...] = ()
    requires_bane: bool = False
    #: The ordinary Reward whose descriptor this Enchanted Reward shares; an item may not
    #: carry both (``13.7.3`` constraint 7).
    supersedes: EffectId | None = None
    requires_choice: Mapping[str, Any] = field(default_factory=dict)
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class AttributeRow:
    """One of a culture's six Attribute sets, keyed 1-6.

    The player either picks a row or rolls a Success die to select one; both are legal and
    land on the same table (``06.3``).
    """

    roll: int
    strength: int
    heart: int
    wits: int


@dataclass(frozen=True, slots=True)
class CultureDerived:
    """The three per-culture constants. Never defaulted — cultures genuinely differ."""

    endurance_bonus: int
    hope_bonus: int
    parry_bonus: int


@dataclass(frozen=True, slots=True)
class ProficiencyGrant:
    """One Combat Proficiency grant. ``choose`` is a list of ids, or ``"any"``."""

    rating: int
    choose: tuple[AbilityId, ...] | str


@dataclass(frozen=True, slots=True)
class Culture:
    id: CultureId
    name: str
    cultural_blessing: EffectId
    standard_of_living: StandardOfLiving
    derived: CultureDerived
    attribute_sets: tuple[AttributeRow, ...]
    skills: Mapping[AbilityId, int]
    favoured_skill_choices: tuple[AbilityId, ...]
    favoured_skill_count: int
    combat_proficiencies: tuple[ProficiencyGrant, ...]
    distinctive_feature_choices: tuple[EffectId, ...]
    distinctive_feature_count: int
    cultural_weakness: EffectId | None = None
    #: ``None`` means unrestricted; a list confines the culture to those weapons (``03.5.1``).
    allowed_weapons: tuple[ItemId, ...] | None = None
    forbidden_items: tuple[ItemId, ...] = ()
    #: Applied *before* derived stats, so it is captured as a stage-2 choice (``06.2``).
    attribute_bonus: Mapping[str, Any] | None = None
    languages: tuple[str, ...] = ()
    typical_names: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    adventuring_age: Mapping[str, int] = field(default_factory=dict)
    #: The Company's base Eye Awareness takes only the highest of these (``14.2``).
    eye_awareness_weight: int = 0


@dataclass(frozen=True, slots=True)
class Calling:
    id: CallingId
    name: str
    favoured_skill_choices: tuple[AbilityId, ...]
    favoured_skill_count: int
    distinctive_feature: EffectId
    shadow_path: str
    free_undertaking: str
    #: The one Calling feature that must name a creature type at acquisition (``05.3``).
    requires_subject: bool = False


@dataclass(frozen=True, slots=True)
class ShadowPath:
    """Four ordered Flaws, one per bout of madness (``11.7``)."""

    id: str
    calling: CallingId
    steps: tuple[EffectId, ...]


@dataclass(frozen=True, slots=True)
class Patron:
    id: str
    name: str
    fellowship_bonus: int
    benefit: EffectId
    favoured_callings: tuple[CallingId, ...] = ()
    agenda: str = ""


@dataclass(frozen=True, slots=True)
class StandardOfLivingTier:
    tier: StandardOfLiving
    #: ``None`` marks a tier accumulating Treasure never reaches — a starting condition.
    treasure_threshold: int | None
    useful_items: int
    mount_vigour: int | None


@dataclass(frozen=True, slots=True)
class Undertaking:
    id: str
    name: str
    yule_only: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class Adversary:
    """Kept separate from :class:`tor.model.adversary.Adversary`: this is the parsed row."""

    id: AdversaryId
    name: str
    attribute_level: int
    endurance: int
    might: int
    drive_kind: DriveKind
    drive: int
    parry: int
    armour: int
    attacks: tuple[Mapping[str, Any], ...]
    size: str = "human"
    creature_types: tuple[str, ...] = ()
    always_available_special_damage: tuple[str, ...] = ()
    fell_abilities: tuple[EffectId, ...] = ()
    distinctive_features: tuple[EffectId, ...] = ()
    icon_inverted: bool = True


#: Gear types are reused from tor.model rather than re-declared: a WeaponType is the same
#: object whether it came from a pack or was hand-built in a test.
GearTypes = WeaponType | ArmourType | ShieldType


@dataclass(frozen=True, slots=True)
class SongTemplate:
    """One authored song a Company may start with or draw on (``15.8``).

    A Company's songs are written during a Fellowship Phase, so most are player-authored
    at runtime; this is the pack-supplied stock. ``kind`` selects the venture the song
    suits — a Lay for a Council, a Song of Victory for Combat, a Walking-song for a
    Journey — and is the only mechanical field.
    """

    id: str
    title: str
    kind: str
