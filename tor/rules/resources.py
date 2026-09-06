"""Endurance, Hope, Fatigue, Treasure, Load, conditions and resting (``07``).

A **shared leaf** (``01.1``): every subsystem may depend on this module, and it depends on
no other subsystem.

Two rules govern everything here.

**Every public function ends by recomputing conditions** (``07.1``). Nothing else in the
codebase may write the Weary or Miserable flags — ``tor.model.conditions.ConditionSet``
says so in its own docstring, and routing every change through one path is what keeps a
threshold crossing from being missed.

**Result and apply stay separate** (``01.4``). Resting is the one mechanic here with enough
shape to need it: :func:`resolve_rest` computes what each hero would get without touching
anything, and :func:`apply_rest` commits it. That is what lets a UI preview a rest, and what
makes the arithmetic testable without a live Company.

.. note::
   ``7.4`` and ``7.5`` write ``recompute_load(hero)`` and ``recompute_conditions(hero)``,
   taking the hero alone. Both take a keyword-only ``ctx`` here. Load is the sum of each
   item's Load *after* ``MODIFY_ITEM_LOAD``, so computing it needs the effect bus and the
   gear types; the bus cannot live on ``Hero`` (an import cycle — see
   :mod:`tor.rules.context`) and the result may not be cached there either (``17.5``
   requirement 2 and ``04.7`` both forbid it by name). The dependencies must therefore be
   parameters, and one context object is the smallest form.

**Adversaries are not handled here.** ``07.2`` and ``12.3`` make them asymmetric — an
adversary never becomes Weary from Endurance loss and is taken out of the fight at zero
rather than falling unconscious — and ``AdversaryInstance.take_endurance_loss`` already
implements that path.

**Not enforced here, by design.** ``7.6``'s "at most one short rest per day" is a clock
constraint and belongs to ``17.3``. ``7.4``'s ceiling of 10 Treasure Load per pack animal
needs the Company, so it belongs where mounts live (``13``). Marvellous Artefacts and
Wondrous Items each add a point of Treasure Load (``7.4``); they are not modelled until
``15`` and join in :func:`treasure_load` when they are.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from tor.effects.hooks import (
    ActionContribution,
    Contribution,
    FlagContribution,
    Hook,
)
from tor.errors import RuleViolation
from tor.events import Event, EventKind
from tor.model.company import Company
from tor.model.conditions import TreasureHoldings
from tor.model.gear import ArmourInstance, Gear, ShieldInstance, WeaponInstance
from tor.model.hero import Hero
from tor.model.ids import EffectId, HeroId
from tor.rules.context import GearIndex, RulesContext

__all__ = [
    "ChangeSource",
    "RestDelta",
    "RestKind",
    "RestOutcome",
    "TreasurePlace",
    "ZeroEnduranceOutcome",
    "advance_injury_recovery",
    "apply_rest",
    "change_endurance",
    "change_fatigue",
    "change_hope",
    "change_treasure",
    "move_treasure",
    "prolonged_rest",
    "recompute_conditions",
    "recompute_load",
    "resolve_rest",
    "short_rest",
    "spend_fellowship_for_hope",
    "treasure_load",
]

#: ``07.2``: a hero at zero Endurance wakes after about an hour with 1 Endurance.
UNCONSCIOUS_MINUTES = 60
WAKES_AT_ENDURANCE = 1
#: ``08.9``: a Dying hero has roughly an hour in which a HEALING roll may save them.
DYING_WINDOW_MINUTES = 60


class ChangeSource(StrEnum):
    """Why a resource moved.

    Recorded on the event, and read by the Hope hooks: the Cultural Blessing that halves
    Fellowship Phase recovery has to tell that route from the other two. ``07`` and ``20``
    fix no such registry, so these ids are engine-coined and follow ``20.11`` — snake_case,
    unprefixed, permanent once published.
    """

    COMBAT = "combat"
    INJURY_SOURCE = "injury_source"
    EXERTION = "exertion"
    JOURNEY = "journey"
    SHORT_REST = "short_rest"
    PROLONGED_REST = "prolonged_rest"
    FELLOWSHIP = "fellowship"
    FELLOWSHIP_PHASE = "fellowship_phase"
    HOPE_SPEND = "hope_spend"
    EFFECT = "effect"
    TREASURE_FOUND = "treasure_found"
    TREASURE_SPENT = "treasure_spent"
    LOREMASTER = "loremaster"


class TreasurePlace(StrEnum):
    """Where a hero's Treasure sits (``07.4``).

    Standard of Living derives from the total; Load from ``CARRIED`` alone. A hero who
    caches a hoard keeps the wealth and sheds the burden.
    """

    CARRIED = "carried"
    ON_MOUNTS = "on_mounts"
    CACHED = "cached"


class ZeroEnduranceOutcome(StrEnum):
    """What reaching zero Endurance means.

    ``07.2``'s default is unconsciousness, or Dying if the hero is already Wounded. A source
    of injury overrides it — fire and falling Wound, cold and suffocation and poison kill
    (``16.4.3``) — and the override is the mechanically significant part.
    """

    UNCONSCIOUS = "unconscious"
    WOUNDED = "wounded"
    DYING = "dying"


class RestKind(StrEnum):
    SHORT = "short"
    PROLONGED = "prolonged"


@dataclass(frozen=True, slots=True)
class RestDelta:
    """What one hero gets out of one rest."""

    endurance: int = 0
    hope: int = 0
    fatigue: int = 0
    #: ``SHORT_REST_COUNTS_AS_PROLONGED`` fired for this hero (``07.6``).
    counted_as_prolonged: bool = False
    #: An effect forbade this hero rest — a poisoned hero cannot rest (``16.4.5``).
    blocked_by: EffectId | None = None


@dataclass(frozen=True, slots=True)
class RestOutcome:
    """``7.6``'s record of a rest.

    ``events`` is an addition to the three fields ``7.6`` lists, per ``01.4``: an Outcome
    "fully describes the consequence", and throwing the events away would force the caller
    to re-derive them.
    """

    kind: RestKind
    per_hero: Mapping[HeroId, RestDelta]
    fellowship_spent: int = 0
    events: tuple[Event, ...] = ()


CarriedItem = WeaponInstance | ArmourInstance | ShieldInstance


def _base_loads(gear: Gear, index: GearIndex) -> Iterator[tuple[CarriedItem, int]]:
    """Every borne item with its type's base Load (``07.4``).

    Walks the slots rather than ``Gear.carried()`` because each slot already knows which
    section of the pack its type lives in, which saves an ``isinstance`` chain. A dropped
    item weighs nothing — dropping a helm or shield mid-combat is a legal secondary action
    precisely because it sheds Load (``08.6``).
    """
    for weapon in gear.weapons:
        if not weapon.dropped:
            yield weapon, index.weapon(weapon.type_id).load
    for slot in (gear.armour, gear.helm):
        if slot is not None and not slot.dropped:
            yield slot, index.armour_type(slot.type_id).load
    if gear.shield is not None and not gear.shield.dropped:
        yield gear.shield, index.shield_type(gear.shield.type_id).load


def treasure_load(hero: Hero) -> int:
    """Load from Treasure: one point per point **carried** (``07.4``).

    Not ``TreasureHoldings.total``. Conflating the two is the likeliest bug in this area —
    it would make caching a hoard pointless, when the whole point is that it sheds the
    burden while keeping the wealth.
    """
    return hero.treasure.carried


def recompute_load(hero: Hero, *, ctx: RulesContext) -> int:
    """War gear + carried Treasure + Fatigue (``07.4``).

    Each item's base Load passes through ``MODIFY_ITEM_LOAD``, which is where Cunning Make
    (-2), the Dwarven blessing (halved, rounding up) and Mithril (replacing the value
    outright) live. An item's effective Load is floored at zero: ``07.4`` does not say so,
    but a -2 modifier on a Load-1 helm must not make a hero lighter for wearing it.

    Never consumes a usage budget — see :data:`~tor.rules.context.DERIVED_STAT_HOOKS`.
    """
    bus = ctx.bus(hero.id)
    war_gear = 0
    for item, base in _base_loads(hero.gear, ctx.gear):
        hook_ctx = ctx.hook_context(Hook.MODIFY_ITEM_LOAD, hero, item=item, base_load=base)
        war_gear += max(0, bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, hook_ctx, base).value)
    return war_gear + treasure_load(hero) + hero.fatigue


def recompute_conditions(hero: Hero, *, ctx: RulesContext) -> list[Event]:
    """Re-derive Weary and Miserable, emitting an event for each flip (``07.5``).

    Note the thresholds carefully. Weary is ``endurance <= load`` — **not** ``<``; Endurance
    exactly equal to Load means Weary. Miserable is ``shadow >= hope`` against *current*
    Hope (``03.8``), so a hero at zero Hope and zero Shadow is Miserable, which reads like a
    bug and is not.

    ``wounded`` is never touched here: it is set by the Wound rules and cleared by recovery,
    not derived from anything.

    Fires no hook, and emits nothing when neither flag moved — a no-op recompute is silent,
    which is what lets every other function in this module end by calling it.
    """
    load = recompute_load(hero, ctx=ctx)
    weary = hero.endurance <= load
    miserable = hero.shadow >= hero.hope

    events: list[Event] = []
    if weary is not hero.conditions.weary:
        hero.conditions.weary = weary
        events.append(
            Event(
                kind=EventKind.CONDITION_CHANGED,
                actor=hero.id,
                payload={
                    "condition": "weary",
                    "active": weary,
                    "endurance": hero.endurance,
                    "load": load,
                },
            )
        )
    if miserable is not hero.conditions.miserable:
        hero.conditions.miserable = miserable
        events.append(
            Event(
                kind=EventKind.CONDITION_CHANGED,
                actor=hero.id,
                payload={
                    "condition": "miserable",
                    "active": miserable,
                    "shadow": hero.shadow,
                    "hope": hero.hope,
                },
            )
        )
    return events


def change_endurance(
    hero: Hero,
    delta: int,
    source: ChangeSource,
    *,
    ctx: RulesContext,
    at_zero: ZeroEnduranceOutcome | None = None,
) -> list[Event]:
    """Move Endurance, clamped to ``[0, max_endurance]`` (``07.2``).

    Never raises for damage past zero: losing more Endurance than you have is a legal, if
    unlucky, outcome, and ``18``'s doctrine reserves exceptions for illegal actions.

    Reaching zero *from above* has a consequence, and which one is the point of ``at_zero``.
    Left ``None``, ``07.2``'s default applies: the hero falls unconscious and wakes after
    about an hour with 1 Endurance, unless already Wounded, in which case the Dying rules
    take over (``08.9``). A source of injury supplies its own (``16.4.3``).

    The hour lives in the event payload rather than in a call into the session layer:
    ``17.7`` forbids a subsystem calling back into the shell, which is exactly why the Event
    record sits below this module.
    """
    before = hero.endurance
    ceiling = hero.max_endurance.value
    hero.endurance = max(0, min(ceiling, before + delta))

    events: list[Event] = []
    if hero.endurance != before:
        events.append(
            Event(
                kind=EventKind.ENDURANCE_CHANGED,
                actor=hero.id,
                payload={
                    "before": before,
                    "after": hero.endurance,
                    "delta": hero.endurance - before,
                    "source": str(source),
                },
            )
        )
    if hero.endurance == 0 and before > 0:
        events.append(_at_zero_endurance(hero, at_zero))
    events += recompute_conditions(hero, ctx=ctx)
    return events


def _at_zero_endurance(hero: Hero, declared: ZeroEnduranceOutcome | None) -> Event:
    """``07.2``'s consequence of reaching zero, or the one a source of injury declared."""
    outcome = declared
    if outcome is None:
        outcome = (
            ZeroEnduranceOutcome.DYING
            if hero.conditions.wounded
            else ZeroEnduranceOutcome.UNCONSCIOUS
        )

    if outcome is ZeroEnduranceOutcome.UNCONSCIOUS:
        return Event(
            kind=EventKind.HERO_UNCONSCIOUS,
            actor=hero.id,
            payload={
                "wakes_after_minutes": UNCONSCIOUS_MINUTES,
                "wakes_at_endurance": WAKES_AT_ENDURANCE,
            },
        )
    if outcome is ZeroEnduranceOutcome.WOUNDED:
        hero.conditions.wounded = True
        return Event(
            kind=EventKind.CONDITION_CHANGED,
            actor=hero.id,
            payload={"condition": "wounded", "active": True, "cause": "zero_endurance"},
        )
    hero.dying = True
    return Event(
        kind=EventKind.HERO_DYING,
        actor=hero.id,
        payload={"window_minutes": DYING_WINDOW_MINUTES},
    )


