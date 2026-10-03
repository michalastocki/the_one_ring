"""Councils: formal gatherings with real stakes (``09.2``).

A **thin adapter** over :mod:`tor.rules.contest`, and deliberately so. ``09`` opens by
saying councils and Skill Endeavours are the same machine wearing different clothes, and
closes the point with a warning: if this module grows past about 150 lines, the engine has
been duplicated.

It has not been duplicated, but it is longer than 150 lines, so the claim is worth
checking rather than asserting. Nothing here counts a success, tracks an attempt budget or
decides when a contest is over — :class:`~tor.rules.contest.ResistanceContest` does all of
that, and the only line below that touches a running total hands it straight to
``contest.record``. What the extra length buys is the house style's documentation, the
``resolve_``/``apply_`` split ``01.4`` requires of every subsystem, and the records that
carry a council's own state: the goal, the audience and its attitude, none of which belongs
on the shared machine. Measured as the engine measures it — 181 statements against combat's
419 and journey's 442 — this is the thin layer ``09`` asks for.

**The promotion is the adapter's one real rule.** ``09.1``'s shared ``evaluate`` returns
``TOTAL_FAILURE`` for a contest that scored nothing, and says in the prose that the two
adapters differ on exactly that. For a council they do not differ by much and they differ
precisely: ``09.2.5`` has only three outcomes, and "all attempts failed" is a **Disaster** —
the Company is now seen as a threat. An endeavour that merely ran out of time is not.
:func:`end_council` is where ``TOTAL_FAILURE`` becomes ``DISASTER``, and it is the only place
this module overrides the engine.

**A council is an explicit Loremaster action** (``09.2``). Ordinary conversation uses plain
Skill rolls; the engine cannot tell the difference and does not try. Likewise the Skills:
``09.2.3`` and ``09.2.4`` list the ones characteristically used and what each costs
narratively, but :func:`resolve_introduction` and :func:`resolve_attempt` take the ability as
an input and restrict nothing. That guidance belongs in a UI.

**Failure is a player's decision, not an outcome.** ``09.2.5``'s middle row offers the
players a choice between being refused outright and achieving the goal at a price. So
:func:`end_council` returns ``PARTIAL`` with ``woe_available`` set and both branches open,
rather than picking one.

.. note::
   ``09.2.3`` says ``MODIFY_COUNCIL_ATTEMPTS`` "adds +1 to the maximum number of Skill rolls
   **a hero** may attempt in a council", while ``09.2`` gives the council a single shared
   budget and ``09.1``'s machine has exactly one. There is no per-hero cap to raise. The
   budget is the Company's, so a Virtue letting its holder attempt one more roll shows up as
   one more attempt for the Company: :func:`attempt_budget` collects the hook from every
   participant and sums, which gives two holders two extra attempts — one each, as the
   sentence reads.

**Not here.** Singing a Lay to shrug off Weariness for the length of the council is
``15``'s ``sing``; :attr:`Council.ignore_weary` is the flag it will set, and
:func:`resolve_attempt` already honours it. ``14.6``'s ``council_resistance_step`` — a
Revelation episode making the next council's goal harder — cannot reach this module as an
import, because ``14.6`` routes it through ``Campaign.pending_modifiers`` at L5 and ``01.1``
forbids a subsystem from importing the shell. It arrives as :attr:`CouncilSetup.resistance_step`
instead, which the session layer fills from that state when it calls :func:`begin_council`.
``02.3.6``'s Skill Special Successes are content the pack does not yet define — ``02.3.6``
names a ``special_successes[]`` section that ``05.1``'s file tree omits entirely — so the one
with mechanical force here, "score one additional success toward a Resistance total",
arrives as :attr:`AttemptOutcome.icons_available` and an ``extra_successes`` argument to
:func:`apply_attempt` rather than as a table lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tor.dice import Randomness
from tor.effects.hooks import (
    Hook,
    NumericContribution,
    ReplacementContribution,
    SceneKind,
)
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
    resolve,
)
from tor.rules.contest import (
    ContestOutcome,
    ResistanceContest,
    ResistanceLevel,
    evaluate,
)
from tor.rules.context import RulesContext

__all__ = [
    "ATTITUDE_DICE",
    "MAX_ROLEPLAY_BONUS",
    "RESISTANCE_FOR_GRADE",
    "AttemptOutcome",
    "AudienceAttitude",
    "Council",
    "CouncilAttempt",
    "CouncilGoal",
    "CouncilResult",
    "CouncilSetup",
    "IntroductionOutcome",
    "RequestGrade",
    "apply_attempt",
    "apply_introduction",
    "attempt_budget",
    "audience_attitude",
    "began_event",
    "begin_council",
    "end_council",
    "grade_for",
    "resolve_attempt",
    "resolve_introduction",
    "step_grade",
]

#: ``09.2.4``: the audience's attitude modifies **every** roll of the Interaction.
MAX_ROLEPLAY_BONUS = 2


class RequestGrade(StrEnum):
    """How the Loremaster grades the Company's stated goal (``09.2.2``).

    The display names for ``09.1``'s one shared ladder. An endeavour calls the same three
    numbers simple / laborious / daunting; keeping two name sets over one enum is what
    ``09.1`` asks for.
    """

    #: The audience loses nothing by helping, or is offered something of equal worth.
    REASONABLE = "reasonable"
    #: The goal profits the Company more than it profits the audience.
    BOLD = "bold"
    #: The audience is asked to do something dangerous, or with little prospect of reward.
    OUTRAGEOUS = "outrageous"


#: ``09.2.2``'s table, which is ``09.1``'s 3 / 6 / 9 ladder under council names (pattern P6).
RESISTANCE_FOR_GRADE: dict[RequestGrade, ResistanceLevel] = {
    RequestGrade.REASONABLE: ResistanceLevel.LOW,
    RequestGrade.BOLD: ResistanceLevel.MEDIUM,
    RequestGrade.OUTRAGEOUS: ResistanceLevel.HIGH,
}

#: Steepest first, so :func:`step_grade` can walk the ladder by index.
_GRADE_ORDER: tuple[RequestGrade, ...] = (
    RequestGrade.REASONABLE,
    RequestGrade.BOLD,
    RequestGrade.OUTRAGEOUS,
)


def grade_for(resistance: int) -> RequestGrade:
    """The grade a Resistance value corresponds to, for a log or a UI.

    A Resistance that is not one of the three rungs is a caller bug rather than a house
    rule: ``09.2.2`` grades the request and the ladder gives the number, never the reverse.
    """
    for grade, level in RESISTANCE_FOR_GRADE.items():
        if level == resistance:
            return grade
    raise StateError(
        f"{resistance} is not one of 09.2.2's grades "
        f"({sorted(int(level) for level in RESISTANCE_FOR_GRADE.values())})"
    )


def step_grade(grade: RequestGrade, steps: int = 1) -> RequestGrade:
    """Make the request harder, or easier (``14.6``).

    A Revelation episode's ``council_resistance_step`` op turns a reasonable request bold and
    a bold one outrageous. Clamped at both ends: there is no rung above outrageous, and the
    Eye of Mordor making an already-outrageous request worse simply leaves it outrageous.
    """
    index = _GRADE_ORDER.index(grade) + steps
    return _GRADE_ORDER[max(0, min(len(_GRADE_ORDER) - 1, index))]


class AudienceAttitude(StrEnum):
    """How the folk being petitioned receive the Company (``09.2.4``)."""

    RELUCTANT = "reluctant"
    OPEN = "open"
    FRIENDLY = "friendly"


#: ``09.2.4``'s table. Open is the default and costs nothing.
ATTITUDE_DICE: dict[AudienceAttitude, int] = {
    AudienceAttitude.RELUCTANT: -1,
    AudienceAttitude.OPEN: 0,
    AudienceAttitude.FRIENDLY: 1,
}

#: Coldest first, so a numeric contribution can step the ladder toward Friendly.
_ATTITUDE_ORDER: tuple[AudienceAttitude, ...] = (
    AudienceAttitude.RELUCTANT,
    AudienceAttitude.OPEN,
    AudienceAttitude.FRIENDLY,
)


@dataclass(frozen=True, slots=True)
class CouncilGoal:
    """What the Company wants and what it is willing to give (``09.2.2``).

    Narrative, and the engine reads neither field. It is recorded because ``09.2.2`` asks
    the players to agree on both *before* the Resistance is set, and a log that cannot say
    what was asked for cannot explain what was refused.
    """

    description: str
    offer: str = ""


@dataclass(frozen=True, slots=True)
class CouncilSetup:
    """``09.2.1`` step 1, as an input (``18.3``)."""

    goal: CouncilGoal
    grade: RequestGrade
    #: The folk being petitioned. Named so an effect can predicate on them — one Cultural
    #: Virtue makes a *specific* folk always Friendly (``04.3.6``).
    audience: str
    participants: tuple[HeroId, ...]
    attitude: AudienceAttitude = AudienceAttitude.OPEN
    #: ``14.6``: rungs the Eye of Mordor has already moved this council's goal, arriving from
    #: ``Campaign.pending_modifiers`` because ``01.1`` forbids importing the shell.
    resistance_step: int = 0

    def __post_init__(self) -> None:
        if not self.participants:
            raise RuleViolation(
                "a council needs at least one hero taking part",
                rule_reference="council_no_participants",
            )


@dataclass(slots=True)
class Council:
    """A council in progress — ``17.3``'s Scene state for ``SceneKind.COUNCIL``.

    ``18.3`` types ``begin_council`` as returning the ``ResistanceContest`` itself. It cannot
    be only that: the goal, the audience and its attitude all outlive a single attempt and
    none of them belongs on the shared machine. The contest rides here under
    :attr:`contest`, which is what the API facade returns.
    """

    goal: CouncilGoal
    grade: RequestGrade
    audience: str
    participants: tuple[HeroId, ...]
    attitude: AudienceAttitude
    contest: ResistanceContest
    spokesperson: HeroId | None = None
    introduction: RollResult | None = None
    #: Set by ``15``'s ``sing`` when the Company sings a Lay: the heroes ignore the effects
    #: of being Weary for the length of the venture.
    ignore_weary: bool = False
    #: Rungs ``14.6`` moved the goal before it was set, kept for the log.
    resistance_step: int = 0

    @property
    def resistance(self) -> int:
        return self.contest.resistance

    @property
    def begun(self) -> bool:
        """``09.2.1``: the Interaction follows the Introduction, which sets the budget."""
        return self.introduction is not None


def begin_council(setup: CouncilSetup, *, ctx: RulesContext) -> Council:
    """``09.2.1`` step 1 — grade the request and open the scene (``09.2.2``).

    The attempt budget is deliberately left at zero until the Introduction sets it: ``09.2.3``
    makes the spokesperson's roll the thing that decides it, and a council that has not been
    introduced has no attempts to spend. :func:`resolve_attempt` refuses to run before then.

    Takes ``ctx`` for the audience attitude, which an effect may override before the first
    word is spoken (``09.2.4``).
    """
    grade = step_grade(setup.grade, setup.resistance_step)
    council = Council(
        goal=setup.goal,
        grade=grade,
        audience=setup.audience,
        participants=setup.participants,
        attitude=setup.attitude,
        contest=ResistanceContest(resistance=int(RESISTANCE_FOR_GRADE[grade]), attempts_allowed=0),
        resistance_step=setup.resistance_step,
    )
    council.attitude = audience_attitude(council, ctx=ctx)
    return council


def began_event(council: Council) -> Event:
    """The log entry for a council that has just been opened."""
    return Event(
        kind=EventKind.COUNCIL_BEGAN,
        payload={
            "goal": council.goal.description,
            "offer": council.goal.offer,
            "audience": council.audience,
            "grade": str(council.grade),
            "resistance": council.resistance,
            "resistance_step": council.resistance_step,
            "attitude": str(council.attitude),
            "participants": [str(h) for h in council.participants],
        },
    )


def audience_attitude(council: Council, *, ctx: RulesContext) -> AudienceAttitude:
    """The attitude every Interaction roll is modified by (``09.2.4``).

    A Loremaster's input, overridable by effects through ``MODIFY_AUDIENCE_ATTITUDE``. Two
    shapes are read, because ``04.3.6``'s examples want the first and the ladder is free:

    * a **replacement** naming an attitude sets it outright — "Dwarves always Friendly";
    * a **numeric** delta steps it along the ladder, clamped at both ends, so an effect can
      warm a reluctant audience without promising a friendly one, or cool a warm one.

    Collected from every participant, because the Virtue belongs to a hero while the
    attitude belongs to the room: if anyone present is a Dwarf-friend, the Dwarves are
    friendly. The deltas sum and the warmest replacement wins outright, both of which are
    order-free — ``17.4``'s replay guarantee is byte-for-byte, so an answer that depended on
    which hero was listed first would not survive a reload. A guarantee beats a nudge: an
    effect promising "always Friendly" means always.
    """
    step = 0
    replacements: list[AudienceAttitude] = []
    for hero_id in council.participants:
        # The audience twice, as every other subsystem carries a route twice:
        # build_predicate's {"flag": ...} form tests a key's truthiness, while matching a
        # value needs the value.
        named: dict[str, Any] = {
            "audience": council.audience,
            f"audience_{council.audience}": True,
            "attitude": str(council.attitude),
        }
        hook_ctx = ctx.hook_context(Hook.MODIFY_AUDIENCE_ATTITUDE, hero_id, **named)
        contributions = ctx.bus(hero_id).collect(Hook.MODIFY_AUDIENCE_ATTITUDE, hook_ctx)
        if not contributions:
            continue
        for contribution in contributions:
            if isinstance(contribution, ReplacementContribution):
                replacements.append(AudienceAttitude(str(contribution.value)))
            elif isinstance(contribution, NumericContribution):
                step += contribution.delta
        ctx.consume(hero_id, contributions, hook_ctx)

    if replacements:
        return max(replacements, key=_ATTITUDE_ORDER.index)
    index = _ATTITUDE_ORDER.index(council.attitude) + step
    return _ATTITUDE_ORDER[max(0, min(len(_ATTITUDE_ORDER) - 1, index))]


def attempt_budget(council: Council, roll: RollResult, *, ctx: RulesContext) -> int:
    """How many Skill rolls the Interaction gets (``09.2.3``).

    A successful Introduction buys ``Resistance + 1 per Success icon``; a failed one buys
    ``Resistance`` flat. It is pattern P3's shape with the base surviving the failure, which
    is why it is written out rather than taken from ``RollResult.magnitude`` — ``magnitude``
    returns zero on a failure, and here the Company still gets its Resistance in attempts.

    ``MODIFY_COUNCIL_ATTEMPTS`` then raises it, collected from every participant; see this
    module's note on why the Company-wide budget is the thing a per-hero Virtue raises.
    """
    budget = council.resistance + (roll.icons if roll.succeeded else 0)
    for hero_id in council.participants:
        hook_ctx = ctx.hook_context(
            Hook.MODIFY_COUNCIL_ATTEMPTS, hero_id, audience=council.audience
        )
        contributions = ctx.bus(hero_id).collect(Hook.MODIFY_COUNCIL_ATTEMPTS, hook_ctx)
        budget += sum(c.delta for c in contributions if isinstance(c, NumericContribution))
        ctx.consume(hero_id, contributions, hook_ctx)
    return max(0, budget)


@dataclass(frozen=True, slots=True)
class CouncilAttempt:
    """What a hero brings to one council roll — Introduction or Interaction alike.

    One record for both, because ``09.2.3`` and ``09.2.4`` differ in what the roll *buys*
    and never in how it is made. A second near-identical parameter list is exactly the
    near-copy ``01.2`` exists to prevent.

    ``roleplay_bonus`` is ``09.2.4``'s explicitly mechanised reward for a speech that touches
    what the audience cares about: the Loremaster may grant ``+1d`` or ``+2d``. The book is
    emphatic that a good die roll and a clever decision deserve equal weight, so it is a
    first-class input rather than something folded into ``bonus_dice`` and lost.
    """

    hero: HeroId
    ability: AbilityId
    roleplay_bonus: int = 0
    spend_hope: bool = False
    support: SupportInput | None = None
    bonus_dice: int = 0
    penalty_dice: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.roleplay_bonus <= MAX_ROLEPLAY_BONUS:
            raise RuleViolation(
                f"a roleplaying bonus is 0 to {MAX_ROLEPLAY_BONUS} dice, got {self.roleplay_bonus}",
                rule_reference="council_roleplay_bonus",
                suggestion="09.2.4 lets the Loremaster grant +1d or even +2d, and no more",
            )


@dataclass(frozen=True, slots=True)
class IntroductionOutcome:
    """The spokesperson's one roll, and what it bought (``09.2.3``)."""

    spokesperson: HeroId
    ability: AbilityId
    roll: RollResult
    attempts: int
    #: ``09.2.5``: a failed Introduction means a later shortfall is a Disaster, not a refusal.
    botched: bool

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded


def resolve_introduction(
    council: Council,
    spokesperson: Hero,
    attempt: CouncilAttempt,
    rng: Randomness,
    *,
    ctx: RulesContext,
) -> IntroductionOutcome:
    """``09.2.1`` step 2 — one spokesperson, one roll (``09.2.3``).

    The ability is an input and nothing restricts it: ``09.2.3`` lists AWE, COURTESY and
    RIDDLE as characteristic and describes what each costs the Company narratively, which is
    guidance for a UI rather than a rule the engine can enforce.

    Takes the same :class:`CouncilAttempt` the Interaction does. The audience's attitude and
    the roleplaying bonus apply here as they do to every other roll of the council, and the
    Introduction's only distinction is what its result buys.
    """
    if council.begun:
        raise StateError("this council has already been introduced; 09.2.3 allows one roll")
    _check_participant(council, spokesperson, attempt)
    roll = _council_roll(council, spokesperson, attempt, rng, ctx=ctx, step="introduction")
    return IntroductionOutcome(
        spokesperson=spokesperson.id,
        ability=attempt.ability,
        roll=roll,
        attempts=attempt_budget(council, roll, ctx=ctx),
        botched=not roll.succeeded,
    )


def apply_introduction(council: Council, outcome: IntroductionOutcome) -> list[Event]:
    """Commit the Introduction: the budget is set and the Interaction may begin."""
    if council.begun:
        raise StateError("this council has already been introduced")
    council.spokesperson = outcome.spokesperson
    council.introduction = outcome.roll
    council.contest.attempts_allowed = outcome.attempts
    council.contest.botched_setup = outcome.botched
    return [
        Event(
            kind=EventKind.COUNCIL_INTRODUCTION,
            actor=outcome.spokesperson,
            payload={
                "ability": str(outcome.ability),
                "succeeded": outcome.succeeded,
                "icons": outcome.roll.icons,
                "attempts": outcome.attempts,
                "botched": outcome.botched,
                "audience": council.audience,
            },
            rolls=(outcome.roll,),
        )
    ]


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    """One Interaction roll, decided but not yet counted (``09.2.4``)."""

    attempt: CouncilAttempt
    roll: RollResult
    #: What :meth:`ResistanceContest.record` will add: 1 plus one per icon, or 0 (pattern P3).
    successes: int
    #: Icons the hero may spend on a Skill Special Success (``02.3.6``). Spending them does
    #: not reduce :attr:`successes` — ``02.3.6`` is explicit that a spent icon still counts
    #: toward the degree.
    icons_available: int

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded


def resolve_attempt(
    council: Council,
    hero: Hero,
    attempt: CouncilAttempt,
    rng: Randomness,
    *,
    ctx: RulesContext,
) -> AttemptOutcome:
    """``09.2.1`` step 3 — one Skill roll of the Interaction (``09.2.4``).

    Different Skills may be used, and the same Skill repeatedly: ``09.2.4`` lists ENHEARTEN,
    INSIGHT, PERSUADE, RIDDLE and SONG as characteristic and the engine restricts none of
    them.

    Nothing is counted here. The successes are computed the way the contest will count them
    so a UI can show the player what the roll was worth before committing, and
    :func:`apply_attempt` is what actually records it.
    """
    if not council.begun:
        raise StateError(
            "this council has not been introduced; 09.2.1 puts the Introduction before the "
            "Interaction, and it is what sets the attempt budget"
        )
    if council.contest.finished:
        raise StateError(
            "this council is already finished; call end_council rather than another attempt"
        )
    _check_participant(council, hero, attempt)
    roll = _council_roll(council, hero, attempt, rng, ctx=ctx, step="interaction")
    return AttemptOutcome(
        attempt=attempt,
        roll=roll,
        successes=roll.magnitude(1),
        icons_available=roll.icons,
    )


def apply_attempt(
    council: Council, outcome: AttemptOutcome, *, extra_successes: int = 0
) -> list[Event]:
    """Count one attempt against the Resistance (``09.1``).

    ``extra_successes`` is ``02.3.6``'s Skill Special Success that "scores one additional
    success toward a Resistance total", bought with Success icons the roll actually showed.
    It goes through :meth:`ResistanceContest.add_successes`, which costs no attempt — the
    icon was already paid for by the roll.
    """
    if extra_successes < 0 or extra_successes > outcome.icons_available:
        raise RuleViolation(
            f"cannot spend {extra_successes} icons for extra successes; the roll showed "
            f"{outcome.icons_available}",
            rule_reference="council_icon_spend",
        )
    council.contest.record(outcome.roll)
    if extra_successes:
        council.contest.add_successes(extra_successes)
    return [
        Event(
            kind=EventKind.COUNCIL_ATTEMPT,
            actor=outcome.attempt.hero,
            payload={
                "ability": str(outcome.attempt.ability),
                "succeeded": outcome.succeeded,
                "successes": outcome.successes + extra_successes,
                "icons_spent": extra_successes,
                "roleplay_bonus": outcome.attempt.roleplay_bonus,
                "total": council.contest.successes,
                "resistance": council.resistance,
                "attempts_used": council.contest.attempts_used,
                "attempts_remaining": council.contest.attempts_remaining,
                "finished": council.contest.finished,
            },
            rolls=(outcome.roll,),
        )
    ]


