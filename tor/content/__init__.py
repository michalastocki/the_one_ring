"""Content packs (``05-content-schemas.md``).

Layer 3. The engine ships **zero** game data: cultures, callings, virtues, rewards,
weapons, armour, adversaries, patrons and every table are loaded from JSON packs the
implementer transcribes from their own licensed copy of the rulebook.

``content/example/`` is a small, deliberately non-canonical pack that exercises every
schema branch, so the whole test suite runs with no licensed data present.
"""

from __future__ import annotations

from tor.content.entities import (
    Adversary,
    AttributeRow,
    Calling,
    Culture,
    CultureDerived,
    EffectDefinition,
    Manifest,
    Patron,
    ProficiencyGrant,
    ShadowPath,
    StandardOfLivingTier,
    Undertaking,
)
from tor.content.loader import VALID_ABILITIES, load_pack, load_packs, validate
from tor.content.ops import Op, OpKind, Who, parse_ops
from tor.content.pack import ContentPack

__all__ = [
    "VALID_ABILITIES",
    "Adversary",
    "AttributeRow",
    "Calling",
    "ContentPack",
    "Culture",
    "CultureDerived",
    "EffectDefinition",
    "Manifest",
    "Op",
    "OpKind",
    "Patron",
    "ProficiencyGrant",
    "ShadowPath",
    "StandardOfLivingTier",
    "Undertaking",
    "Who",
    "load_pack",
    "load_packs",
    "parse_ops",
    "validate",
]