def change_hope(hero: Hero, delta: int, source: ChangeSource, *, ctx: RulesContext) -> list[Event]:
    """Move Hope, clamped to ``[0, max_hope]`` (``07.3``).

    The single choke point for all three recovery routes, which is what makes the Virtue
    adding +1 to any Hope regain work everywhere without being written three times.

    A **gain** passes through ``MODIFY_HOPE_RECOVERY``. The hook context carries the route
    twice — as ``route`` and as a ``route_<source>`` flag — because ``build_predicate``'s
    ``{"flag": ...}`` form tests truthiness of a key in ``extra``, so a pack predicating on
    one specific route needs the flag spelling.

    A **spend** does not, and is refused outright at zero Hope: ``07.3`` says a hero with no
    Hope cannot spend it — no bonus die, no support, no magical success — and that is an
    illegal action, so it raises rather than silently clamping.
    """
    events: list[Event] = []
    if delta < 0:
        events += _spend_hope(hero, -delta, source)
    elif delta > 0:
        events += _gain_hope(hero, delta, source, ctx=ctx)
    events += recompute_conditions(hero, ctx=ctx)
    return events


def _spend_hope(hero: Hero, amount: int, source: ChangeSource) -> list[Event]:
    if hero.hope == 0:
        raise RuleViolation(
            "a hero at zero Hope cannot spend Hope",
            rule_reference="hope_spending",
            suggestion="recover Hope first: a Fellowship point while resting, or a "
            "prolonged rest, or the Fellowship Phase",
        )
    if amount > hero.hope:
        raise RuleViolation(
            f"cannot spend {amount} Hope; only {hero.hope} left",
            rule_reference="hope_spending",
        )
    before = hero.hope
    hero.hope -= amount
    return [
        Event(
            kind=EventKind.HOPE_CHANGED,
            actor=hero.id,
            payload={
                "before": before,
                "after": hero.hope,
                "delta": -amount,
                "source": str(source),
            },
        )
    ]


