"""RollPool — builds and executes a pool of Action Dice and Success Dice."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .action_die import ActionDie
from .models import ActionDieRoll, DieResult, Stance, SuccessDieRoll
from .success_die import SuccessDie


@dataclass
class RollPool:
    """Encapsulates the parameters of a single test's dice pool.

    Parameters
    ----------
    ability_level:
        Base number of Success Dice (0–6, representing the tested skill rank).
    stance:
        NORMAL, ENHANCED (roll 2 AD keep better), or WEAKENED (roll 2 AD keep worse).
    is_miserable:
        Character is in the Miserable state — EYE triggers automatic failure.
    is_weary:
        Character is in the Weary state — HOLLOW faces on Success Dice count as 0.
    bonus_dice:
        Additional Success Dice (from Hope, companion support, etc.).
    penalty_dice:
        Dice removed from the pool (circumstance penalties, etc.).

    Notes
    -----
    If both *bonus_dice* and *penalty_dice* are supplied they are applied as a
    net modifier (cumulative).  The pool size is clamped to a minimum of 0.

    When ENHANCED and WEAKENED are both active (e.g. via different sources)
    they cancel out and the test resolves as NORMAL.
    """

    ability_level: int
    stance: Stance = Stance.NORMAL
    is_miserable: bool = False
    is_weary: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0

    # Internal helpers (not set by caller)
    _action_die: ActionDie = field(default_factory=ActionDie, repr=False)
    _success_die: SuccessDie = field(default_factory=SuccessDie, repr=False)

    @property
    def success_dice_count(self) -> int:
        """Resolved number of Success Dice after applying all modifiers."""
        return max(0, self.ability_level + self.bonus_dice - self.penalty_dice)

    def roll(
        self, rng: random.Random | None = None
    ) -> tuple[ActionDieRoll, list[SuccessDieRoll]]:
        """Execute the dice pool.

        Returns
        -------
        action_die_result:
            The single selected Action Die result (after applying ENHANCED /
            WEAKENED if applicable).
        success_dice_results:
            List of individual Success Die results.
        """
        effective_stance = self._effective_stance()
        action_die_result = self._roll_action_die(effective_stance, rng)
        success_dice_results = [
            self._success_die.roll(is_weary=self.is_weary, rng=rng)
            for _ in range(self.success_dice_count)
        ]
        return action_die_result, success_dice_results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _effective_stance(self) -> Stance:
        """Return the effective stance (pass-through; cancellation handled at pool creation)."""
        return self.stance

    def _roll_action_die(
        self, stance: Stance, rng: random.Random | None
    ) -> ActionDieRoll:
        if stance == Stance.NORMAL:
            return self._action_die.roll(rng)

        # ENHANCED or WEAKENED: roll two dice, pick based on comparison key.
        die1 = self._action_die.roll(rng)
        die2 = self._action_die.roll(rng)

        if stance == Stance.ENHANCED:
            return max(die1, die2, key=lambda d: d.comparison_key)
        else:  # WEAKENED
            return min(die1, die2, key=lambda d: d.comparison_key)