@dataclass(frozen=True, slots=True)
class CouncilResult:
    """How the council ended (``09.2.5``)."""

    outcome: ContestOutcome
    successes: int
    resistance: int
    attempts_used: int
    #: True only on ``PARTIAL``: the players may take the goal at a price instead of being
    #: refused. Which of the two happens is their decision, not the engine's.
    woe_available: bool
    events: tuple[Event, ...] = field(default_factory=tuple)


def end_council(council: Council) -> CouncilResult:
    """``09.2.1`` step 4 — grade the council (``09.2.5``).

    Three outcomes, not four. The shared engine's ``TOTAL_FAILURE`` — every attempt failed —
    is promoted to ``DISASTER`` here, because ``09.2.5`` gives a council no row for it: a
    Company that scored nothing is now seen as a threat, and may be imprisoned or attacked.
    That promotion is the whole reason ``09.1``'s prose says the two adapters differ on
    ``TOTAL_FAILURE`` versus ``DISASTER``, and the only rule in this module the engine does
    not already own.

    A shortfall after a botched Introduction is also a Disaster, but that one the engine
    already knows: ``botched_setup`` is a field on the contest and ``evaluate`` reads it.
    """
    outcome = evaluate(council.contest)
    if outcome is ContestOutcome.TOTAL_FAILURE:
        outcome = ContestOutcome.DISASTER
    woe_available = outcome is ContestOutcome.PARTIAL
    event = Event(
        kind=EventKind.COUNCIL_ENDED,
        payload={
            "goal": council.goal.description,
            "audience": council.audience,
            "grade": str(council.grade),
            "outcome": str(outcome),
            "successes": council.contest.successes,
            "resistance": council.resistance,
            "attempts_used": council.contest.attempts_used,
            "botched_introduction": council.contest.botched_setup,
            "woe_available": woe_available,
        },
    )
    return CouncilResult(
        outcome=outcome,
        successes=council.contest.successes,
        resistance=council.resistance,
        attempts_used=council.contest.attempts_used,
        woe_available=woe_available,
        events=(event,),
    )


