"""ActionDie — the d12 Action Die used in The One Ring RPG."""

from __future__ import annotations

import random

from .models import ActionDieRoll, DieResult


class ActionDie:
    """Represents the twelve-sided Action Die (d12).

    Face mapping:
      1–10  → nominal integer value
      11    → EYE_OF_SAURON  (numeric value = 0)
      12    → GANDALF_RUNE   (triggers automatic success)
    """

    def roll(self, rng: random.Random | None = None) -> ActionDieRoll:
        """Roll the die and return an :class:`ActionDieRoll`.

        Parameters
        ----------
        rng:
            Optional :class:`random.Random` instance for deterministic tests.
            When *None*, the module-level :func:`random.randint` is used.
        """
        raw = (rng or random).randint(1, 12)
        result = self._interpret(raw)
        return ActionDieRoll(raw=raw, result=result)

    @staticmethod
    def _interpret(raw: int) -> DieResult | int:
        if raw == 12:
            return DieResult.GANDALF_RUNE
        if raw == 11:
            return DieResult.EYE_OF_SAURON
        return raw
