"""Skill Endeavours: a complex task as a series of smaller feats (``09.3``, ``16.3``).

The second thin adapter over :mod:`tor.rules.contest`, and the reason that leaf exists as a
leaf. A council and an endeavour are the same machine — *accumulate successes to meet a
Resistance within a limited number of attempts* — and differ in three places only:

* **Where the budget comes from.** A council's Introduction roll sets it (``09.2.3``); an
  endeavour's is set by the time available (``09.3.1``), or is absent altogether.
* **What a failed roll costs.** A council's failure costs an attempt and nothing else. An
  endeavour grades each failed roll by Risk (``16.3``) — a delay, a woe, or a Disaster that
  ends the task on the spot and cannot be resumed.
* **What scoring nothing means.** For a council it is a Disaster; for an endeavour that
  simply ran out of time it is only a total failure. :func:`end_endeavour` therefore
  leaves the shared engine's ``TOTAL_FAILURE`` exactly as it found it — the mirror image of
  the one rule ``tor.rules.council`` adds.

**Risk is injury's, not this module's.** Every roll goes through
``injury.resolve_risky_roll``, which owns the warning that must precede a Hazardous or
Foolish roll and the grading of a failure into a :class:`~tor.rules.injury.FailureShape`.
This module maps exactly one of those shapes onto the contest: a ``DISASTER`` aborts it,
which ``16.3`` asks for in as many words ("wire the third to
``ResistanceContest.abort()``"). That is the mapping ``contest.abort`` was left taking a
plain string for — a shared leaf may not depend on the Risk model, so the subsystem does.

**Abandonment is not a Disaster.** ``09.3.2`` lists two ways an endeavour fails: the
Company abandons it, or time runs out. Neither is a Disaster, so :func:`abandon` does *not*
call ``contest.abort`` — that would turn a Company choosing to walk away into the grievous
outcome reserved for a roll that went catastrophically wrong.

.. note::
   ``16.3``'s table grades the three ways a roll can *stay* failed inside an endeavour, and
   says nothing of Success with Woe. ``16.2.1`` offers that choice at Standard risk on any
   roll, and an endeavour roll is no exception: :func:`apply_roll` takes ``take_woe`` and
   records the attempt as a bare success. Bare, because a failed roll's Success icons never
   counted toward its degree and a woe does not retroactively make them count.

**Not here.** A woe is *narrative* until the Loremaster says otherwise. ``16.4.2`` says a
woe "may cause" a loss of Endurance and grades it — moderate for a failure or a Success with
Woe, severe for a Failure with Woe, grievous for a Disaster — and :class:`RollOutcome`
reports that :attr:`~RollOutcome.loss_level`. Rolling the loss is ``injury.roll_endurance_loss``
and applying it is ``resources.change_endurance``; the engine never assumes a smoky room
burns someone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tor.dice import Randomness
from tor.effects.hooks import SceneKind
from tor.errors import RuleViolation, StateError
from tor.events import Event, EventKind
from tor.model.abilities import ability_attribute
from tor.model.hero import Hero
from tor.model.ids import AbilityId, HeroId
from tor.rolls import (
    RollPurpose,
    RollResult,
    SupportInput,
    attribute_tn,
    build_request,
)
from tor.rules.contest import (
    ContestOutcome,
    ResistanceContest,
    ResistanceLevel,
    evaluate,
)
from tor.rules.context import RulesContext
from tor.rules.injury import (
    LOSS_FOR_SHAPE,
    FailureShape,
    LossLevel,
    RiskDeclaration,
    RiskLevel,
    resolve_risky_roll,
)

__all__ = [
    "EXTRA_ATTEMPTS",
    "RESISTANCE_FOR_GOAL",
    "Endeavour",
    "EndeavourGoal",
    "EndeavourResult",
    "EndeavourRoll",
    "EndeavourSetup",
    "RollOutcome",
    "TimeLimit",
    "abandon",
    "apply_roll",
    "attempts_for",
    "began_event",
    "begin_endeavour",
    "end_endeavour",
    "resolve_roll",
]

#: ``16.3``'s third row, wired as ``16.3`` asks: the reason a Disaster gives the contest.
DISASTER_REASON = "disaster"


class EndeavourGoal(StrEnum):
    """How hard the task is (``09.3.1``) — ``09.1``'s one ladder under endeavour names."""

    #: A lengthy but manageable effort.
    SIMPLE = "simple"
    #: Difficult and time-consuming.
    LABORIOUS = "laborious"
    #: Hard and complicated.
    DAUNTING = "daunting"


