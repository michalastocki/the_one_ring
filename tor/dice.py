"""The two dice, and the source of all randomness (``02-core-dice-and-rolls.md`` §2.1).

Layer 0. This module knows nothing of characters, rules, or the setting — deliberately so.
The icon faces are named ``RUNE``, ``EYE`` and "success icon" rather than by their in-world
names, which keeps the module free of setting vocabulary and reusable.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from tor.errors import StateError

__all__ = [
    "FEAT_FACES",
    "FeatFace",
    "FeatValue",
    "Randomness",
    "ScriptedRandomness",
    "SuccessDie",
    "SystemRandomness",
    "auto_failure_face",
    "auto_success_face",
    "feat_numeric",
    "feat_ordering_key",
]


class FeatFace(StrEnum):
    """The two special faces of the Feat die. Numeric faces are plain ``int`` 1..10."""

    EYE = "eye"
    RUNE = "rune"


#: A Feat die result: a number 1..10, or one of the two icon faces.
FeatValue = int | FeatFace

#: Every outcome of the Feat die, for uniform sampling and for table-coverage checks.
FEAT_FACES: tuple[FeatValue, ...] = (*range(1, 11), FeatFace.EYE, FeatFace.RUNE)


@dataclass(frozen=True, slots=True)
class SuccessDie:
    """One Success die (a d6).

    Faces 1-3 are printed *outlined*, 4-6 *solid*. The distinction is mechanically live:
    it is what the Weary condition keys off. The 6 additionally carries a success icon,
    and because 6 is a solid face a Weary character still scores icons on it.
    """

    face: int

    def __post_init__(self) -> None:
        if not 1 <= self.face <= 6:
            raise ValueError(f"a Success die shows 1..6, not {self.face}")

    @property
    def is_outlined(self) -> bool:
        return self.face <= 3

    @property
    def has_icon(self) -> bool:
        return self.face == 6


def auto_success_face(*, inverted: bool) -> FeatFace:
    """The face that succeeds regardless of Target Number.

    The rune for player-heroes; the eye for servants of the Shadow, whose icons swap
    (``12.4``). This inversion is one boolean on one code path — there is no parallel
    implementation for adversaries anywhere in the engine.
    """
    return FeatFace.EYE if inverted else FeatFace.RUNE


def auto_failure_face(*, inverted: bool) -> FeatFace:
    """The face that fails regardless of total, *when the roller is subject to that rule*.

    An eye normally; a rune under inversion. It only bites when the ``RollRequest`` sets
    ``eye_is_auto_failure`` — the Miserable condition, or the Ill-luck curse which mimics
    it without the bearer actually being Miserable (``13.8``).
    """
    return FeatFace.RUNE if inverted else FeatFace.EYE


def feat_numeric(value: FeatValue, *, inverted: bool) -> int:
    """The numeric contribution of a Feat die to a roll total.

    **Both** icon faces contribute ``0``. The auto-success face needs no numeric value
    because it short-circuits the Target Number comparison entirely; the auto-failure face
    is simply worth nothing.

    ``inverted`` therefore changes nothing here — it is accepted so that call sites read
    uniformly alongside the other face helpers, which do depend on it.
    """
    if isinstance(value, FeatFace):
        return 0
    return value


def feat_ordering_key(value: FeatValue, *, inverted: bool) -> int:
    """Ordering used to keep the best or worst of two Feat dice.

    With ``inverted=False``: ``EYE < 1 < ... < 10 < RUNE``.
    With ``inverted=True``:  ``RUNE < 1 < ... < 10 < EYE``.

    Favoured keeps the maximum under this ordering, Ill-favoured the minimum.
    """
    if isinstance(value, FeatFace):
        return 11 if value is auto_success_face(inverted=inverted) else 0
    return value


@runtime_checkable
class Randomness(Protocol):
    """The single source of randomness. Injected everywhere; never global (``00.4``)."""

    def feat(self) -> FeatValue:
        """One Feat die: uniform over ``{1..10, EYE, RUNE}``."""
        ...

    def success(self) -> SuccessDie:
        """One Success die: uniform over ``{1..6}``."""
        ...


class SystemRandomness:
    """Production randomness, seedable for reproducibility."""

    __slots__ = ("_rng",)

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def feat(self) -> FeatValue:
        return FEAT_FACES[self._rng.randrange(len(FEAT_FACES))]

    def success(self) -> SuccessDie:
        return SuccessDie(self._rng.randint(1, 6))


class ScriptedRandomness:
    """A test double that hands out a fixed queue of dice, in order.

    **Raises ``StateError`` when a queue runs dry.** Never refill, never cycle: a test that
    scripts two Success dice and sees the engine ask for a third has caught a real bug, and
    a silent fallback would hide it. That over-roll detection is the entire point.

    Pair it with :attr:`exhausted` at the end of a rules test — rolling too *few* dice is
    as much a bug as rolling too many (``19.2``).
    """

    __slots__ = ("_feats", "_successes")

    def __init__(
        self,
        feats: Sequence[FeatValue] = (),
        successes: Sequence[int] = (),
    ) -> None:
        self._feats: deque[FeatValue] = deque(feats)
        self._successes: deque[int] = deque(successes)

    def feat(self) -> FeatValue:
        if not self._feats:
            raise StateError(
                "the scripted Feat die queue is empty: "
                "the engine rolled more Feat dice than the test scripted"
            )
        return self._feats.popleft()

    def success(self) -> SuccessDie:
        if not self._successes:
            raise StateError(
                "the scripted Success die queue is empty: "
                "the engine rolled more Success dice than the test scripted"
            )
        return SuccessDie(self._successes.popleft())

    @property
    def exhausted(self) -> bool:
        """True when both queues have been drained exactly."""
        return not self._feats and not self._successes

    @property
    def remaining(self) -> tuple[int, int]:
        """``(feat dice left, success dice left)`` — for a useful assertion message."""
        return len(self._feats), len(self._successes)
