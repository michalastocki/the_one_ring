"""The three Attributes (``03-domain-model.md`` §3.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Attribute", "AttributeSet"]


class Attribute(StrEnum):
    STRENGTH = "strength"
    HEART = "heart"
    WITS = "wits"


@dataclass(frozen=True, slots=True)
class AttributeSet:
    """A hero's three Attribute scores.

    Range 1-8 in practice: culture tables produce 2-7, and one cultural blessing adds 1.

    This does **not** compute Target Numbers. TNs are modifiable by effects
    (``MODIFY_ATTRIBUTE_TN``, for the *Prowess* Virtue and the *Weakening* Curse), so they
    belong to the derived-stat pipeline on the ``Hero``, not to a bare score.
    """

    strength: int
    heart: int
    wits: int

    def __post_init__(self) -> None:
        for attribute in Attribute:
            score = self.score(attribute)
            if score < 1:
                raise ValueError(f"{attribute} must be at least 1, got {score}")

    def score(self, attribute: Attribute) -> int:
        match attribute:
            case Attribute.STRENGTH:
                return self.strength
            case Attribute.HEART:
                return self.heart
            case Attribute.WITS:
                return self.wits

    def with_bonus(self, attribute: Attribute, delta: int) -> AttributeSet:
        """Return a copy with ``delta`` added to one Attribute.

        Used by the cultural blessing granting +1 to an Attribute of the player's choice,
        which must apply *before* derived stats are computed (``06.2``).
        """
        scores = {a.value: self.score(a) for a in Attribute}
        scores[attribute.value] += delta
        return AttributeSet(**scores)