#: Pattern P6: the same 3 / 6 / 9 a council grades its requests on.
RESISTANCE_FOR_GOAL: dict[EndeavourGoal, ResistanceLevel] = {
    EndeavourGoal.SIMPLE: ResistanceLevel.LOW,
    EndeavourGoal.LABORIOUS: ResistanceLevel.MEDIUM,
    EndeavourGoal.DAUNTING: ResistanceLevel.HIGH,
}


class TimeLimit(StrEnum):
    """How much time the Company has (``09.3.1``).

    Whether an hour is short or plenty depends entirely on the task — an hour to search one
    room is plenty, an hour to search a castle is short — so this is the Loremaster's call,
    never a computed one. ``UNLIMITED`` is the fourth case ``09.3.1`` describes in prose:
    no time limit at all, and an unbounded contest that reports elapsed effort instead.
    """

    SHORT = "short"
    ENOUGH = "enough"
    PLENTY = "plenty"
    UNLIMITED = "unlimited"


#: ``09.3.1``'s table: attempts beyond the Resistance that each time limit allows.
EXTRA_ATTEMPTS: dict[TimeLimit, int] = {
    TimeLimit.SHORT: 0,
    TimeLimit.ENOUGH: 1,
    TimeLimit.PLENTY: 2,
}


def attempts_for(goal: EndeavourGoal, limit: TimeLimit) -> int | None:
    """The attempt budget (``09.3.1``) — ``None`` when there is no time limit at all."""
    if limit is TimeLimit.UNLIMITED:
        return None
    return int(RESISTANCE_FOR_GOAL[goal]) + EXTRA_ATTEMPTS[limit]


@dataclass(frozen=True, slots=True)
class EndeavourSetup:
    """``09.3.1``'s steps 1 and 2, as an input (``18.3``)."""

    task: str
    goal: EndeavourGoal
    time_limit: TimeLimit
    participants: tuple[HeroId, ...]
    #: How often a roll may be made, in minutes — ``09.3.1``'s ``roll_interval``. The
    #: Loremaster adjudicates it ("perhaps twice a day while tracking across country,
    #: perhaps once an hour while scaling a cliff"), and the clock (``17.3``) counts in
    #: days and minutes. Required for an unlimited endeavour, which reports elapsed effort
    #: instead of attempts remaining and has nothing else to report it in.
    roll_interval_minutes: int | None = None

    def __post_init__(self) -> None:
        if not self.participants:
            raise RuleViolation(
                "an endeavour needs at least one hero working at it",
                rule_reference="endeavour_no_participants",
            )
        if self.time_limit is TimeLimit.UNLIMITED and self.roll_interval_minutes is None:
            raise RuleViolation(
                "an endeavour with no time limit needs a roll interval to report its "
                "elapsed effort in",
                rule_reference="endeavour_roll_interval",
                suggestion="09.3.1: the Loremaster decides how often rolls may be made",
            )
        if self.roll_interval_minutes is not None and self.roll_interval_minutes <= 0:
            raise RuleViolation(
                f"a roll interval must be positive, got {self.roll_interval_minutes}",
                rule_reference="endeavour_roll_interval",
            )


