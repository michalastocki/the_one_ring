"""The universal roll (``02-core-dice-and-rolls.md`` §2.3).

Seam A, and the most important module in the codebase. *Every* die roll in the game funnels
through :func:`resolve` — Skill rolls, attack rolls, PROTECTION rolls, Shadow Tests,
Marching Tests, Wound Severity, Endurance Loss, Journey Event nature, Magical Treasure.
Subsystems build a :class:`RollRequest`, hand it over, and read the :class:`RollResult`.

The consequences of Weary, Miserable, Favoured, Ill-favoured, Hope spending, Inspiration,
support and bonus dice are implemented **once**, here. No subsystem re-implements
"outlined dice count as zero".

``build_request`` is the **only** place that consults the ``EffectBus`` for roll
modification (``02.3.1``). Subsystems call it and then :func:`resolve`; they never assemble
a ``RollRequest`` by hand, except for pure table rolls that have no character behind them.

.. note::
   The spec is internally inconsistent on one point, and this module resolves it
   deliberately. ``01.1`` lists ``tor.rolls | tor.effects | tor.tables`` as independent
   siblings, while ``02.3.1`` puts ``build_request`` in ``tor.rolls`` and has it read the
   ``EffectBus`` — which is an import between two supposed siblings. Both cannot hold.
   We follow ``02.3.1``, the more specific statement, and place ``tor.rolls`` one layer
   *above* ``tor.effects`` in the import-linter contract. The direction is also the better
   design: the hook mechanism has no business knowing what a ``RollRequest`` is, whereas
   assembling one plainly requires the effects that modify it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from tor.dice import (
    FeatFace,
    FeatValue,
    Randomness,
    SuccessDie,
    auto_failure_face,
    auto_success_face,
    feat_numeric,
    feat_ordering_key,
)
from tor.effects.bus import EffectBus
from tor.effects.hooks import (
    DEFAULT_ENVIRONMENT,
    Environment,
    FlagContribution,
    Hook,
    HookContext,
    NumericContribution,
    SceneKind,
)
from tor.errors import RuleViolation
from tor.model.ids import AbilityId, TaskId

__all__ = [
    "AttemptLedger",
    "AutoKind",
    "Degree",
    "FeatDicePolicy",
    "IconBudget",
    "Outcome",
    "RollPurpose",
    "RollRequest",
    "RollResult",
    "RollingCharacter",
    "SupportInput",
    "attribute_tn",
    "build_request",
    "resolve",
]


class FeatDicePolicy(StrEnum):
    NORMAL = "normal"
    FAVOURED = "favoured"
    ILL_FAVOURED = "ill_favoured"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    #: The roll carried no Target Number — a table roll, read for its face alone.
    NO_TN = "no_tn"


class Degree(StrEnum):
    """Degree of success. Only meaningful when the outcome is ``SUCCESS``."""

    NONE = "none"
    SUCCESS = "success"
    GREAT_SUCCESS = "great_success"
    EXTRAORDINARY_SUCCESS = "extraordinary_success"


class AutoKind(StrEnum):
    """Why the Target Number comparison was bypassed, if it was."""

    AUTO_SUCCESS = "auto_success"
    AUTO_FAILURE = "auto_failure"
    MAGICAL = "magical"


class RollPurpose(StrEnum):
    """What the roll is *for*.

    Effects predicate on this — "VALOUR rolls are Favoured", "+1d on Shadow Tests against
    Greed" — so it is the hook by which a Cultural Blessing reaches a roll without the
    roll's caller knowing the blessing exists.
    """

    GENERIC = "generic"
    SKILL = "skill"
    ATTACK = "attack"
    PROTECTION = "protection"
    VALOUR_TEST = "valour_test"
    WISDOM_TEST = "wisdom_test"
    SHADOW_TEST = "shadow_test"
    COMBAT_TASK = "combat_task"
    MARCHING_TEST = "marching_test"
    JOURNEY_EVENT = "journey_event"
    COUNCIL = "council"
    ENDEAVOUR = "endeavour"
    WOUND_SEVERITY = "wound_severity"
    ENDURANCE_LOSS = "endurance_loss"
    FIRST_AID = "first_aid"
    SONG = "song"


def attribute_tn(score: int, *, short_campaign: bool = False) -> int:
    """The Target Number derived from an Attribute score (``02.3.8``).

    ``short_campaign`` derives from 18 instead of 20 — an official option for short
    campaigns and one-shots. It is a campaign-level setting applied when the
    ``AttributeSet`` computes TNs, never ad hoc at a call site.
    """
    return (18 if short_campaign else 20) - score


@dataclass(frozen=True, slots=True)
class RollRequest:
    """Everything :func:`resolve` needs, and nothing it does not."""

    ability: AbilityId | None = None
    rating: int = 0
    target_number: int | None = None

    #: Effect ids making the roll Favoured. Kept so a UI can explain *why*.
    favoured_sources: tuple[str, ...] = ()
    ill_favoured_sources: tuple[str, ...] = ()

    bonus_dice: int = 0
    penalty_dice: int = 0

    weary: bool = False
    #: Set by the Miserable condition, and by the Ill-luck curse (``13.8``) which mimics it
    #: without the bearer actually being Miserable.
    eye_is_auto_failure: bool = False
    #: True for servants of the Shadow, whose two icon faces swap meaning (``12.4``).
    icon_inverted: bool = False
    #: Bypasses the Target Number entirely; the Success dice are still rolled (``02.3.7``).
    magical_success: bool = False
    purpose: RollPurpose = RollPurpose.GENERIC

    def __post_init__(self) -> None:
        if self.rating < 0:
            raise ValueError(f"ability rating cannot be negative, got {self.rating}")

    @property
    def policy(self) -> FeatDicePolicy:
        """Favoured and Ill-favoured **cancel, regardless of counts** (``02.3.2``).

        Two Favoured sources and one Ill-favoured source still yields NORMAL. Sources do
        not stack on either side: the policy is a three-way state, not a tally.
        """
        favoured = bool(self.favoured_sources)
        ill_favoured = bool(self.ill_favoured_sources)
        if favoured and ill_favoured:
            return FeatDicePolicy.NORMAL
        if favoured:
            return FeatDicePolicy.FAVOURED
        if ill_favoured:
            return FeatDicePolicy.ILL_FAVOURED
        return FeatDicePolicy.NORMAL

    @property
    def dice_count(self) -> int:
        """Success dice to roll. Floored at zero — never negative (``02.3.3``).

        A character can be reduced to rolling only the Feat die, never fewer.
        """
        return max(0, self.rating + self.bonus_dice - self.penalty_dice)


@dataclass(frozen=True, slots=True)
class RollResult:
    """The outcome of one roll, carrying everything needed to audit the number."""

    request: RollRequest
    feat_dice: tuple[FeatValue, ...]
    kept_feat: FeatValue
    success_dice: tuple[SuccessDie, ...]
    total: int
    outcome: Outcome
    degree: Degree
    icons: int
    auto: AutoKind | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def magnitude(self, base: int = 1) -> int:
        """Pattern P3: ``base``, plus one per Success icon. Zero if the roll failed.

        This is "reduce/increase by 1, plus 1 per Success icon" — Shadow Test reduction,
        First Aid injury days, end-of-journey Fatigue reduction, marching distance, the
        Short Cut. Writing ``1 + result.icons`` anywhere is duplicating this.
        """
        return (base + self.icons) if self.succeeded else 0

    def tier(self) -> int:
        """Pattern P4: 0 on failure, 1 on bare success, 2 with one icon, 3 with two or more.

        Intimidate Foe, Rally Comrades, Protect Companion, Prepare Shot.
        """
        if not self.succeeded:
            return 0
        if self.icons == 0:
            return 1
        return 2 if self.icons == 1 else 3

    def feat_numeric_value(self, shift: int = 0) -> FeatValue:
        """The kept Feat face, for table lookup and the Piercing Blow check.

        ``shift`` applies to numeric faces only; an icon face passes through untouched, so
        spending Pierce on a roll that already shows the rune changes nothing (``08.6``).

        Deliberately **unclamped**, unlike :meth:`tor.tables.LookupTable.lookup`: a Feat 8
        with a +3 Pierce is an effective 11, and it needs to read as 11 to clear a
        threshold of 10. Table lookups clamp because tables have no row for 11; the
        Piercing Blow check does not, because it is a comparison.
        """
        if isinstance(self.kept_feat, FeatFace):
            return self.kept_feat
        return self.kept_feat + shift


def _roll_feat_dice(req: RollRequest, rng: Randomness) -> tuple[tuple[FeatValue, ...], FeatValue]:
    """Roll one or two Feat dice per policy and select the kept one.

    Both dice are retained for display. Selection uses the allegiance-relative ordering,
    so Favoured keeps the maximum and Ill-favoured the minimum under it.
    """
    policy = req.policy
    if policy is FeatDicePolicy.NORMAL:
        single = rng.feat()
        return (single,), single

    rolled = (rng.feat(), rng.feat())
    chooser = max if policy is FeatDicePolicy.FAVOURED else min
    kept = chooser(rolled, key=lambda v: feat_ordering_key(v, inverted=req.icon_inverted))
    return rolled, kept


def _decide_outcome(
    req: RollRequest, kept: FeatValue, total: int
) -> tuple[Outcome, AutoKind | None]:
    """The outcome precedence of ``02.3.4``, in its stated strict order.

    Rule 2 outranks rule 3: a Miserable hero who rolls the auto-success face succeeds. The
    two cannot co-occur — they key off opposite faces of the same die — so the ordering is
    defensive rather than load-bearing, but it is stated so no implementer invents a
    conflict.
    """
    if req.magical_success:  # 1
        return Outcome.SUCCESS, AutoKind.MAGICAL
    if kept == auto_success_face(inverted=req.icon_inverted):  # 2
        return Outcome.SUCCESS, AutoKind.AUTO_SUCCESS
    if req.eye_is_auto_failure and kept == auto_failure_face(inverted=req.icon_inverted):  # 3
        return Outcome.FAILURE, AutoKind.AUTO_FAILURE
    if req.target_number is None:  # 4
        return Outcome.NO_TN, None
    if total >= req.target_number:  # 5
        return Outcome.SUCCESS, None
    return Outcome.FAILURE, None  # 6


def _degree_for(outcome: Outcome, icons: int) -> Degree:
    if outcome is not Outcome.SUCCESS:
        return Degree.NONE
    if icons == 0:
        return Degree.SUCCESS
    return Degree.GREAT_SUCCESS if icons == 1 else Degree.EXTRAORDINARY_SUCCESS


def resolve(req: RollRequest, rng: Randomness) -> RollResult:
    """Resolve one roll. The single entry point for every die in the game."""
    feat_dice, kept = _roll_feat_dice(req, rng)
    success_dice = tuple(rng.success() for _ in range(req.dice_count))

    # Icons are unaffected by Weary: 6 is a solid face, so a Weary hero still scores on it.
    icons = sum(1 for die in success_dice if die.has_icon)

    feat_contribution = feat_numeric(kept, inverted=req.icon_inverted)
    success_contribution = sum(
        0 if (req.weary and die.is_outlined) else die.face for die in success_dice
    )
    total = feat_contribution + success_contribution

    outcome, auto = _decide_outcome(req, kept, total)

    return RollResult(
        request=req,
        feat_dice=feat_dice,
        kept_feat=kept,
        success_dice=success_dice,
        total=total,
        outcome=outcome,
        degree=_degree_for(outcome, icons),
        icons=icons,
        auto=auto,
    )


@dataclass(slots=True)
class IconBudget:
    """Success icons available to spend on special results (``02.3.6``).

    Icons have two distinct uses that must not be conflated. **Degree of success** is
    passive — reading how well the roll went. **Spending** is active — each icon buys one
    special result (Skill Special Successes, combat Special Damage).

    Spending does not reduce the numeric total and does not lower the degree. A great
    success remains a great success after its icon is spent.
    """

    available: int
    spent: list[str] = field(default_factory=list)

    @classmethod
    def from_result(cls, result: RollResult) -> IconBudget:
        return cls(available=result.icons)

    @property
    def remaining(self) -> int:
        return self.available - len(self.spent)

    def spend(self, option_id: str, count: int = 1) -> None:
        """Spend ``count`` icons on ``option_id``. Repeats are legal."""
        if count < 1:
            raise ValueError(f"an icon spend must be at least 1, got {count}")
        if count > self.remaining:
            raise RuleViolation(
                f"cannot spend {count} icon(s) on {option_id!r}: only {self.remaining} left "
                f"of {self.available}",
                rule_reference="icon_spending",
            )
        self.spent.extend([option_id] * count)

    def count_of(self, option_id: str) -> int:
        return self.spent.count(option_id)


@dataclass(slots=True)
class AttemptLedger:
    """Which abilities have already been tried against a task (``02.3.9``).

    The default is one attempt per action. A failed Skill roll may be reattempted by the
    same hero only with a *different* Skill, representing a different approach, and only
    with the Loremaster's permission — which is an input, not something this enforces.

    Scoped to a task, not to the session; discarded when the task resolves.
    """

    entries: dict[TaskId, set[AbilityId]] = field(default_factory=dict)

    def may_attempt(self, task: TaskId, ability: AbilityId) -> bool:
        return ability not in self.entries.get(task, frozenset())

    def record(self, task: TaskId, ability: AbilityId) -> None:
        self.entries.setdefault(task, set()).add(ability)

    def clear(self, task: TaskId) -> None:
        self.entries.pop(task, None)


def policy_sources(policy: FeatDicePolicy, source: str = "policy") -> dict[str, Sequence[str]]:
    """Build the source tuples for a policy chosen directly rather than by effects.

    Table rolls whose Feat die policy comes from a region tier (``10.4.2``) or a loss level
    (``16.4.1``) have no effect to name, but must still go through the same field so
    :attr:`RollRequest.policy` stays the only place the policy is decided.
    """
    if policy is FeatDicePolicy.FAVOURED:
        return {"favoured_sources": (source,), "ill_favoured_sources": ()}
    if policy is FeatDicePolicy.ILL_FAVOURED:
        return {"favoured_sources": (), "ill_favoured_sources": (source,)}
    return {"favoured_sources": (), "ill_favoured_sources": ()}


# ----------------------------------------------------------------------------------
# Building a request from a character (02.3.1)
# ----------------------------------------------------------------------------------


#: Hope spent for bonus dice yields +1d, or +2d while Inspired (``02.3.3``).
HOPE_DICE = 1
INSPIRED_HOPE_DICE = 2
#: A supporter grants +1d, or +2d when the acting hero is their Fellowship Focus.
SUPPORT_DICE = 1
FOCUSED_SUPPORT_DICE = 2


class RollingCharacter(Protocol):
    """The narrow view :func:`build_request` needs (``01.5``).

    Implemented by ``Hero``. This structural typing is why one code path serves heroes and
    adversaries alike despite their very different sheets, and why the Feat-die icon
    inversion is a flag rather than a parallel implementation — an adversary that has no
    Skills passes ``rating=`` explicitly instead.

    ``01.5`` sketches four members: ``ability_rating``, ``target_number``, ``conditions``
    and ``effects``. This is the one :func:`build_request` actually consults; the other
    three arrive as explicit parameters (``target_number``, ``weary``,
    ``eye_is_auto_failure``, ``bus``), which is the stronger form of the same doctrine —
    a subsystem is handed narrow, explicit dependencies rather than reaching through the
    actor for them. ``effects`` in particular is unsatisfiable rather than merely unused:
    an ``EffectBus`` field on ``Hero`` would make ``tor.model`` import ``tor.effects``,
    which imports ``tor.model.derive`` — a runtime import cycle, not just a breach of
    ``01.1``. The bus reaches a rule through ``tor.rules.context.RulesContext`` instead.
    """

    def rating(self, ability: AbilityId) -> int: ...


class SupportInput(Protocol):
    """One companion helping with a roll.

    The supporter must hold rank 1 or better in a Skill the Loremaster deems appropriate;
    the engine validates the rank, the appropriateness is an input.
    """

    @property
    def rating(self) -> int: ...

    @property
    def is_focus(self) -> bool: ...

    @property
    def approved(self) -> bool: ...


def build_request(
    character: RollingCharacter,
    ability: AbilityId | None,
    *,
    bus: EffectBus,
    target_number: int | None = None,
    purpose: RollPurpose = RollPurpose.GENERIC,
    rating: int | None = None,
    spend_hope: bool = False,
    support: SupportInput | None = None,
    useful_item: bool = False,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
    weary: bool = False,
    eye_is_auto_failure: bool = False,
    icon_inverted: bool = False,
    magical_success: bool = False,
    scene: SceneKind | None = None,
    environment: Environment = DEFAULT_ENVIRONMENT,
    target: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> RollRequest:
    """Assemble a request, folding in every effect that modifies the roll."""
    ctx = HookContext(
        hook=Hook.MODIFY_ROLL_REQUEST,
        actor=character,
        target=target,
        scene=scene,
        environment=environment,
        ability=ability,
        purpose=str(purpose),
        extra=dict(extra or {}),
    )

    if magical_success and not _magical_success_allowed(bus, ctx):
        raise RuleViolation(
            f"no effect grants a magical success for {ability!r}",
            rule_reference="magical_success_eligibility",
            suggestion="a cultural blessing, Cultural Virtue, or item Blessing must permit it",
        )

    base_rating = rating if rating is not None else (character.rating(ability) if ability else 0)

    favoured: list[str] = []
    ill_favoured: list[str] = []
    effect_dice = 0
    for contribution in bus.collect(Hook.MODIFY_ROLL_REQUEST, ctx):
        if isinstance(contribution, NumericContribution):
            effect_dice += contribution.delta
        elif isinstance(contribution, FlagContribution):
            if contribution.flag == "favoured":
                favoured.append(str(contribution.value))
            elif contribution.flag == "ill_favoured":
                ill_favoured.append(str(contribution.value))

    total_bonus = bonus_dice + effect_dice
    if spend_hope:
        total_bonus += INSPIRED_HOPE_DICE if _is_inspired(bus, ctx) else HOPE_DICE
    if support is not None:
        total_bonus += _support_dice(support)
    if useful_item:
        # +1d on a non-combat roll, and only one item may benefit a given roll (03.5.2).
        total_bonus += 1

    return RollRequest(
        ability=ability,
        rating=base_rating,
        target_number=target_number,
        favoured_sources=tuple(favoured),
        ill_favoured_sources=tuple(ill_favoured),
        bonus_dice=total_bonus,
        penalty_dice=penalty_dice,
        weary=weary,
        eye_is_auto_failure=eye_is_auto_failure,
        icon_inverted=icon_inverted,
        magical_success=magical_success,
        purpose=purpose,
    )


def _support_dice(support: SupportInput) -> int:
    """A supporter's contribution. The Focus bonus **replaces** the ordinary one."""
    if not support.approved:
        raise RuleViolation(
            "support needs the Loremaster's approval that the circumstances allow it",
            rule_reference="support_approval",
        )
    if support.rating < 1:
        raise RuleViolation(
            "a supporter needs at least rank 1 in an appropriate Skill",
            rule_reference="support_rank",
        )
    return FOCUSED_SUPPORT_DICE if support.is_focus else SUPPORT_DICE


def _is_inspired(bus: EffectBus, ctx: HookContext) -> bool:
    inspiration_ctx = HookContext(
        hook=Hook.MODIFY_INSPIRATION,
        actor=ctx.actor,
        target=ctx.target,
        scene=ctx.scene,
        environment=ctx.environment,
        ability=ctx.ability,
        purpose=ctx.purpose,
        extra=ctx.extra,
    )
    return any(
        isinstance(c, FlagContribution) and c.flag == "inspired"
        for c in bus.collect(Hook.MODIFY_INSPIRATION, inspiration_ctx)
    )


def _magical_success_allowed(bus: EffectBus, ctx: HookContext) -> bool:
    eligibility_ctx = HookContext(
        hook=Hook.MAGICAL_SUCCESS_ELIGIBLE,
        actor=ctx.actor,
        target=ctx.target,
        scene=ctx.scene,
        environment=ctx.environment,
        ability=ctx.ability,
        purpose=ctx.purpose,
        extra=ctx.extra,
    )
    return any(
        isinstance(c, FlagContribution) and c.flag == "magical_success_eligible"
        for c in bus.collect(Hook.MAGICAL_SUCCESS_ELIGIBLE, eligibility_ctx)
    )