def _gain_hope(hero: Hero, amount: int, source: ChangeSource, *, ctx: RulesContext) -> list[Event]:
    bus = ctx.bus(hero.id)
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_HOPE_RECOVERY,
        hero,
        route=str(source),
        **{f"route_{source}": True},
    )
    contributions = bus.collect(Hook.MODIFY_HOPE_RECOVERY, hook_ctx)
    gain = max(0, bus.apply_numeric(Hook.MODIFY_HOPE_RECOVERY, hook_ctx, amount).value)

    before = hero.hope
    hero.hope = min(hero.max_hope.value, before + gain)
    ctx.consume(hero.id, contributions, hook_ctx)

    if hero.hope == before:
        return []
    return [
        Event(
            kind=EventKind.HOPE_CHANGED,
            actor=hero.id,
            payload={
                "before": before,
                "after": hero.hope,
                "delta": hero.hope - before,
                "source": str(source),
            },
        )
    ]


def change_fatigue(
    hero: Hero, delta: int, source: ChangeSource, *, ctx: RulesContext
) -> list[Event]:
    """Move Fatigue, floored at zero (``07.4``, ``10.7``).

    Fatigue raises Load one for one, so every change here can flip Weary — which is why it
    ends in :func:`recompute_conditions` like everything else.

    A **gain** passes through ``MODIFY_FATIGUE_GAIN``: numeric contributions adjust the
    amount, and a ``no_fatigue`` flag cancels it entirely. The hook fires here rather than
    in journey because ``07.1`` makes this the only writer of Fatigue, and ``04.7`` forbids
    a subsystem re-implementing a rule that already has a home.
    """
    events: list[Event] = []
    gained = delta
    if delta > 0:
        bus = ctx.bus(hero.id)
        hook_ctx = ctx.hook_context(Hook.MODIFY_FATIGUE_GAIN, hero, source=str(source))
        contributions = bus.collect(Hook.MODIFY_FATIGUE_GAIN, hook_ctx)
        if any(
            isinstance(c, FlagContribution) and c.flag == "no_fatigue" and c.value
            for c in contributions
        ):
            gained = 0
        else:
            gained = max(0, bus.apply_numeric(Hook.MODIFY_FATIGUE_GAIN, hook_ctx, delta).value)
        ctx.consume(hero.id, contributions, hook_ctx)

    before = hero.fatigue
    hero.fatigue = max(0, before + gained)
    if hero.fatigue != before:
        events.append(
            Event(
                kind=EventKind.FATIGUE_CHANGED,
                actor=hero.id,
                payload={
                    "before": before,
                    "after": hero.fatigue,
                    "delta": hero.fatigue - before,
                    "source": str(source),
                },
            )
        )
    events += recompute_conditions(hero, ctx=ctx)
    return events