@dataclass(slots=True)
class Endeavour:
    """An endeavour in progress — ``17.3``'s Scene state for ``SceneKind.ENDEAVOUR``.

    ``18.3`` types ``begin_endeavour`` as returning the ``ResistanceContest`` itself, as it
    does ``begin_council``, and for the same reason it cannot be only that: the task, the
    time limit and whether the Company walked away all outlive an attempt. The contest rides
    under :attr:`contest`.
    """

    task: str
    goal: EndeavourGoal
    time_limit: TimeLimit
    participants: tuple[HeroId, ...]
    contest: ResistanceContest
    roll_interval_minutes: int | None = None
    abandoned: bool = False
    abandon_reason: str | None = None

    @property
    def resistance(self) -> int:
        return self.contest.resistance

    @property
    def finished(self) -> bool:
        """Met, out of time, ruined by a Disaster, or given up on (``09.3.2``)."""
        return self.contest.finished or self.abandoned

    @property
    def elapsed_minutes(self) -> int | None:
        """Effort spent so far, for the timeline (``09.3.1``) — ``None`` with no interval."""
        if self.roll_interval_minutes is None:
            return None
        return self.contest.attempts_used * self.roll_interval_minutes


def begin_endeavour(setup: EndeavourSetup) -> Endeavour:
    """``09.3.1`` steps 1 and 2 — set the Resistance and the time limit.

    Unlike a council, nothing here can be modified by an effect: ``04.3`` lists no hook for
    an endeavour's budget, and the budget is a function of the time the Loremaster says is
    available. So this takes no context.
    """
    return Endeavour(
        task=setup.task,
        goal=setup.goal,
        time_limit=setup.time_limit,
        participants=setup.participants,
        contest=ResistanceContest(
            resistance=int(RESISTANCE_FOR_GOAL[setup.goal]),
            attempts_allowed=attempts_for(setup.goal, setup.time_limit),
        ),
        roll_interval_minutes=setup.roll_interval_minutes,
    )


def began_event(endeavour: Endeavour) -> Event:
    """The log entry for an endeavour that has just been set."""
    return Event(
        kind=EventKind.ENDEAVOUR_BEGAN,
        payload={
            "task": endeavour.task,
            "goal": str(endeavour.goal),
            "resistance": endeavour.resistance,
            "time_limit": str(endeavour.time_limit),
            "attempts_allowed": endeavour.contest.attempts_allowed,
            "roll_interval_minutes": endeavour.roll_interval_minutes,
            "participants": [str(h) for h in endeavour.participants],
        },
    )


@dataclass(frozen=True, slots=True)
class EndeavourRoll:
    """What a hero brings to one roll of the Execution (``09.3.1``).

    The ability is an input: different Skills may serve the same goal, and the same Skill
    may be used again and again if circumstances allow. ``risk`` defaults to Standard, which
    is what an endeavour is when the Loremaster has said nothing more.
    """

    hero: HeroId
    ability: AbilityId
    risk: RiskDeclaration = field(default_factory=lambda: RiskDeclaration(RiskLevel.STANDARD))
    spend_hope: bool = False
    support: SupportInput | None = None
    bonus_dice: int = 0
    penalty_dice: int = 0


@dataclass(frozen=True, slots=True)
class RollOutcome:
    """One roll of the Execution, graded but not yet counted (``09.3.2``, ``16.3``)."""

    attempt: EndeavourRoll
    roll: RollResult
    #: ``16.2.1``'s grading of the failure, or ``SIMPLE`` for a success.
    shape: FailureShape
    #: True only on a failed Standard roll: the players may take a Success with Woe.
    woe_available: bool
    #: What :meth:`ResistanceContest.record` will add: 1 plus one per icon, or 0.
    successes: int
    #: ``16.4.2``'s grade for any Endurance the woe or the Disaster may cost — the
    #: Loremaster rules whether it does. ``None`` when the roll succeeded outright.
    loss_level: LossLevel | None

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded

    @property
    def disaster(self) -> bool:
        return self.shape is FailureShape.DISASTER