def _check_participant(council: Council, hero: Hero, attempt: CouncilAttempt) -> None:
    """The two ways a caller can hand the wrong hero to a council roll.

    A mismatch between the record and the aggregate is a wiring bug and a
    :class:`StateError`; a hero who is simply not at the council is an illegal move and a
    :class:`RuleViolation` (``01.6``).
    """
    if hero.id != attempt.hero:
        raise StateError(f"attempt names {attempt.hero!r} but was handed {hero.id!r}")
    if hero.id not in council.participants:
        raise RuleViolation(
            f"{hero.id!r} is not taking part in this council",
            rule_reference="council_not_participating",
        )


def _council_roll(
    council: Council,
    hero: Hero,
    attempt: CouncilAttempt,
    rng: Randomness,
    *,
    ctx: RulesContext,
    step: str,
) -> RollResult:
    """Every roll a council makes, Introduction and Interaction alike.

    One function because ``09.2.3`` and ``09.2.4`` differ in what the roll *buys*, never in
    how it is made: the same attitude modifier, the same roleplaying bonus, the same
    ``MODIFY_COUNCIL_ROLL``. That hook goes through ``build_request``'s ``extra_hooks``,
    unlike journey's, because the Ill-omen curse it exists for is a plain ``-1d`` on the
    dice pool (``04.3.6``) and needs no reinterpretation.
    """
    attitude_dice = ATTITUDE_DICE[council.attitude]
    return resolve(
        build_request(
            hero,
            attempt.ability,
            bus=ctx.bus(hero.id),
            target_number=attribute_tn(
                hero.attributes.score(ability_attribute(attempt.ability)),
                short_campaign=ctx.short_campaign,
            ),
            purpose=RollPurpose.COUNCIL,
            spend_hope=attempt.spend_hope,
            support=attempt.support,
            bonus_dice=attempt.bonus_dice + attempt.roleplay_bonus + max(0, attitude_dice),
            penalty_dice=attempt.penalty_dice + max(0, -attitude_dice),
            # 15's `sing` is what sets ignore_weary; this is where a Lay earns its roll.
            weary=hero.conditions.weary and not council.ignore_weary,
            eye_is_auto_failure=hero.conditions.miserable,
            scene=ctx.scene if ctx.scene is not None else SceneKind.COUNCIL,
            environment=ctx.environment,
            extra={
                "council_step": step,
                f"council_{step}": True,
                "audience": council.audience,
                f"audience_{council.audience}": True,
                "attitude": str(council.attitude),
                "grade": str(council.grade),
            },
            extra_hooks=(Hook.MODIFY_COUNCIL_ROLL,),
        ),
        rng,
    )
