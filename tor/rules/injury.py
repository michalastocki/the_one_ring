"""Risk, the consequences of failure, and sources of injury (``16``).

A **shared leaf** (``01.1``): every subsystem may depend on it. Almost everything here
returns an Outcome and leaves the applying to the caller, normally by handing
:func:`loss_delta` to ``resources.change_endurance``.

:func:`wound_hero` is the exception, and the reason this module depends on
:mod:`tor.rules.resources` after all. ``01.1`` names injury as the place a subsystem goes to
inflict a Wound — "when journey needs to inflict a Wound, it calls ``tor.rules.injury`` (a
shared L4 leaf)" — and checking the Wounded box drops a Dying hero's Endurance to zero and
can flip Weary, both of which only ``resources`` may write. So this one function mutates and
emits ``WOUND_RECEIVED``, against the grain of the rest of the module and of ``01.4``,
because the alternative is combat, journey and the sources of injury each keeping their own
copy of ``08.8`` — which ``04.7`` forbids outright. The shared-leaves contract already
orders injury above resources for exactly this kind of need.

Two systems meet in this module, and ``16.4.2`` is the reason they share one:

* **Risk** grades what a *failed roll* costs — a simple failure, a success bought with woe,
  a failure with woe, or a disaster (``16.2``).
* **Sources of injury** grade harm that arrives without an adversary — a fall, a fire,
  frigid water, poison (``16.4``).

:data:`LOSS_FOR_SHAPE` is the bridge between them.

Apart from :func:`wound_hero` nothing here mutates state, and this module fires no hooks
and defines no event kinds of its own.

**Not here yet.** ``apply_injury`` — the full ``16.4.3`` flow, where a source's cadence
drives repeated rolls and its ``zero_endurance`` override replaces ``07.2``'s default
unconsciousness — and ``16.4.4``'s fatal circumstance, where a Wound or the Dying condition
becomes death outright with no HEALING window, both still wait. The fatal rule needs a way
to say a hero is dead, and the model has none: ``03`` gives :class:`~tor.model.hero.Hero`
``dying`` and no counterpart, so inventing one is a model change rather than an injury one.
:class:`InjuryContext` fixes the input type, and :func:`wound_hero` is the half ``01.1``
asks for now.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tor.dice import FeatFace, Randomness
from tor.errors import ContentError, RuleViolation
from tor.events import Event, EventKind
from tor.model.hero import Hero
from tor.rolls import (
    FeatDicePolicy,
    RollPurpose,
    RollRequest,
    RollResult,
    policy_sources,
    resolve,
)
from tor.rules.context import RulesContext
from tor.rules.resources import ChangeSource, change_endurance, recompute_conditions
from tor.tables import LookupTable, Sentinel

__all__ = [
    "LOSS_FOR_SHAPE",
    "LOSS_POLICY",
    "EnduranceLossOutcome",
    "FailureShape",
    "InjuryContext",
    "LossKind",
    "LossLevel",
    "PoisonOutcome",
    "PoisonState",
    "RiskDeclaration",
    "RiskLevel",
    "RiskOutcome",
    "RollAdvice",
    "daily_poison_check",
    "healing_penalty_dice",
    "loss_delta",
    "resolve_risky_roll",
    "roll_endurance_loss",
    "should_roll",
    "unscathed_face",
    "wound_hero",
]


class RiskLevel(StrEnum):
    """How much a failure costs, set by the Loremaster before the roll (``16.2``)."""

    STANDARD = "standard"
    HAZARDOUS = "hazardous"
    FOOLISH = "foolish"


class FailureShape(StrEnum):
    """The four shapes a consequence takes (``16.2.1``).

    ``WOE_OFFERED`` is the one that is not yet a consequence: at Standard risk the players
    may *choose* to succeed at a price instead of simply failing, so both branches come
    back and the caller commits to one.
    """

    SIMPLE = "simple"
    WOE_OFFERED = "woe_offered"
    FAILURE_WITH_WOE = "failure_with_woe"
    DISASTER = "disaster"


class LossLevel(StrEnum):
    """How hard a source of injury hits (``16.4.1``)."""

    MODERATE = "moderate"
    SEVERE = "severe"
    GRIEVOUS = "grievous"


class LossKind(StrEnum):
    """The three rows of the Endurance Loss table (``16.4.1``)."""

    UNSCATHED = "unscathed"
    BRUISED = "bruised"
    KNOCKED_OUT = "knocked_out"


#: ``16.4.1`` — and note the direction, which is counterintuitive and load-bearing. A
#: **Moderate** source rolls **Favoured**, so it tends toward the rune and thus toward being
#: unscathed. Getting this backwards makes minor hazards lethal.
LOSS_POLICY: Mapping[LossLevel, FeatDicePolicy] = {
    LossLevel.MODERATE: FeatDicePolicy.FAVOURED,
    LossLevel.SEVERE: FeatDicePolicy.NORMAL,
    LossLevel.GRIEVOUS: FeatDicePolicy.ILL_FAVOURED,
}

#: ``16.4.2`` — the bridge that is the whole reason Risk and injury share a module.
LOSS_FOR_SHAPE: Mapping[FailureShape, LossLevel] = {
    FailureShape.SIMPLE: LossLevel.MODERATE,
    FailureShape.WOE_OFFERED: LossLevel.MODERATE,
    FailureShape.FAILURE_WITH_WOE: LossLevel.SEVERE,
    FailureShape.DISASTER: LossLevel.GRIEVOUS,
}


@dataclass(frozen=True, slots=True)
class RiskDeclaration:
    """A Loremaster's Risk setting, and the warning that must come with it (``16.2``).

    The book requires the players be told what makes an action particularly hazardous
    *before* they commit. The engine cannot judge whether a warning was fair, but it can
    refuse to let the step be skipped silently — which is what
    :func:`resolve_risky_roll` does.
    """

    level: RiskLevel
    warning: str = ""
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class RiskOutcome:
    """A risky roll, graded (``16.2.1``)."""

    roll: RollResult
    shape: FailureShape
    #: True only when the players may choose to succeed at a price instead of failing.
    woe_available: bool = False

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded


@dataclass(frozen=True, slots=True)
class RollAdvice:
    """Advisory only (``16.1``): whether a roll is called for at all.

    ``16.1`` is explicit that this must never become a gate the engine enforces — a
    Loremaster who wants a roll gets one. It exists so a UI can prompt.
    """

    roll_called_for: bool
    reason: str


def should_roll(
    *, can_fail: bool, danger: bool = False, knowledge: bool = False, manipulation: bool = False
) -> RollAdvice:
    """``16.1``'s guidance, as advice.

    A roll is called for only if the action might fail **and** falls into one of three
    cases: it is dangerous, it seeks information not immediately available, or it aims to
    influence uncooperative characters. If the description leaves no doubt about the
    outcome, the action simply succeeds.
    """
    if not can_fail:
        return RollAdvice(False, "the description leaves no doubt; the action simply succeeds")
    if danger or knowledge or manipulation:
        cases = [
            name
            for name, present in (
                ("danger", danger),
                ("knowledge", knowledge),
                ("manipulation", manipulation),
            )
            if present
        ]
        return RollAdvice(True, f"the action turns on {', '.join(cases)}")
    return RollAdvice(False, "the hero risks nothing by failing and seeks nothing hidden")


def resolve_risky_roll(
    request: RollRequest, declaration: RiskDeclaration, rng: Randomness
) -> RiskOutcome:
    """Make a roll and grade its failure by the declared Risk (``16.2``).

    A level above Standard without a warning the players acknowledged is a
    :class:`RuleViolation`. That is the one thing the engine can enforce about fairness.

    A success is a success at any Risk level — Risk grades the *failure*. At Standard, a
    failure comes back as ``WOE_OFFERED`` with both branches available, because ``16.2.1``
    makes Success with Woe a player's choice rather than an outcome the engine picks.
    """
    if declaration.level is not RiskLevel.STANDARD and not (
        declaration.warning and declaration.acknowledged
    ):
        raise RuleViolation(
            f"a {declaration.level} roll needs a warning the players acknowledged",
            rule_reference="risk_warning_required",
            suggestion="describe what makes the action hazardous, and record the "
            "players' acknowledgement, before rolling",
        )

    roll = resolve(request, rng)
    if roll.succeeded:
        return RiskOutcome(roll=roll, shape=FailureShape.SIMPLE)

    if declaration.level is RiskLevel.STANDARD:
        return RiskOutcome(roll=roll, shape=FailureShape.WOE_OFFERED, woe_available=True)
    if declaration.level is RiskLevel.HAZARDOUS:
        return RiskOutcome(roll=roll, shape=FailureShape.FAILURE_WITH_WOE)
    return RiskOutcome(roll=roll, shape=FailureShape.DISASTER)


@dataclass(frozen=True, slots=True)
class EnduranceLossOutcome:
    """What the Endurance Loss table said (``16.4.1``).

    ``amount`` is what a Bruised hero loses. For ``KNOCKED_OUT`` it is meaningless on its
    own — the hero drops to zero whatever they had — so use :func:`loss_delta`, which needs
    the hero's current Endurance to say how much that is.
    """

    kind: LossKind
    amount: int
    roll: RollResult


@dataclass(frozen=True, slots=True)
class InjuryContext:
    """What is hurting the hero, and how badly (``16.4``).

    ``fatal`` is a Loremaster's declaration, not a computed property: ``16.4.4`` gives it to
    circumstances that should in all likelihood prove lethal — a fall from an extreme height
    onto rock, being trapped under a burning structure, drowning in freezing water. When it
    is set, a Wound or the Dying condition becomes death outright, with no HEALING window.
    """

    source: str
    level: LossLevel
    fatal: bool = False


def roll_endurance_loss(
    level: LossLevel, rng: Randomness, *, table: LookupTable[Any]
) -> EnduranceLossOutcome:
    """Roll harm from a source of injury (``16.4.1``).

    The loss level chooses the Feat die policy — pattern P10 again — and the kept face is
    read off the Endurance Loss table. The table is **injected**: ``00-README.md`` §2 keeps
    every number the book prints out of the engine.
    """
    request = RollRequest(
        ability=None,
        rating=0,
        target_number=None,
        purpose=RollPurpose.ENDURANCE_LOSS,
        **policy_sources(LOSS_POLICY[level], source=f"loss_{level}"),  # type: ignore[arg-type]
    )
    roll = resolve(request, rng)
    row = table.lookup(roll.kept_feat)
    return _read_loss_row(row, roll, table_id=table.id)


def _read_loss_row(row: Any, roll: RollResult, *, table_id: str) -> EnduranceLossOutcome:
    """Interpret one row of the Endurance Loss table.

    ``"$roll"`` arrives as :data:`~tor.tables.Sentinel.ROLL` — the loader converts it, so
    this tests identity rather than sniffing for a magic string. ``"all"`` means the hero
    drops to zero, whatever they had; :func:`loss_delta` turns that into a number.
    """
    if not isinstance(row, Mapping):
        raise ContentError(
            f"an endurance loss row must be an object, got {row!r}", entity_id=table_id
        )
    try:
        kind = LossKind(str(row["kind"]))
    except (KeyError, ValueError) as exc:
        raise ContentError(
            f"endurance loss row {row!r} needs a 'kind' of {sorted(k.value for k in LossKind)}",
            entity_id=table_id,
        ) from exc

    loss = row.get("loss", 0)
    if loss is Sentinel.ROLL:
        amount = int(roll.feat_numeric_value())
    elif loss == "all":
        amount = 0  # meaningless without the hero; loss_delta supplies it.
    else:
        amount = int(loss)
    return EnduranceLossOutcome(kind=kind, amount=amount, roll=roll)


def loss_delta(outcome: EnduranceLossOutcome, hero_endurance: int) -> int:
    """The Endurance change to hand to ``resources.change_endurance``.

    Negative, because it is a loss. A Knocked Out hero is reduced to zero Endurance
    (``16.4.1``), which is the hero's whole current total rather than a fixed number — the
    reason this needs the hero and the table row does not.
    """
    if outcome.kind is LossKind.UNSCATHED:
        return 0
    if outcome.kind is LossKind.KNOCKED_OUT:
        return -hero_endurance
    return -outcome.amount


@dataclass(frozen=True, slots=True)
class PoisonState:
    """A poison working on a hero (``16.4.5``)."""

    level: LossLevel
    source: str = "poison"


@dataclass(frozen=True, slots=True)
class PoisonOutcome:
    """One day's progress against a poison (``16.4.5``)."""

    cured: bool
    loss: EnduranceLossOutcome | None = None