def resolve_roll(
    endeavour: Endeavour,
    hero: Hero,
    attempt: EndeavourRoll,
    rng: Randomness,
    *,
    ctx: RulesContext,
) -> RollOutcome:
    """``09.3.1`` step 3 — one roll, graded by Risk (``16.3``).

    The roll goes through ``injury.resolve_risky_roll``, which refuses a Hazardous or
    Foolish roll the players were not warned about (``16.2``) — the one fairness check the
    engine can make. Nothing is counted here; :func:`apply_roll` does that once the players
    have decided whether to take a woe that was offered.
    """
    if endeavour.finished:
        raise StateError(
            "this endeavour is already finished; call end_endeavour rather than rolling again"
        )
    if hero.id != attempt.hero:
        raise StateError(f"attempt names {attempt.hero!r} but was handed {hero.id!r}")
    if hero.id not in endeavour.participants:
        raise RuleViolation(
            f"{hero.id!r} is not working at this endeavour",
            rule_reference="endeavour_not_participating",
        )
    request = build_request(
        hero,
        attempt.ability,
        bus=ctx.bus(hero.id),
        target_number=attribute_tn(
            hero.attributes.score(ability_attribute(attempt.ability)),
            short_campaign=ctx.short_campaign,
        ),
        purpose=RollPurpose.ENDEAVOUR,
        spend_hope=attempt.spend_hope,
        support=attempt.support,
        bonus_dice=attempt.bonus_dice,
        penalty_dice=attempt.penalty_dice,
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene if ctx.scene is not None else SceneKind.ENDEAVOUR,
        environment=ctx.environment,
        extra={
            "goal": str(endeavour.goal),
            "risk": str(attempt.risk.level),
            f"risk_{attempt.risk.level.value}": True,
        },
    )
    risky = resolve_risky_roll(request, attempt.risk, rng)
    return RollOutcome(
        attempt=attempt,
        roll=risky.roll,
        shape=risky.shape,
        woe_available=risky.woe_available,
        successes=risky.roll.magnitude(1),
        loss_level=None if risky.succeeded else LOSS_FOR_SHAPE[risky.shape],
    )


def apply_roll(
    endeavour: Endeavour,
    outcome: RollOutcome,
    *,
    take_woe: bool = False,
    extra_successes: int = 0,
) -> list[Event]:
    """Count one roll against the Resistance (``09.3.2``, ``16.3``).

    The three shapes of a failure that stays failed:

    * **Simple** — a delay. The attempt is spent and the endeavour continues.
    * **Failure with Woe** — something goes wrong; the endeavour continues regardless.
    * **Disaster** — the attempt is recorded and the contest aborted. The endeavour fails
      completely and cannot be resumed, whether or not attempts remain (``09.3.2``).

    ``take_woe`` is the players' choice ``16.2.1`` offers on a failed Standard roll: succeed,
    at a price. The attempt is still spent and counts as a single success.

    ``extra_successes`` is ``02.3.6``'s Skill Special Success, bought with icons a
    *successful* roll actually showed; it costs no attempt.
    """
    if take_woe and not outcome.woe_available:
        raise RuleViolation(
            "a Success with Woe is offered only on a failed roll at Standard risk",
            rule_reference="endeavour_woe_unavailable",
            suggestion="16.2.1: at Hazardous or Foolish risk a failure is graded, not chosen",
        )
    icons = outcome.roll.icons if outcome.succeeded else 0
    if extra_successes < 0 or extra_successes > icons:
        raise RuleViolation(
            f"cannot spend {extra_successes} icons for extra successes; the roll offers {icons}",
            rule_reference="endeavour_icon_spend",
        )

    endeavour.contest.record(outcome.roll)
    if take_woe:
        endeavour.contest.add_successes(1)
    if extra_successes:
        endeavour.contest.add_successes(extra_successes)
    if outcome.disaster:
        endeavour.contest.abort(DISASTER_REASON)

    counted = (1 if take_woe else outcome.successes) + extra_successes
    return [
        Event(
            kind=EventKind.ENDEAVOUR_ROLL,
            actor=outcome.attempt.hero,
            payload={
                "ability": str(outcome.attempt.ability),
                "risk": str(outcome.attempt.risk.level),
                "succeeded": outcome.succeeded or take_woe,
                "shape": None if outcome.succeeded else str(outcome.shape),
                "took_woe": take_woe,
                "successes": counted,
                "icons_spent": extra_successes,
                "loss_level": None if outcome.loss_level is None else str(outcome.loss_level),
                "total": endeavour.contest.successes,
                "resistance": endeavour.resistance,
                "attempts_used": endeavour.contest.attempts_used,
                "attempts_remaining": endeavour.contest.attempts_remaining,
                "elapsed_minutes": endeavour.elapsed_minutes,
                "finished": endeavour.finished,
            },
            rolls=(outcome.roll,),
        )
    ]


