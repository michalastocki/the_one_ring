"""SuccessDie — the d6 Success Die used in The One Ring RPG."""

from __future__ import annotations

import random

from .models import FaceType, SuccessDieRoll


class SuccessDie:
    """Represents the six-sided Success Die (d6).

    Face mapping:
      1, 2, 3 → HOLLOW  (partial; value = 0 when character is Weary)
      4, 5    → FILLED  (full; value unchanged regardless of Weary)
      6       → TENGWAR (full value = 6; grants one ⭐ Tengwar marker)

    Weary rule:
      When *is_weary* is True the HOLLOW faces (1–3) contribute 0 to the total
      instead of their nominal value.
    """

    def roll(
        self,
        is_weary: bool = False,
        rng: random.Random | None = None,
    ) -> SuccessDieRoll:
        """Roll the die and return a :class:`SuccessDieRoll`.

        Parameters
        ----------
        is_weary:
            Apply the Weary rule (HOLLOW faces count as 0).
        rng:
            Optional :class:`random.Random` instance for deterministic tests.
        """
        raw = (rng or random).randint(1, 6)
        face_type, is_tengwar = self._classify(raw)
        value = self._apply_weary(raw, face_type, is_weary)
        return SuccessDieRoll(raw=raw, face_type=face_type, value=value, is_tengwar=is_tengwar)

    @staticmethod
    def _classify(raw: int) -> tuple[FaceType, bool]:
        if raw == 6:
            return FaceType.TENGWAR, True
        if raw in (4, 5):
            return FaceType.FILLED, False
        return FaceType.HOLLOW, False  # raw in (1, 2, 3)

    @staticmethod
    def _apply_weary(raw: int, face_type: FaceType, is_weary: bool) -> int:
        if is_weary and face_type == FaceType.HOLLOW:
            return 0
        return raw