def change_treasure(
    hero: Hero,
    delta: int,
    source: ChangeSource,
    *,
    ctx: RulesContext,
    where: TreasurePlace = TreasurePlace.CARRIED,
) -> list[Event]:
    """Gain or spend Treasure in one place (``07.4``).

    ``where`` defaults to ``CARRIED``, which is where Treasure arrives; moving it afterwards
    is :func:`move_treasure`. Invariant I9 keeps every holding non-negative, so spending more
    than is held in that place is a ``RuleViolation`` rather than a negative balance.
    """
    holdings = hero.treasure
    before = getattr(holdings, str(where))
    after = before + delta
    if after < 0:
        raise RuleViolation(
            f"cannot take {-delta} Treasure from {where}; only {before} there",
            rule_reference="treasure_non_negative",
        )
    hero.treasure = _with_place(hero, where, after)

    events: list[Event] = []
    if delta:
        events.append(
            Event(
                kind=EventKind.TREASURE_CHANGED,
                actor=hero.id,
                payload={
                    "place": str(where),
                    "before": before,
                    "after": after,
                    "delta": delta,
                    "source": str(source),
                },
            )
        )
    events += recompute_conditions(hero, ctx=ctx)
    return events


def move_treasure(
    hero: Hero,
    amount: int,
    *,
    origin: TreasurePlace,
    destination: TreasurePlace,
    ctx: RulesContext,
) -> list[Event]:
    """Shift Treasure between carried, on mounts, and cached (``07.4``).

    Not expressible as two :func:`change_treasure` calls: those would emit two events and
    pass through a state where the hero's total dipped, which a replay would faithfully
    reproduce. One move, one event, total unchanged throughout.

    Standard of Living is unaffected — it derives from the total — while Load falls by the
    amount moved out of ``CARRIED``. That asymmetry is the whole mechanic.
    """
    if amount < 0:
        raise RuleViolation(
            "a Treasure move takes a non-negative amount",
            rule_reference="treasure_move",
        )
    if origin is destination:
        raise RuleViolation(
            f"origin and destination are both {origin}",
            rule_reference="treasure_move",
        )
    available = getattr(hero.treasure, str(origin))
    if amount > available:
        raise RuleViolation(
            f"cannot move {amount} Treasure from {origin}; only {available} there",
            rule_reference="treasure_move",
        )

    hero.treasure = _with_place(hero, origin, available - amount)
    hero.treasure = _with_place(
        hero, destination, getattr(hero.treasure, str(destination)) + amount
    )

    events = [
        Event(
            kind=EventKind.TREASURE_MOVED,
            actor=hero.id,
            payload={
                "amount": amount,
                "origin": str(origin),
                "destination": str(destination),
                "total": hero.treasure.total,
            },
        )
    ]
    events += recompute_conditions(hero, ctx=ctx)
    return events


