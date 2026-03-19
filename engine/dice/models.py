"""Data models, enumerations, and result types for The One Ring dice engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DieResult(Enum):
    """Special face results on the Action Die (d12)."""

    GANDALF_RUNE = "gandalf_rune"   # face 12 — automatic success
    EYE_OF_SAURON = "eye_of_sauron"  # face 11 — value 0; auto-failure when Miserable


class FaceType(Enum):
    """Face category on the Success Die (d6)."""

    HOLLOW = "hollow"   # faces 1–3
    FILLED = "filled"   # faces 4–5
    TENGWAR = "tengwar"  # face 6 — grants a Tengwar (⭐) marker


class Stance(Enum):
    """The stance / condition modifier applied to a single test."""

    NORMAL = "normal"
    ENHANCED = "enhanced"    # roll 2 Action Dice, keep the better
    WEAKENED = "weakened"    # roll 2 Action Dice, keep the worse


class TestOutcome(Enum):
    """High-level outcome of a resolved test."""

    SUCCESS = "success"
    FAILURE = "failure"
    AUTOMATIC_SUCCESS = "automatic_success"   # triggered by GANDALF_RUNE
    AUTOMATIC_FAILURE = "automatic_failure"   # triggered by EYE + MISERABLE


class SuccessQuality(Enum):
    """Quality level when the outcome is a success."""

    BASIC_SUCCESS = "basic_success"                # 0 Tengwar
    GREAT_SUCCESS = "great_success"                # 1 Tengwar
    EXTRAORDINARY_SUCCESS = "extraordinary_success"  # 2+ Tengwar


class TestType(Enum):
    """The four categories of tests defined by the rules."""

    SKILL = "skill"
    COMBAT = "combat"
    SHADOW = "shadow"
    MAGICAL = "magical"


class CharacterState(Enum):
    """Persistent character states that affect dice rolls."""

    MISERABLE = "miserable"  # Shadow ≥ current Hope
    WEARY = "weary"          # Endurance ≤ Load
    WOUNDED = "wounded"      # character carries an active Wound


class WeaponType(Enum):
    """Weapon categories used to determine special-attack bonuses."""

    SWORD = "sword"
    BOW = "bow"
    SPEAR = "spear"
    AXE = "axe"
    UNARMED = "unarmed"


# ---------------------------------------------------------------------------
# Intermediate roll results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionDieRoll:
    """The result of rolling one Action Die (d12)."""

    raw: int  # 1–12 as rolled
    result: DieResult | int  # DieResult.GANDALF_RUNE | DieResult.EYE_OF_SAURON | int 1–10

    @property
    def numeric_value(self) -> int:
        """Numeric contribution to the total (0 for EYE, 1–10 for normal faces)."""
        if self.result == DieResult.EYE_OF_SAURON:
            return 0
        if self.result == DieResult.GANDALF_RUNE:
            return 12  # used only for ENHANCED/WEAKENED comparison
        return int(self.result)

    @property
    def comparison_key(self) -> int:
        """Ordering key for ENHANCED / WEAKENED selection.

        EYE_OF_SAURON (raw 11) is the worst possible outcome → key 0.
        Normal faces 1–10 keep their face value as key.
        GANDALF_RUNE (raw 12) is the best possible outcome → key 13.
        """
        if self.result == DieResult.EYE_OF_SAURON:
            return 0
        if self.result == DieResult.GANDALF_RUNE:
            return 13
        return int(self.result)


@dataclass(frozen=True)
class SuccessDieRoll:
    """The result of rolling one Success Die (d6)."""

    raw: int          # 1–6 as rolled
    face_type: FaceType
    value: int        # 0 when WEARY + HOLLOW; otherwise 1–6
    is_tengwar: bool  # True only when face_type == TENGWAR


@dataclass
class TestResult:
    """Full result of a resolved test."""

    outcome: TestOutcome
    success_quality: Optional[SuccessQuality] = None

    # Dice details
    action_die: Optional[ActionDieRoll] = None          # None for MagicalTest
    success_dice: list[SuccessDieRoll] = field(default_factory=list)

    # Computed totals
    total: int = 0
    target_number: int = 0
    tengwar_count: int = 0

    # Combat-specific extras
    break_defense_triggered: bool = False
    break_defense_result: Optional["TestResult"] = None

    # Shadow-test specific
    shadow_reduction: int = 0
