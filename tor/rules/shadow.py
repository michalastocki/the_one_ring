"""The Shadow: gaining it, resisting it, shedding it, and succumbing to it (``11``).

A **shared leaf** (``01.1``): combat, journey, council, treasure and the Eye subsystem all
call into it. It depends on one other leaf, :mod:`tor.rules.resources`, and on nothing else
at L4 — because ``tor.model.conditions.ConditionSet`` reserves the Miserable flag to
``resources.recompute_conditions``, and every function here moves Shadow, which is half of
what Miserable is derived from. ``11.6`` says so in its own sketch: "the hero is no longer
Ill-favoured; recompute conditions".

Three things are worth reading before the code.

**The resisting ability is a property of the source** (``11.1``), so no caller supplies it.
:data:`RESISTED_WITH` is the whole mapping, and the two sources it maps to ``None`` are the
two ``11.3`` says permit no test at all — a Misdeed and harm to your Fellowship Focus.
Supplying a test for one of those raises rather than being ignored, because silent ignoring
hides caller bugs.

**Being Ill-favoured at maximum Shadow is an effect, not a branch** (``11.2``).
:data:`OVERBURDENED` is registered once, permanently, by
:func:`register_shadow_conditions` at hero construction, and its predicate does the work —
that way no code path can forget to attach it, and no code path has to remember to detach
it either.

**Shadow Scars are ordinary Shadow points that happen to be permanent** (``11.5``). They
count toward Miserable and toward the maximum like any other, they are the floor that
:func:`remove_shadow` and :func:`bout_of_madness` stop at, and only :func:`heal_scar`
removes one.

.. note::
   ``11.5``'s code block sets ``hero.shadow = 1`` when the will is hardened, but the note
   three lines below it says "Shadow becomes exactly the scar count, which is at least 1".
   Both cannot hold for a hero who has hardened before: the block would drop a hero with
   two existing scars to Shadow 1 while giving them a third, breaching invariant I6.
   :func:`harden_will` follows the note.

**Not here.** The phase-end retirement of a hero who never took their bout of madness
(``11.6``) is a clock boundary and belongs to ``17.3``; :func:`check_succumb` and
:class:`DepartureKind` fix the types it will need. The Heal Scars undertaking's Yule
gating and its 5 Adventure points belong to ``15.6``; :func:`heal_scar` is the mechanical
half. Registering the Flaw a bout of madness grants needs the pack that defines it, so
:class:`MadnessOutcome` reports it and the caller registers it, exactly as creation does
for every other granted effect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tor.dice import Randomness
from tor.effects.bus import Effect, EffectBus, EffectKind, EffectSource
from tor.effects.hooks import FlagContribution, Hook, HookContext
from tor.errors import RuleViolation, StateError
from tor.events import Event, EventKind
from tor.model.abilities import VALOUR, WISDOM, ability_attribute
from tor.model.company import Company
from tor.model.hero import SHADOW_PATH_STEPS, Hero
from tor.model.ids import AbilityId, EffectId, HeroId
from tor.rolls import RollPurpose, RollResult, attribute_tn, build_request, resolve
from tor.rules.context import RulesContext
from tor.rules.resources import ChangeSource, change_hope, recompute_conditions
from tor.tables import LookupTable

__all__ = [
    "OVERBURDENED",
    "OVERBURDENED_ID",
    "RESISTED_WITH",
    "DepartureKind",
    "MadnessOutcome",
    "MisdeedCost",
    "RemovalReason",
    "ShadowGainOutcome",
    "ShadowSource",
    "ShadowTestInput",
    "apply_shadow_test",
    "bout_of_madness",
    "check_succumb",
    "gain_shadow",
    "graded_dread",
    "harden_will",
    "harm_to_focus",
    "heal_scar",
    "preview_misdeed",
    "register_shadow_conditions",
    "remove_shadow",
    "resisting_ability",
    "resolve_shadow_test",
    "shadow_path_flaw",
]

#: ``11.6``: harm to a hero gives everyone who named them as their Focus 1 unresistible
#: Shadow. One point, always — the amount is not graded.
FOCUS_HARM_SHADOW = 1


class ShadowSource(StrEnum):
    """Where the Shadow came from (``11.1``). The source decides how it is resisted."""

    DREAD = "dread"
    SORCERY = "sorcery"
    GREED = "greed"
    MISDEED = "misdeed"
    #: Harm to the hero's own Fellowship Focus (``03.6``, ``11.6``).
    FOCUS = "focus"
    OTHER = "other"


#: The ability that resists each source (``11.1``). ``None`` means no test is permitted.
#:
#: This is the whole mapping and the single place it is written: ``11.1`` is explicit that
#: the resisting ability is a property of the source rather than a parameter, so a caller
#: that thinks it knows better is a caller with a bug.
RESISTED_WITH: Mapping[ShadowSource, AbilityId | None] = {
    ShadowSource.DREAD: VALOUR,
    ShadowSource.SORCERY: WISDOM,
    ShadowSource.GREED: WISDOM,
    ShadowSource.MISDEED: None,
    ShadowSource.FOCUS: None,
    ShadowSource.OTHER: None,
}


def resisting_ability(source: ShadowSource) -> AbilityId | None:
    """VALOUR, WISDOM, or ``None`` where ``11.3`` permits no test."""
    return RESISTED_WITH[source]


class RemovalReason(StrEnum):
    """Why Shadow was removed. ``11.1`` names the type; ``20`` fixes no registry, so these
    ids are engine-coined and follow ``20.11``."""

    FELLOWSHIP_PHASE = "fellowship_phase"
    EFFECT = "effect"
    LOREMASTER = "loremaster"


class DepartureKind(StrEnum):
    """How a hero who succumbs leaves the story (``11.8``).

    Purely narrative — the mechanical result is identical — but it is recorded on the
    retirement event so a UI narrates the right ending. Men, Hobbits and Dwarves succumb to
    madness; Elves are overpowered by the burden and seek the Uttermost West.
    """

    MADNESS = "madness"
    SAILS_WEST = "sails_west"


# -- being overburdened --------------------------------------------------------------

OVERBURDENED_ID = EffectId("overburdened_by_shadow")


def _shadow_at_maximum(actor: Any) -> bool:
    """``shadow`` has reached maximum Hope.

    Written defensively because an ``EffectBus`` dispatches for adversaries too, and an
    ``AdversaryInstance`` has neither field. Compared with ``>=`` rather than ``11.2``'s
    ``==``: invariant I5 already caps Shadow at maximum Hope, so the two agree, and ``>=``
    does not quietly stop working if a future path ever oversteps.
    """
    shadow = getattr(actor, "shadow", None)
    maximum = getattr(actor, "max_hope", None)
    return shadow is not None and maximum is not None and shadow >= maximum.value


def _overburdened(ctx: HookContext) -> FlagContribution:
    return FlagContribution(source=OVERBURDENED_ID, flag="ill_favoured", value=str(OVERBURDENED_ID))


#: Ill-favoured on **every** roll while Shadow stands at maximum Hope (``11.2``).
#:
#: Engine-owned rather than content, and registered permanently rather than on the
#: threshold crossing: ``11.2`` asks for exactly that, so that no code path can forget to
#: attach it. The predicate is what turns it on and off.
OVERBURDENED = Effect(
    id=OVERBURDENED_ID,
    kind=EffectKind.CONDITION,
    listeners={Hook.MODIFY_ROLL_REQUEST: _overburdened},
    predicate=lambda ctx: _shadow_at_maximum(ctx.actor),
)


def register_shadow_conditions(bus: EffectBus) -> None:
    """Attach the always-on Shadow conditions to a hero's bus (``11.2``).

    Called once, at hero construction. Idempotent in the sense that matters: registering
    the same effect from the same source twice is a ``RuleViolation``, so a second call is
    a caller bug rather than a silent duplicate contribution.
    """
    bus.register(OVERBURDENED, EffectSource.condition(str(OVERBURDENED_ID)))


# -- the Shadow Test -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShadowTestInput:
    """What the players bring to a Shadow Test (``11.3``).

    Everything the *hero* brings — the rank, the Target Number, Weary, Miserable, and every
    effect listening on ``MODIFY_ROLL_REQUEST`` or ``MODIFY_SHADOW_TEST`` — is read from the
    hero and the bus, so none of it appears here.

    ``reparation_allowed`` is ``11.4.4``'s single exception to the no-test rule: a Misdeed
    committed unknowingly, met with contrition and an earnest attempt to put things right,
    permits a WISDOM test. It is spelled explicitly so the exception can never be reached by
    accident.
    """

    reparation_allowed: bool = False
    #: Pushing the test with a point of Hope, for the +1d of pattern P8 — or +2d while
    #: Inspired, which ``build_request`` resolves. :func:`gain_shadow` spends the point
    #: before the dice are rolled, so a hero at zero Hope is refused rather than rolling.
    #: :func:`resolve_shadow_test` only adds the die, exactly as ``build_request`` does;
    #: a caller reaching for it directly owns the spend.
    spend_hope: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0


def _test_ability(source: ShadowSource, test: ShadowTestInput) -> AbilityId:
    """The ability this test rolls, refusing a test the source does not permit."""
    if source is ShadowSource.MISDEED and test.reparation_allowed:
        # 11.4.4 rule 2 — reparation is tested with WISDOM, never VALOUR.
        return WISDOM
    ability = resisting_ability(source)
    if ability is None:
        raise RuleViolation(
            f"Shadow from {source.value!r} cannot be resisted, so no Shadow Test may be made",
            rule_reference="shadow_test_not_permitted",
            suggestion=(
                "a Misdeed committed unknowingly and met with reparations is the one "
                "exception; set reparation_allowed on the test"
                if source is ShadowSource.MISDEED
                else None
            ),
        )
    return ability


def resolve_shadow_test(
    hero: Hero,
    source: ShadowSource,
    rng: Randomness,
    *,
    ctx: RulesContext,
    test: ShadowTestInput | None = None,
) -> RollResult:
    """Roll the VALOUR or WISDOM test that resists a source of Shadow (``11.3``).

    A normal roll: VALOUR against the HEART TN, WISDOM against the WITS TN, Weary and
    Miserable applied like any other roll. It additionally folds ``MODIFY_SHADOW_TEST``,
    which is where the Cultural Blessings, Cultural Virtues and Patron benefits that shape
    these tests live (``04.3.5``) — a +1d against Sorcery says so on that hook rather than
    matching a purpose string.

    ``hero.rating`` knows nothing of VALOUR and WISDOM — they are ranks, not abilities on
    the 18-Skill grid (``03.3``) — so the rank is passed explicitly.
    """
    test = test or ShadowTestInput()
    ability = _test_ability(source, test)
    rank = hero.valour if ability == VALOUR else hero.wisdom
    attribute = ability_attribute(ability)
    request = build_request(
        hero,
        ability,
        bus=ctx.bus(hero.id),
        rating=rank,
        target_number=attribute_tn(
            hero.attributes.score(attribute), short_campaign=ctx.short_campaign
        ),
        purpose=RollPurpose.SHADOW_TEST,
        spend_hope=test.spend_hope,
        bonus_dice=test.bonus_dice,
        penalty_dice=test.penalty_dice,
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=ctx.environment,
        # Twice, because build_predicate's {"flag": ...} form tests a key's truthiness
        # while bonus_dice's "source" narrowing compares a value — the same split
        # resources.change_hope makes for recovery routes.
        extra={"source": str(source), f"source_{source.value}": True},
        extra_hooks=(Hook.MODIFY_SHADOW_TEST,),
    )
    return resolve(request, rng)


def apply_shadow_test(points: int, roll: RollResult) -> int:
    """The points actually gained after a test (``11.3``).

    A passed test reduces the gain by 1, plus 1 per Success icon — pattern P3, which
    ``RollResult.magnitude`` owns. A failed roll reduces nothing, because ``magnitude``
    returns 0 on a failure whatever its icons.
    """
    return max(0, points - roll.magnitude(1))


# -- gaining Shadow ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShadowGainOutcome:
    """What one gain came to (``11.1``).

    ``11.1`` types :func:`gain_shadow` as returning this rather than ``list[Event]``, so
    unlike the rest of ``tor.rules`` the events ride on the outcome. ``discarded`` is the
    part that hit the ceiling and was thrown away rather than banked (invariant I5).
    """

    source: ShadowSource
    requested: int
    gained: int
    discarded: int
    scar_gained: bool
    roll: RollResult | None
    events: tuple[Event, ...]
    #: ``shadow`` now stands at maximum Hope — the hero is Ill-favoured on everything, and
    #: owes a bout of madness before the Adventuring Phase ends (``11.6``).
    at_maximum: bool
    #: All four Flaws already taken, and Shadow at maximum: the hero is out of play
    #: (``11.8``).
    succumbed: bool


def gain_shadow(
    hero: Hero,
    points: int,
    source: ShadowSource,
    *,
    ctx: RulesContext,
    test: ShadowTestInput | None = None,
    rng: Randomness | None = None,
    add_scar: bool = False,
) -> ShadowGainOutcome:
    """Gain Shadow, optionally resisted by a Shadow Test (``11.1``, ``11.3``).

    The order matters and is fixed here: the test reduces the raw points first (``11.3``
    grades the *source*), then ``MODIFY_SHADOW_GAIN`` adjusts what would actually be gained,
    then the ceiling of ``11.2`` discards the excess. An effect on that hook may not push
    the gain below zero.

    ``add_scar`` is the top row of ``11.4.4``'s Misdeed table — "4 **plus** 1 Shadow Scar".
    Read as written: the scar is an additional permanent point on top of the graded ones,
    not one of them. It is still subject to the same ceiling.

    Passing a ``test`` for a source ``11.3`` says permits none raises: silent ignoring
    hides caller bugs.
    """
    if points < 0:
        raise StateError(f"cannot gain {points} Shadow; use remove_shadow to shed it")

    events: list[Event] = []
    roll: RollResult | None = None
    if test is not None:
        # Refuse an impermissible test before a point of Hope is spent on it.
        _test_ability(source, test)
        if rng is None:
            raise StateError("a Shadow Test needs a Randomness to roll with")
        if test.spend_hope:
            events += change_hope(hero, -1, ChangeSource.HOPE_SPEND, ctx=ctx)
        roll = resolve_shadow_test(hero, source, rng, ctx=ctx, test=test)
        gained = apply_shadow_test(points, roll)
    else:
        gained = points

    hook_ctx = ctx.hook_context(
        Hook.MODIFY_SHADOW_GAIN, hero, source=str(source), **{f"source_{source.value}": True}
    )
    gained = max(0, ctx.bus(hero.id).apply_numeric(Hook.MODIFY_SHADOW_GAIN, hook_ctx, gained).value)
    ctx.consume(hero.id, ctx.bus(hero.id).collect(Hook.MODIFY_SHADOW_GAIN, hook_ctx), hook_ctx)

    wanted = gained + (1 if add_scar else 0)
    ceiling = hero.max_hope.value
    applied = max(0, min(wanted, ceiling - hero.shadow))
    hero.shadow += applied
    # A scar is a Shadow point, so one that the ceiling discarded was never gained.
    scar_gained = add_scar and applied == wanted
    if scar_gained:
        hero.shadow_scars += 1

    events.append(
        Event(
            kind=EventKind.SHADOW_GAINED,
            actor=hero.id,
            payload={
                "source": str(source),
                "requested": points,
                "gained": applied,
                "discarded": wanted - applied,
                "tested": roll is not None,
                "shadow": hero.shadow,
            },
            rolls=() if roll is None else (roll,),
        )
    )
    if scar_gained:
        events.append(
            Event(
                kind=EventKind.SHADOW_SCAR_GAINED,
                actor=hero.id,
                payload={"source": str(source), "scars": hero.shadow_scars},
            )
        )
    events += recompute_conditions(hero, ctx=ctx)

    return ShadowGainOutcome(
        source=source,
        requested=points,
        gained=applied,
        discarded=wanted - applied,
        scar_gained=scar_gained,
        roll=roll,
        events=tuple(events),
        at_maximum=hero.shadow >= ceiling,
        succumbed=check_succumb(hero),
    )


def harm_to_focus(
    company: Company, harmed: HeroId, heroes: Mapping[HeroId, Hero], *, ctx: RulesContext
) -> list[Event]:
    """Everyone who named ``harmed`` as their Fellowship Focus gains 1 Shadow (``03.6``).

    Unresistible, and one point regardless of what happened: a Wound, a bout of madness, or
    any other serious harm (``11.3``, ``11.6``). Holders are visited in sorted order —
    ``17.4``'s replay guarantee is byte-for-byte, so iteration order is never left to a
    dict.
    """
    events: list[Event] = []
    for holder in sorted(company.heroes_focused_on(harmed)):
        outcome = gain_shadow(
            heroes[holder], FOCUS_HARM_SHADOW, ShadowSource.FOCUS, ctx=ctx, test=None
        )
        events += outcome.events
    return events


# -- shedding it -------------------------------------------------------------------------


def _shadow_floor(hero: Hero, ctx: RulesContext) -> int:
    """The lowest Shadow this hero can be reduced to.

    The Scars, always (``11.5``: they are permanent and only *Heal Scars* removes one), and
    whatever ``MODIFY_SHADOW_FLOOR`` raises that to — ``13.8``'s Shadow Taint curse sets a
    floor on the score rather than inflicting a one-off gain. Never above the ceiling, and
    never below zero.
    """
    hook_ctx = ctx.hook_context(Hook.MODIFY_SHADOW_FLOOR, hero)
    floor = ctx.bus(hero.id).apply_numeric(Hook.MODIFY_SHADOW_FLOOR, hook_ctx, hero.shadow_scars)
    return max(0, min(floor.value, hero.max_hope.value))


def remove_shadow(
    hero: Hero, points: int, reason: RemovalReason, *, ctx: RulesContext
) -> list[Event]:
    """Shed Shadow, down to the Scars (``11.9``).

    ``points`` is the Loremaster's figure — how noteworthy the Company's deeds were is a
    judgement the engine does not make. ``MODIFY_SHADOW_REMOVAL_CAP`` then restricts it, and
    **after** the figure rather than before, which is ``11.9``'s one procedural instruction.
    The hook may only restrict: a contribution that would raise the figure above what the
    Loremaster allowed is clamped away, because the hook is a cap.
    """
    if points < 0:
        raise StateError(f"cannot remove {points} Shadow; use gain_shadow to add it")

    hook_ctx = ctx.hook_context(Hook.MODIFY_SHADOW_REMOVAL_CAP, hero, reason=str(reason))
    capped = ctx.bus(hero.id).apply_numeric(Hook.MODIFY_SHADOW_REMOVAL_CAP, hook_ctx, points)
    allowed = max(0, min(points, capped.value))

    floor = _shadow_floor(hero, ctx)
    before = hero.shadow
    hero.shadow = max(floor, hero.shadow - allowed)
    removed = before - hero.shadow

    events = [
        Event(
            kind=EventKind.SHADOW_REMOVED,
            actor=hero.id,
            payload={
                "reason": str(reason),
                "requested": points,
                "allowed": allowed,
                "removed": removed,
                "shadow": hero.shadow,
            },
        )
    ]
    events += recompute_conditions(hero, ctx=ctx)
    return events


def harden_will(hero: Hero, *, ctx: RulesContext) -> list[Event]:
    """Trade all current Shadow for one more permanent Scar (``11.5``).

    Only available while Shadow **does not yet match** maximum Hope — a hero already at the
    maximum has one way out and it is a bout of madness.

    Shadow afterwards is exactly the scar count, never zero and never 1 for a hero who has
    hardened before. See this module's note on ``11.5``'s two readings.
    """
    if hero.shadow >= hero.max_hope.value:
        raise RuleViolation(
            f"hardening the will needs Shadow below maximum Hope, and {hero.id} is at "
            f"{hero.shadow} of {hero.max_hope.value}",
            rule_reference="harden_will_threshold",
            suggestion="a hero at maximum Shadow suffers a bout of madness instead",
        )
    before = hero.shadow
    hero.shadow_scars += 1
    hero.shadow = max(hero.shadow_scars, _shadow_floor(hero, ctx))

    events = [
        Event(
            kind=EventKind.WILL_HARDENED,
            actor=hero.id,
            payload={"shadow_before": before, "shadow": hero.shadow, "scars": hero.shadow_scars},
        ),
        Event(
            kind=EventKind.SHADOW_SCAR_GAINED,
            actor=hero.id,
            payload={"source": "harden_will", "scars": hero.shadow_scars},
        ),
    ]
    events += recompute_conditions(hero, ctx=ctx)
    return events


def heal_scar(hero: Hero, *, ctx: RulesContext) -> list[Event]:
    """Remove exactly one Shadow Scar (``11.5``, ``15.6``).

    A Scar is a Shadow point, so healing one sheds that point too. The Yule restriction and
    the 5 Adventure points are the undertaking's, not this function's.
    """
    if hero.shadow_scars == 0:
        raise RuleViolation(
            f"{hero.id} has no Shadow Scars to heal",
            rule_reference="heal_scar_requires_a_scar",
        )
    hero.shadow_scars -= 1
    hero.shadow = max(hero.shadow - 1, _shadow_floor(hero, ctx))

    events = [
        Event(
            kind=EventKind.SHADOW_SCAR_HEALED,
            actor=hero.id,
            payload={"scars": hero.shadow_scars, "shadow": hero.shadow},
        )
    ]
    events += recompute_conditions(hero, ctx=ctx)
    return events


# -- bouts of madness and succumbing -------------------------------------------------------


def shadow_path_flaw(steps: Sequence[EffectId], step: int) -> EffectId:
    """The Flaw a Shadow Path grants at ``step`` (``11.7``), counted from 1.

    Paths are content (``shadow_paths.json``) and always have four ordered steps, so this
    is injected rather than looked up — the same shape ``tor.rules.injury`` uses for the
    Endurance Loss table, and what keeps this leaf free of ``tor.content``.
    """
    if not 1 <= step <= len(steps):
        raise StateError(f"a Shadow Path has {len(steps)} steps, so there is no step {step}")
    return steps[step - 1]


@dataclass(frozen=True, slots=True)
class MadnessOutcome:
    """A bout of madness and what it left behind (``11.6``).

    ``flaw`` is reported rather than registered: building the effect needs the pack that
    declares it, and the caller that owns the pack is the one that registers every other
    granted effect. ``focus_holders`` names the heroes who each owe 1 unresistible Shadow —
    :func:`harm_to_focus` applies it once the caller has the Company to hand.
    """

    description: str
    flaw: EffectId
    step: int
    shadow: int
    events: tuple[Event, ...]
    #: The bout used the hero's last step, so the next time Shadow reaches maximum Hope
    #: they are taken out of play rather than becoming Ill-favoured (``11.8``).
    at_final_step: bool


def bout_of_madness(
    hero: Hero,
    description: str,
    *,
    path_steps: Sequence[EffectId],
    ctx: RulesContext,
) -> MadnessOutcome:
    """Lose control, shed the Shadow, and take the next Flaw of the path (``11.6``).

    The only way out for a hero whose Shadow has reached maximum Hope. Current Shadow goes;
    the Scars stay, because they are permanent. The hero recovers control quickly
    afterwards, and is no longer Ill-favoured — which is why this ends by recomputing the
    conditions like everything else here.

    ``description`` is the player's, and required: ``11.6`` makes describing the bout the
    substance of it.
    """
    if not description.strip():
        raise RuleViolation(
            "a bout of madness is described by the player before it takes effect",
            rule_reference="bout_of_madness_description",
        )
    if hero.shadow_path_step >= SHADOW_PATH_STEPS:
        raise RuleViolation(
            f"{hero.id} has taken all {SHADOW_PATH_STEPS} steps of their Shadow Path and "
            "has no further bout to suffer",
            rule_reference="shadow_path_exhausted",
            suggestion="such a hero is taken out of play instead; see check_succumb",
        )

    step = hero.shadow_path_step + 1
    flaw = shadow_path_flaw(path_steps, step)
    hero.shadow = max(hero.shadow_scars, _shadow_floor(hero, ctx))
    hero.shadow_path_step = step
    hero.flaws.append(flaw)

    events = [
        Event(
            kind=EventKind.BOUT_OF_MADNESS,
            actor=hero.id,
            payload={"description": description, "step": step, "shadow": hero.shadow},
        ),
        Event(
            kind=EventKind.FLAW_GAINED,
            actor=hero.id,
            payload={"flaw": str(flaw), "step": step, "temporary": False},
        ),
    ]
    events += recompute_conditions(hero, ctx=ctx)

    return MadnessOutcome(
        description=description,
        flaw=flaw,
        step=step,
        shadow=hero.shadow,
        events=tuple(events),
        at_final_step=step >= SHADOW_PATH_STEPS,
    )


def check_succumb(hero: Hero) -> bool:
    """All four Flaws taken and Shadow at maximum Hope: the hero is out of play (``11.8``).

    ``Hero.permanent_flaw_count`` is the count that matters — a Flaw inflicted temporarily
    by the Curse of Weakness (``13.8``) is held apart and does not count toward succumbing
    (``11.7``).
    """
    return hero.permanent_flaw_count >= SHADOW_PATH_STEPS and hero.shadow >= hero.max_hope.value


# -- grading a source ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MisdeedCost:
    """What a Misdeed costs, before it is committed (``11.4.3``).

    Exposed so a UI can surface the price first: ``11.4.3`` says the Loremaster should
    normally warn players before they commit one, and a warning nobody can compute is no
    warning at all.
    """

    points: int
    scar: bool


def preview_misdeed(grade: int, table: LookupTable[Any]) -> MisdeedCost:
    """Read one row of ``misdeeds.json`` (``11.4.4``).

    ``grade`` is the Loremaster's reading of how bad the act is, 1 to 6, not a die roll —
    the table is graded, not rolled. Attempting a despicable act costs the same as
    committing it (``11.4.4`` rule 1), so nothing here asks whether it succeeded.
    """
    row = table.lookup(grade)
    return MisdeedCost(points=int(row["points"]), scar=bool(row.get("scar", False)))


def graded_dread(grade: int, table: LookupTable[Any]) -> int:
    """Read one row of ``sources_of_dread.json`` (``11.4.1``). Graded, not rolled."""
    return int(table.lookup(grade)["points"])