def daily_poison_check(
    poison: PoisonState, rng: Randomness, *, table: LookupTable[Any]
) -> PoisonOutcome:
    """The end-of-day roll a poisoned hero makes (``16.4.5``).

    A rune means no damage **and** no more poison. That is the natural recovery path, and it
    follows from the table's Unscathed row — but the cure is specific to poison and **must
    not be generalised**: a hero who shrugs off a fall is not thereby immune to falling.

    Takes the :class:`PoisonState` rather than the hero, deviating from ``16.4.5``'s
    signature: nothing about the hero is read, the level lives on the poison, and keeping
    the function hero-free keeps it pure.

    Two neighbouring rules belong to their own callers: a poisoned hero cannot rest, which
    reaches ``resources`` as a ``cannot_rest`` flag on the rest hooks rather than as an
    import; and a successful HEALING roll at the *start* of a day also cures, losing ``(1d)``
    if the poison is Severe and ``(2d)`` if Grievous.
    """
    outcome = roll_endurance_loss(poison.level, rng, table=table)
    if outcome.kind is LossKind.UNSCATHED:
        return PoisonOutcome(cured=True, loss=outcome)
    return PoisonOutcome(cured=False, loss=outcome)


def healing_penalty_dice(level: LossLevel) -> int:
    """Dice a HEALING roll against poison loses (``16.4.5``).

    Severe costs ``(1d)`` and Grievous ``(2d)``; a Moderate poison costs nothing.
    """
    return {LossLevel.MODERATE: 0, LossLevel.SEVERE: 1, LossLevel.GRIEVOUS: 2}[level]