def _with_place(hero: Hero, place: TreasurePlace, value: int) -> TreasureHoldings:
    """``TreasureHoldings`` is frozen, so a change is a replacement."""
    return replace(hero.treasure, **{str(place): value})


def advance_injury_recovery(hero: Hero, *, days: int = 1, ctx: RulesContext) -> list[Event]:
    """Count down a Severe injury's mending time (``07.7``).

    When it reaches zero the Wounded flag clears and ``WOUND_HEALED`` is emitted, once.

    Two neighbouring rules live elsewhere: a Moderate injury clears at the end of the combat
    in which it was taken and carries no day count at all (``08.8``), and a Dying hero saved
    by First Aid adds ten days less those the successful HEALING roll removed (``08.9``).
    """
    if days < 0:
        raise RuleViolation(
            "injury recovery advances by a non-negative number of days",
            rule_reference="injury_days",
        )
    before = hero.injury_days
    hero.injury_days = max(0, before - days)

    events: list[Event] = []
    if before > 0 and hero.injury_days == 0 and hero.conditions.wounded:
        hero.conditions.wounded = False
        events.append(Event(kind=EventKind.WOUND_HEALED, actor=hero.id, payload={"days": before}))
    events += recompute_conditions(hero, ctx=ctx)
    return events


# ----------------------------------------------------------------------------------
# Resting (07.6) — resolve and apply, per 01.4
# ----------------------------------------------------------------------------------


