"""The shared Resistance contest (``09.1``) and opposed actions (``09.4``).

A **shared leaf** (``01.1``): every subsystem may depend on it, and it depends on nothing
else at L4.

Councils (``09.2``) and Skill Endeavours (``09.3``) are the same machine wearing different
clothes — *accumulate successful rolls matching or exceeding a Resistance, within a limited
number of attempts*. The machine is here; each adapter is a thin layer of naming over it.
``09`` puts the point sharply: if ``council.py`` exceeds about 150 lines, the engine has
been duplicated.

The single most consequential detail in both is that **each rolled Success icon counts as
an additional success** — a great success is worth 2 toward Resistance, an extraordinary
one 3 or more. That is pattern P3, and :meth:`ResistanceContest.record` gets it from
``RollResult.magnitude(1)`` rather than counting anything itself.

This module fires no hooks. ``MODIFY_COUNCIL_ATTEMPTS`` and ``MODIFY_AUDIENCE_ATTITUDE``
belong to the council adapter, which knows what an audience is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from tor.dice import Randomness
from tor.errors import StateError
from tor.model.ids import CombatantId
from tor.rolls import RollRequest, RollResult, resolve

__all__ = [
    "ContestAttempt",
    "ContestOutcome",
    "OpposedEntry",
    "OpposedResult",
    "ResistanceContest",
    "ResistanceLevel",
    "evaluate",
    "resolve_opposed",
]


class ResistanceLevel(IntEnum):
    """The 3 / 6 / 9 ladder both subsystems grade against (``09.1``, pattern P6).

    A council calls these reasonable / bold / outrageous and an endeavour calls them simple
    / laborious / daunting, but the numbers are identical, so there is one enum and each
    adapter supplies its own display names.
    """

    LOW = 3
    MEDIUM = 6
    HIGH = 9


class ContestOutcome(StrEnum):
    """How a finished contest ended (``09.1``)."""

    SUCCESS = "success"
    PARTIAL = "partial"
    TOTAL_FAILURE = "total_failure"
    DISASTER = "disaster"


@dataclass(frozen=True, slots=True)
class ContestAttempt:
    """One roll and what it was worth, kept so a UI can narrate the whole contest."""

    roll: RollResult
    successes: int


@dataclass(slots=True)
class ResistanceContest:
    """Successes accumulated against a Resistance, within an attempt budget (``09.1``).

    ``attempts_allowed`` may be ``None``: ``09.3.1`` runs an endeavour unbounded when no
    time limit applies at all, and reports elapsed effort instead. ``exhausted`` is then
    never true, and only meeting the Resistance or aborting finishes it.
    """

    resistance: int
    attempts_allowed: int | None
    successes: int = 0
    attempts_used: int = 0
    attempt_log: list[ContestAttempt] = field(default_factory=list)
    #: A council whose Introduction failed fails as a Disaster rather than a plain failure.
    botched_setup: bool = False
    aborted: bool = False
    abort_reason: str | None = None

    @property
    def met(self) -> bool:
        return self.successes >= self.resistance

    @property
    def exhausted(self) -> bool:
        return self.attempts_allowed is not None and self.attempts_used >= self.attempts_allowed

    @property
    def finished(self) -> bool:
        return self.met or self.exhausted or self.aborted

    @property
    def attempts_remaining(self) -> int | None:
        """``None`` when unbounded — an endeavour with no time limit (``09.3.1``)."""
        if self.attempts_allowed is None:
            return None
        return max(0, self.attempts_allowed - self.attempts_used)

    def record(self, roll: RollResult) -> ContestAttempt:
        """One attempt. A success is worth 1, plus one per Success icon (pattern P3).

        A failed roll still consumes an attempt and contributes nothing —
        ``RollResult.magnitude`` returns 0 on a failure regardless of icons.

        Recording into a finished contest is a :class:`StateError`, not a rule violation:
        ``01.6``'s own example of the family is resolving a round in a finished combat, and
        this is the same shape of caller bug.
        """
        if self.finished:
            raise StateError(
                "this contest is already finished; check `finished` before recording another "
                f"attempt (met={self.met}, exhausted={self.exhausted}, aborted={self.aborted})"
            )
        self.attempts_used += 1
        gained = roll.magnitude(1)
        self.successes += gained
        attempt = ContestAttempt(roll=roll, successes=gained)
        self.attempt_log.append(attempt)
        return attempt

    def add_successes(self, n: int) -> None:
        """Successes from something other than an attempt.

        ``02.3.6``'s Skill Special Success scores an extra success without costing an
        attempt, which is why this is separate from :meth:`record`.
        """
        if n < 0:
            raise StateError(f"cannot add {n} successes; use a failed attempt instead")
        self.successes += n

    def abort(self, reason: str) -> None:
        """End the contest immediately, short of its attempt budget (``09.3.2``).

        A Disaster during an endeavour means the task fails completely and **cannot be
        resumed**, which is distinct from simply running out of attempts — hence a path of
        its own rather than fast-forwarding ``attempts_used``.

        The reason is a plain string rather than injury's ``FailureShape``: ``01.1`` says a
        shared leaf may depend on nothing at L4, and the Risk model lives in
        ``tor.rules.injury``. The endeavour adapter, a subsystem, does the mapping.
        """
        if self.finished:
            raise StateError("this contest is already finished; it cannot be aborted again")
        self.aborted = True
        self.abort_reason = reason


def evaluate(contest: ResistanceContest) -> ContestOutcome:
    """Grade a finished contest (``09.1``).

    .. note::
       ``09.1``'s code block and the prose two lines beneath it disagree. The block returns
       ``DISASTER`` whenever ``successes == 0``; the text then says "``TOTAL_FAILURE`` vs
       ``DISASTER`` differs between the two adapters". Both cannot hold. We follow the
       prose, which is the more specific statement: the shared engine returns
       ``TOTAL_FAILURE`` for a scoreless contest, and the council adapter promotes it,
       because for a council "every attempt failed" *is* a Disaster (``09.2.5``). An
       endeavour that merely ran out of time is not.

    An abort or a botched setup is a Disaster at either adapter, so those stay here.
    """
    if contest.met:
        return ContestOutcome.SUCCESS
    if contest.aborted or contest.botched_setup:
        return ContestOutcome.DISASTER
    if contest.successes == 0:
        return ContestOutcome.TOTAL_FAILURE
    return ContestOutcome.PARTIAL


@dataclass(frozen=True, slots=True)
class OpposedEntry:
    """One contestant in an opposed action, with the roll they are making."""

    contestant: CombatantId
    request: RollRequest


@dataclass(frozen=True, slots=True)
class OpposedResult:
    """Who won, or why nobody did (``09.4``)."""

    winner: CombatantId | None
    rolls: Mapping[CombatantId, RollResult]
    #: ``"all_failed"`` or ``"tied_icons"`` when there is no winner, else ``None``.
    draw_reason: str | None = None


def resolve_opposed(entries: Sequence[OpposedEntry], rng: Randomness) -> OpposedResult:
    """One PC acting in direct opposition to another (``09.4``).

    Everyone rolls simultaneously, and **each against their own relevant Attribute TN** —
    which is why every entry carries its own fully-built request rather than sharing one.
    Among those who succeed, the most Success icons wins. If all fail, or the leaders tie
    on icons, there is no winner: the callers may roll again or call it a draw.

    Entries roll in the order given, which a scripted test must match.
    """
    if not entries:
        raise StateError("an opposed action needs at least one contestant")

    rolls = {entry.contestant: resolve(entry.request, rng) for entry in entries}
    succeeded = [(cid, result) for cid, result in rolls.items() if result.succeeded]
    if not succeeded:
        return OpposedResult(winner=None, rolls=rolls, draw_reason="all_failed")

    best = max(result.icons for _, result in succeeded)
    leaders = [cid for cid, result in succeeded if result.icons == best]
    if len(leaders) > 1:
        return OpposedResult(winner=None, rolls=rolls, draw_reason="tied_icons")
    return OpposedResult(winner=leaders[0], rolls=rolls)