def unscathed_face(*, inverted: bool = False) -> FeatFace:
    """The face that means Unscathed, for a caller scripting or explaining a roll."""
    return FeatFace.EYE if inverted else FeatFace.RUNE


# -- inflicting a Wound ------------------------------------------------------------------


def wound_hero(
    hero: Hero,
    *,
    ctx: RulesContext,
    source: ChangeSource,
    injury_days: int = 0,
    dying: bool = False,
    payload: Mapping[str, Any] | None = None,
    rolls: tuple[RollResult, ...] = (),
) -> list[Event]:
    """Mark a hero Wounded, wherever the harm came from (``08.8``).

    ``01.1`` names this module as the home: "when journey needs to inflict a Wound, it calls
    ``tor.rules.injury`` (a shared L4 leaf)". Combat, a journey's Terrible Misfortune and a
    source of injury all check the same box, and ``04.7`` forbids three copies of the rule —
    so the box is checked here and the callers differ only in what they put on the event.

    ``dying`` carries ``08.8``'s whole escalation. A Grievous first Wound and a second Wound
    reach the same place: Endurance drops to zero and the hero is Dying. The caller decides
    *which* of those happened (combat reads the severity table, a fatal fall is declared);
    this applies the consequence either way, and does it once rather than twice — a hero
    already at zero Endurance has nothing left to lose.

    ``injury_days`` only ever rises: ``07.7`` counts down a single recovery, so a second
    Severe injury while the first is still mending does not shorten it.
    """
    second = hero.conditions.wounded
    hero.conditions.wounded = True
    hero.injury_days = max(hero.injury_days, injury_days)
    hero.dying = hero.dying or dying

    events: list[Event] = []
    if dying and hero.endurance:
        events += change_endurance(hero, -hero.endurance, source, ctx=ctx)
    events += recompute_conditions(hero, ctx=ctx)
    events.append(
        Event(
            kind=EventKind.WOUND_RECEIVED,
            actor=hero.id,
            payload={
                "injury_days": hero.injury_days,
                "dying": hero.dying,
                "second": second,
                "source": str(source),
                **dict(payload or {}),
            },
            rolls=rolls,
        )
    )
    return events