def resolve_rest(
    kind: RestKind,
    heroes: Sequence[Hero],
    *,
    ctx: RulesContext,
    sheltered: bool,
) -> dict[HeroId, RestDelta]:
    """What each hero would get, touching nothing (``07.6``).

    Resting involves no randomness, so this preview is exact rather than indicative.

    Order matters, and ``07.6`` is explicit about the first step:

    1. ``SHORT_REST_COUNTS_AS_PROLONGED`` is checked **before** the recovery formula is
       selected. It converts the rest *entirely*, so the zero-Hope point and the sheltered
       Fatigue shed come with it, not just the Endurance formula.
    2. A ``cannot_rest`` flag yields nothing but the effect that blocked it. That is not a
       ``RuleViolation`` — resting while poisoned is legal, it simply achieves nothing.
    3. The base formula, then the recovery hooks.

    Company-wide contributions are gathered across every hero first and folded in second, so
    the outcome never depends on the order the heroes were passed in.
    """
    effective: dict[HeroId, RestKind] = {}
    blocked: dict[HeroId, EffectId] = {}
    collected: dict[HeroId, tuple[Hook, list[Contribution]]] = {}
    company_bonus = 0

    for hero in heroes:
        hero_kind = kind
        if kind is RestKind.SHORT and _counts_as_prolonged(hero, ctx=ctx):
            hero_kind = RestKind.PROLONGED
        effective[hero.id] = hero_kind

        hook = _RECOVERY_HOOK[hero_kind]
        bus = ctx.bus(hero.id)
        hook_ctx = ctx.hook_context(
            hook,
            hero,
            rest_kind=str(hero_kind),
            sheltered=sheltered,
            company=tuple(h.id for h in heroes),
        )
        contributions = bus.collect(hook, hook_ctx)
        collected[hero.id] = (hook, contributions)

        for contribution in contributions:
            if (
                isinstance(contribution, FlagContribution)
                and contribution.flag == "cannot_rest"
                and contribution.value
            ):
                blocked[hero.id] = contribution.source
            elif (
                isinstance(contribution, ActionContribution)
                and contribution.action == "company_recovery"
            ):
                company_bonus += int(contribution.payload.get("amount", 0))

    per_hero: dict[HeroId, RestDelta] = {}
    for hero in heroes:
        if (blocker := blocked.get(hero.id)) is not None:
            per_hero[hero.id] = RestDelta(blocked_by=blocker)
            continue

        hero_kind = effective[hero.id]
        hook, contributions = collected[hero.id]
        base = _base_recovery(hero, hero_kind)
        bus = ctx.bus(hero.id)
        hook_ctx = ctx.hook_context(hook, hero, rest_kind=str(hero_kind))
        endurance = max(0, bus.apply_numeric(hook, hook_ctx, base).value) + company_bonus

        prolonged = hero_kind is RestKind.PROLONGED
        per_hero[hero.id] = RestDelta(
            endurance=endurance,
            # 07.3: only a hero at *zero* Hope regains any, and exactly one point.
            hope=1 if prolonged and hero.hope == 0 else 0,
            # 07.6, 10.7: a point of Fatigue sheds only in a safe refuge, never on the road.
            fatigue=-1 if prolonged and sheltered and hero.fatigue > 0 else 0,
            counted_as_prolonged=kind is RestKind.SHORT and prolonged,
        )
    return per_hero