def abandon(endeavour: Endeavour, reason: str) -> list[Event]:
    """The Company gives up (``09.3.2``).

    A failure, not a Disaster: this marks the endeavour finished without touching
    ``contest.abort``, so :func:`end_endeavour` grades it on what the Company managed before
    walking away. A task abandoned is a task that might yet be taken up again on another
    day; one ruined by a Disaster may not.
    """
    if endeavour.finished:
        raise StateError("this endeavour is already finished; there is nothing to abandon")
    endeavour.abandoned = True
    endeavour.abandon_reason = reason
    return [
        Event(
            kind=EventKind.ENDEAVOUR_ABANDONED,
            payload={
                "task": endeavour.task,
                "reason": reason,
                "successes": endeavour.contest.successes,
                "resistance": endeavour.resistance,
                "attempts_used": endeavour.contest.attempts_used,
                "elapsed_minutes": endeavour.elapsed_minutes,
            },
        )
    ]


@dataclass(frozen=True, slots=True)
class EndeavourResult:
    """How the endeavour ended (``09.3.2``)."""

    outcome: ContestOutcome
    successes: int
    resistance: int
    attempts_used: int
    elapsed_minutes: int | None
    abandoned: bool
    #: Whether the Company could take the task up again: true after running out of time or
    #: walking away, false after a Disaster (``09.3.2``: it "cannot be resumed") and after a
    #: success, which leaves nothing to resume.
    resumable: bool
    events: tuple[Event, ...] = ()


def end_endeavour(endeavour: Endeavour) -> EndeavourResult:
    """Grade a finished endeavour (``09.3.2``).

    The shared engine's grading stands unaltered — and that is this adapter's point of
    difference from a council. Meeting the Resistance is a success; a Disaster is a
    Disaster; anything else, out of time or abandoned, is a failure, ``PARTIAL`` if the
    Company scored and ``TOTAL_FAILURE`` if it did not. A council promotes the second to a
    Disaster; an endeavour does not, because a search that turned up nothing has not made
    anyone an enemy.

    An endeavour with no time limit can only end by succeeding, by a Disaster, or by being
    abandoned, so calling this on one still running is a :class:`StateError` rather than a
    premature failure.
    """
    if not endeavour.finished:
        raise StateError(
            "this endeavour is still running; meet the Resistance, run out of time, or "
            "abandon it before grading it"
        )
    outcome = evaluate(endeavour.contest)
    resumable = outcome is not ContestOutcome.DISASTER and outcome is not ContestOutcome.SUCCESS
    event = Event(
        kind=EventKind.ENDEAVOUR_ENDED,
        payload={
            "task": endeavour.task,
            "goal": str(endeavour.goal),
            "outcome": str(outcome),
            "successes": endeavour.contest.successes,
            "resistance": endeavour.resistance,
            "attempts_used": endeavour.contest.attempts_used,
            "elapsed_minutes": endeavour.elapsed_minutes,
            "abandoned": endeavour.abandoned,
            "resumable": resumable,
        },
    )
    return EndeavourResult(
        outcome=outcome,
        successes=endeavour.contest.successes,
        resistance=endeavour.resistance,
        attempts_used=endeavour.contest.attempts_used,
        elapsed_minutes=endeavour.elapsed_minutes,
        abandoned=endeavour.abandoned,
        resumable=resumable,
        events=(event,),
    )
