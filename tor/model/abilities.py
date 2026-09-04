"""Skills, Combat Proficiencies, and the rank abilities (``03.3``, ``20.1``-``20.3``).

The 18-Skill grid is **engine knowledge**, not content. Two things depend on its
structure: the roll pipeline needs each Skill's governing Attribute to pick a Target
Number, and Blessing generation (``13.5``) rolls down the *group* axis. A content pack may
rename a Skill for display, but not move it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from tor.model.attributes import Attribute
from tor.model.ids import AbilityId

__all__ = [
    "BRAWLING",
    "COMBAT_PROFICIENCIES",
    "RANK_ABILITIES",
    "SKILLS",
    "SKILL_GROUP_ORDER",
    "VALOUR",
    "WISDOM",
    "SkillGroup",
    "SkillMeta",
    "ability_attribute",
    "is_combat_proficiency",
    "is_skill",
    "skills_in_group",
]


class SkillGroup(StrEnum):
    PERSONALITY = "personality"
    MOVEMENT = "movement"
    PERCEPTION = "perception"
    SURVIVAL = "survival"
    CUSTOM = "custom"
    VOCATION = "vocation"


#: The order the Blessings table rolls down (Success die 1-6) — ``20.1``.
SKILL_GROUP_ORDER: tuple[SkillGroup, ...] = (
    SkillGroup.PERSONALITY,
    SkillGroup.MOVEMENT,
    SkillGroup.PERCEPTION,
    SkillGroup.SURVIVAL,
    SkillGroup.CUSTOM,
    SkillGroup.VOCATION,
)


@dataclass(frozen=True, slots=True)
class SkillMeta:
    attribute: Attribute
    group: SkillGroup


def _grid() -> dict[AbilityId, SkillMeta]:
    """The 6x3 grid of ``20.1``: row = group, column = governing Attribute."""
    rows: dict[SkillGroup, tuple[str, str, str]] = {
        SkillGroup.PERSONALITY: ("awe", "enhearten", "persuade"),
        SkillGroup.MOVEMENT: ("athletics", "travel", "stealth"),
        SkillGroup.PERCEPTION: ("awareness", "insight", "scan"),
        SkillGroup.SURVIVAL: ("hunting", "healing", "explore"),
        SkillGroup.CUSTOM: ("song", "courtesy", "riddle"),
        SkillGroup.VOCATION: ("craft", "battle", "lore"),
    }
    columns = (Attribute.STRENGTH, Attribute.HEART, Attribute.WITS)
    return {
        AbilityId(name): SkillMeta(attribute=attribute, group=group)
        for group, names in rows.items()
        for name, attribute in zip(names, columns, strict=True)
    }


#: The 18 Common Skills, each with its governing Attribute and Skill group.
SKILLS: Mapping[AbilityId, SkillMeta] = MappingProxyType(_grid())

#: The four Combat Proficiencies (``20.2``). All roll against the STRENGTH TN.
COMBAT_PROFICIENCIES: tuple[AbilityId, ...] = (
    AbilityId("axes"),
    AbilityId("bows"),
    AbilityId("spears"),
    AbilityId("swords"),
)

VALOUR = AbilityId("valour")
WISDOM = AbilityId("wisdom")
#: VALOUR rolls against HEART, WISDOM against WITS (``20.3``).
RANK_ABILITIES: Mapping[AbilityId, Attribute] = MappingProxyType(
    {VALOUR: Attribute.HEART, WISDOM: Attribute.WITS}
)

#: Not a Combat Proficiency: an attack *mode* resolved at the highest of the four,
#: taking -1d (``03.3``). Its rating is computed, never stored.
BRAWLING = AbilityId("brawling")


def is_skill(ability: AbilityId) -> bool:
    return ability in SKILLS


def is_combat_proficiency(ability: AbilityId) -> bool:
    return ability in COMBAT_PROFICIENCIES


def skills_in_group(group: SkillGroup) -> tuple[AbilityId, ...]:
    """The three Skills of a group, in STRENGTH, HEART, WITS order.

    That order is what the second Blessings die selects down: 1-2 picks the STRENGTH
    Skill, 3-4 the HEART Skill, 5-6 the WITS Skill (``20.1``).
    """
    ordered = (Attribute.STRENGTH, Attribute.HEART, Attribute.WITS)
    by_attribute = {SKILLS[a].attribute: a for a in SKILLS if SKILLS[a].group is group}
    return tuple(by_attribute[attribute] for attribute in ordered)


def ability_attribute(ability: AbilityId) -> Attribute:
    """The Attribute whose Target Number this ability rolls against.

    Every Combat Proficiency — and Brawling — rolls against STRENGTH.
    """
    if ability in SKILLS:
        return SKILLS[ability].attribute
    if ability in RANK_ABILITIES:
        return RANK_ABILITIES[ability]
    if ability in COMBAT_PROFICIENCIES or ability == BRAWLING:
        return Attribute.STRENGTH
    raise KeyError(f"unknown ability {ability!r}")