_RECOVERY_HOOK: Mapping[RestKind, Hook] = {
    RestKind.SHORT: Hook.MODIFY_SHORT_REST_RECOVERY,
    RestKind.PROLONGED: Hook.MODIFY_PROLONGED_REST_RECOVERY,
}


def _counts_as_prolonged(hero: Hero, *, ctx: RulesContext) -> bool:
    """``07.6``: one Cultural Virtue makes a short rest count as a prolonged one."""
    bus = ctx.bus(hero.id)
    hook_ctx = ctx.hook_context(Hook.SHORT_REST_COUNTS_AS_PROLONGED, hero)
    contributions = bus.collect(Hook.SHORT_REST_COUNTS_AS_PROLONGED, hook_ctx)
    converted = any(
        isinstance(c, FlagContribution) and c.flag == "counts_as_prolonged" and c.value
        for c in contributions
    )
    if converted:
        ctx.consume(hero.id, contributions, hook_ctx)
    return converted


def _base_recovery(hero: Hero, kind: RestKind) -> int:
    """``07.6``'s two tables, before any hook."""
    wounded = hero.conditions.wounded
    if kind is RestKind.SHORT:
        return 0 if wounded else hero.attributes.strength
    if wounded:
        return hero.attributes.strength
    return hero.max_endurance.value - hero.endurance


def apply_rest(
    kind: RestKind,
    heroes: Sequence[Hero],
    per_hero: Mapping[HeroId, RestDelta],
    *,
    ctx: RulesContext,
) -> list[Event]:
    """Commit a resolved rest.

    Every change goes back through ``change_endurance`` / ``change_hope`` /
    ``change_fatigue``, so each one re-clamps and re-derives conditions through the single
    path ``07.1`` mandates.
    """
    source = ChangeSource.SHORT_REST if kind is RestKind.SHORT else ChangeSource.PROLONGED_REST
    events: list[Event] = []
    for hero in heroes:
        delta = per_hero.get(hero.id)
        if delta is None or delta.blocked_by is not None:
            continue
        if delta.endurance:
            events += change_endurance(hero, delta.endurance, source, ctx=ctx)
        if delta.hope:
            events += change_hope(hero, delta.hope, source, ctx=ctx)
        if delta.fatigue:
            events += change_fatigue(hero, delta.fatigue, source, ctx=ctx)
        events.append(
            Event(
                kind=EventKind.RESTED,
                actor=hero.id,
                payload={
                    "kind": str(kind),
                    "endurance": delta.endurance,
                    "hope": delta.hope,
                    "fatigue": delta.fatigue,
                    "counted_as_prolonged": delta.counted_as_prolonged,
                },
            )
        )
    return events


def short_rest(
    company: Company,
    heroes: Sequence[Hero],
    *,
    ctx: RulesContext,
    sheltered: bool = False,
) -> RestOutcome:
    """About an hour of inactivity (``07.6``).

    ``sheltered`` is not in ``7.1``'s signature, because ``7.6`` gives the shelter condition
    only to prolonged rests. It is needed anyway: the Cultural Virtue that makes a short rest
    count as a prolonged one "entirely" brings the Fatigue shed with it, so the question
    becomes live. It defaults to ``False`` — the road — which is the safe direction and hides
    no decision, since an unconverted short rest ignores it.
    """
    del company  # 7.1's signature; the Company is not read for a short rest.
    per_hero = resolve_rest(RestKind.SHORT, heroes, ctx=ctx, sheltered=sheltered)
    events = apply_rest(RestKind.SHORT, heroes, per_hero, ctx=ctx)
    return RestOutcome(kind=RestKind.SHORT, per_hero=per_hero, events=tuple(events))


def prolonged_rest(
    company: Company,
    heroes: Sequence[Hero],
    *,
    ctx: RulesContext,
    sheltered: bool,
) -> RestOutcome:
    """A night's sleep (``07.6``).

    ``sheltered`` is required, not defaulted: whether the Company slept in a safe refuge or
    on the road is a Loremaster's call, and ``00-README.md`` §3 forbids guessing a default
    that hides the decision.
    """
    del company  # 7.1's signature; the Company is not read for a prolonged rest.
    per_hero = resolve_rest(RestKind.PROLONGED, heroes, ctx=ctx, sheltered=sheltered)
    events = apply_rest(RestKind.PROLONGED, heroes, per_hero, ctx=ctx)
    return RestOutcome(kind=RestKind.PROLONGED, per_hero=per_hero, events=tuple(events))


def spend_fellowship_for_hope(
    company: Company,
    allocation: Mapping[HeroId, int],
    *,
    heroes: Mapping[HeroId, Hero],
    ctx: RulesContext,
) -> list[Event]:
    """Trade Fellowship points for Hope while the Company rests (``07.3``, route 1).

    One Hope per point, distributed by the players' agreement.

    ``heroes`` is an addition to ``7.1``'s signature: ``Company.heroes`` holds ``HeroId``
    values, so there is no way to reach a ``Hero`` from a ``Company`` alone.

    The pool is spent **before** any Hope is credited, so an over-allocation fails with
    nobody having gained anything. Heroes are visited in sorted id order rather than mapping
    order, because ``17.4`` promises a replay reproduces state byte for byte and a
    save/load round-trip need not preserve insertion order.
    """
    for hero_id, points in sorted(allocation.items()):
        if points < 0:
            raise RuleViolation(
                f"cannot allocate {points} Fellowship points to {hero_id!r}",
                rule_reference="fellowship_allocation",
            )
        if hero_id not in heroes:
            raise RuleViolation(
                f"{hero_id!r} is not a hero of this Company",
                rule_reference="fellowship_allocation",
            )

    total = sum(allocation.values())
    if not total:
        return []
    company.fellowship.spend(total)

    events: list[Event] = [
        Event(
            kind=EventKind.FELLOWSHIP_SPENT,
            payload={
                "points": total,
                "allocation": {str(k): v for k, v in sorted(allocation.items())},
                "remaining": company.fellowship.current,
            },
        )
    ]
    for hero_id, points in sorted(allocation.items()):
        if points:
            events += change_hope(heroes[hero_id], points, ChangeSource.FELLOWSHIP, ctx=ctx)
    return events
