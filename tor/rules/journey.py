"""Journeys: the hex path, Marching Tests, events, Fatigue and arrival (``10``).

A subsystem, not a shared leaf: it may be imported by nobody at L4, and it imports the
leaves it needs — :mod:`tor.rules.resources` for every Fatigue and Hope change,
:mod:`tor.rules.shadow` for the Dread two events inflict, and :mod:`tor.rules.injury` for
the Wound that *Terrible Misfortune* deals. ``01.1`` names that last one explicitly: "when
journey needs to inflict a Wound, it calls ``tor.rules.injury`` (a shared L4 leaf)".

Five things are worth reading before the code.

**The path excludes the starting hex** (``10.1``), and ``19.4`` makes the off-by-one its own
mandatory test. A five-hex path means five hexes to cover; :func:`plan_journey` checks that
every ``Hex.index`` is its own position in the tuple, so a caller who numbers from 1 is
refused rather than silently sent one hex too far.

**Arrival is checked before the event** (``10.3.2``). When the marching distance matches or
exceeds the hexes remaining, the Company simply arrives and **no event occurs**. Writing the
event first and the arrival check second gives a spurious final event on every journey.

**A Perilous Area stops the Company dead** (``10.3.3``), ahead of both the event and the
arrival: the march stops at the first hex of the area, the Company faces ``peril`` events
there, and only then does :func:`exit_perilous_area` put it down on the first hex beyond the
boundary. An area may carry its own event table, which is why the table is a parameter to
:func:`determine_event` and not a module constant.

**Only the event-determination roll treats a numeric contribution as a shift.** ``10.4.2``
gives ``MODIFY_JOURNEY_EVENT_ROLL`` two very different jobs: one effect shifts the Feat die
*result* by +1 (``LookupTable.lookup(shift=...)``, per ``02.2``), and another makes the event
resolve "as if in a Border Land ... regardless of the actual region". Neither is a bonus die,
so this hook does not go through ``build_request``'s ``extra_hooks`` — see
:func:`determine_event`.

**Fatigue is never written here.** Every point goes through
``resources.change_fatigue``, which owns ``MODIFY_FATIGUE_GAIN`` and the Load recomputation
that can flip Weary. ``10.5``'s rule that Fatigue cannot be shed while the journey lasts is
likewise already resources': ``07.6`` sheds a point only on a prolonged rest taken
*sheltered*, which the road is not.

.. note::
   ``10.2`` contains a contradiction this module has to resolve. It requires all four roles
   covered and says a hero holding more than one needs an effect's permission, and two lines
   later says "with fewer than four heroes, some must cover multiple roles" — which would be
   impossible for a Company of three. :func:`validate_roles` reads the permission rule as
   applying only when the Company is large enough to avoid doubling up; with fewer heroes
   than roles, necessity permits it.

.. note::
   ``10.7``'s code block and the prose below it disagree about a mounted forced march. The
   block's forced-march line overwrites the mounted halving entirely; the prose says to
   "treat forced march as replacing the base rate and apply the mounted halving afterwards",
   and to flag the combination rather than guess. :func:`journey_days` follows the prose and
   :func:`journey_day_warnings` raises the flag.

**Not here.** The hex map is campaign data: this module is handed a path and never builds
one. Narrating the Mishap and Short Cut *days* into a calendar belongs to ``17.3``'s clock;
:attr:`Journey.day_adjustments` records them and :func:`journey_days` reads them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Any

# Ops are content (05.6): tor.content.ops knows how to read an on_failure list, and this
# module knows what each op means for a journey.
from tor.content.ops import Op, OpKind, Who, parse_ops
from tor.dice import Randomness
from tor.effects.hooks import FlagContribution, Hook, NumericContribution, Terrain
from tor.errors import ContentError, RuleViolation, StateError, Warning_
from tor.events import Event, EventKind
from tor.model.abilities import ability_attribute
from tor.model.conditions import Condition, RegionType, Season
from tor.model.gear import Mount
from tor.model.hero import Hero
from tor.model.ids import AbilityId, HeroId
from tor.rolls import (
    FeatDicePolicy,
    RollPurpose,
    RollRequest,
    RollResult,
    SupportInput,
    attribute_tn,
    build_request,
    policy_sources,
    resolve,
)
from tor.rules.context import RulesContext
from tor.rules.injury import wound_hero
from tor.rules.resources import (
    ChangeSource,
    change_endurance,
    change_fatigue,
    change_hope,
)
from tor.rules.shadow import ShadowSource, gain_shadow
from tor.tables import LookupTable

__all__ = [
    "MARCHING_BASE",
    "MAX_HEXES_PER_LEG",
    "REGION_POLICY",
    "ROLE_SKILL",
    "EventDeclaration",
    "EventDetermination",
    "EventTarget",
    "FatigueRelief",
    "Hex",
    "Journey",
    "JourneyEndOutcome",
    "JourneyEvent",
    "JourneyEventOutcome",
    "JourneyEventRecord",
    "JourneyRole",
    "JourneyStep",
    "PerilousArea",
    "TerrainKind",
    "apply_event",
    "apply_step",
    "began_event",
    "determine_event",
    "end_journey",
    "event_dice",
    "exit_perilous_area",
    "forced_march_fatigue",
    "journey_day_warnings",
    "journey_days",
    "marching_distance",
    "plan_journey",
    "residual_fatigue",
    "resolve_event",
    "resolve_step",
    "roll_marching_test",
    "select_target",
    "validate_roles",
]

#: ``10.1``: a journey of more than twenty hexes should be split into legs. A warning at
#: construction, never an error — a Loremaster who wants one long leg gets one.
MAX_HEXES_PER_LEG = 20

#: ``10.3.1``: a successful Marching Test carries the Company three hexes plus one per
#: Success icon — pattern P3 with base 3.
MARCHING_BASE = 3
#: A failed Marching Test, by season. Spring and Summer carry two hexes, Autumn and Winter
#: only one.
FAILED_MARCH_WARM = 2
FAILED_MARCH_COLD = 1
WARM_SEASONS: frozenset[Season] = frozenset({Season.SPRING, Season.SUMMER})

#: ``10.4.4``: terrain properties of the *event hex*, not of the journey.
HARD_TERRAIN_PENALTY_DICE = 1
ROAD_BONUS_DICE = 1

#: ``10.6``: the end-of-journey TRAVEL roll sheds 1 Fatigue plus 1 per Success icon —
#: pattern P3 again, with base 1.
FATIGUE_RELIEF_BASE = 1

TRAVEL = AbilityId("travel")
HUNTING = AbilityId("hunting")
AWARENESS = AbilityId("awareness")
EXPLORE = AbilityId("explore")

#: ``10.1`` declares a ``TerrainKind`` enum whose three members are exactly those of
#: ``04.4``'s :class:`~tor.effects.hooks.Terrain`, and ``20`` gives one id list for both.
#: Two enums would mean a hex's terrain could not be handed to an ``Environment``, which is
#: what an effect predicating on terrain reads — so ``10.1``'s name aliases ``04.4``'s type.
TerrainKind = Terrain


class JourneyRole(StrEnum):
    """The four roles of ``10.2``. Every journey must cover all four."""

    GUIDE = "guide"
    HUNTER = "hunter"
    LOOKOUT = "lookout"
    SCOUT = "scout"


#: The Skill each role is challenged on (``10.2``). The Guide's is the Marching Test itself,
#: which is why ``10.4.1``'s target table never names them.
ROLE_SKILL: Mapping[JourneyRole, AbilityId] = {
    JourneyRole.GUIDE: TRAVEL,
    JourneyRole.HUNTER: HUNTING,
    JourneyRole.LOOKOUT: AWARENESS,
    JourneyRole.SCOUT: EXPLORE,
}

#: ``10.4.2`` — pattern P10. The region sets the Feat die policy for the roll that decides
#: *which* event happens, so a Dark Land tends toward the eye and a Border Land toward the
#: rune.
REGION_POLICY: Mapping[RegionType, FeatDicePolicy] = {
    RegionType.BORDER: FeatDicePolicy.FAVOURED,
    RegionType.WILD: FeatDicePolicy.NORMAL,
    RegionType.DARK: FeatDicePolicy.ILL_FAVOURED,
}

#: Ops a journey event table may carry. The rest of ``05.6``'s vocabulary belongs to council
#: revelations and adversary templates, and a journey table naming one is a malformed pack.
_JOURNEY_OPS: frozenset[OpKind] = frozenset(
    {
        OpKind.WOUND,
        OpKind.SHADOW,
        OpKind.HOPE,
        OpKind.ENDURANCE,
        OpKind.FATIGUE,
        OpKind.JOURNEY_DAYS,
        OpKind.SUPPRESS_FATIGUE,
        OpKind.CONDITION,
        OpKind.NARRATIVE,
    }
)


# -- the path ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerilousArea:
    """An unhexed stretch of map with a Peril rating (``10.3.3``)."""

    id: str
    name: str
    #: How many Events must be faced before the Company may leave.
    peril: int
    #: An area-specific events table, used instead of the standard one.
    event_table: str | None = None

    def __post_init__(self) -> None:
        if self.peril < 0:
            raise ValueError(f"a Peril rating cannot be negative, got {self.peril}")


@dataclass(frozen=True, slots=True)
class Hex:
    """One hex of the path (``10.1``)."""

    index: int
    terrain: TerrainKind
    region: RegionType
    has_road: bool = False
    perilous_area: PerilousArea | None = None
    landmark: str | None = None

    @property
    def hard(self) -> bool:
        """Costs the event roll a die, and the journey an extra day (``10.4.4``, ``10.7``)."""
        return self.terrain is TerrainKind.HARD

    @property
    def along_road(self) -> bool:
        """Grants the event roll a die (``10.4.4``).

        ``10.1`` carries the fact twice — as a ``road`` terrain and as ``has_road`` — so that
        a hill with a road through it is still hard going. Either says a road is there.
        """
        return self.has_road or self.terrain is TerrainKind.ROAD


@dataclass(frozen=True, slots=True)
class JourneyEventRecord:
    """One resolved event, kept on the journey for the log (``10.1``)."""

    hex_index: int
    event: str
    role: JourneyRole
    target: HeroId
    succeeded: bool
    fatigue: int
    perilous_area: str | None = None


@dataclass(slots=True)
class Journey:
    """A journey in progress (``10.1``).

    Mutable, and the one piece of journey state there is: position, the record of what has
    happened, and the two flags that change how long the trip takes.
    """

    origin: str
    destination: str
    #: Excludes the starting hex. ``19.4`` tests the off-by-one.
    path: tuple[Hex, ...]
    season: Season
    roles: dict[HeroId, set[JourneyRole]]
    mounts: dict[HeroId, Mount] = field(default_factory=dict)
    position: int = 0
    events_resolved: list[JourneyEventRecord] = field(default_factory=list)
    #: The ±1 day results of Mishap and Short Cut (``10.4.3``).
    day_adjustments: int = 0
    forced_march: bool = False
    #: True only when the *entire* Company travels on horseback (``10.7``).
    mounted: bool = False
    finished: bool = False

    @property
    def guide(self) -> HeroId:
        """The one hero covering the Guide role. :func:`validate_roles` guarantees exactly one."""
        for hero_id, roles in self.roles.items():
            if JourneyRole.GUIDE in roles:
                return hero_id
        raise StateError("this journey has no Guide; it was not validated at construction")

    @property
    def remaining(self) -> int:
        """Hexes still between the Company and the destination."""
        return len(self.path) - self.position

    @property
    def current_hex(self) -> Hex | None:
        """The hex the Company stands on, or ``None`` at the origin."""
        return self.path[self.position - 1] if self.position else None

    def heroes_in_role(self, role: JourneyRole) -> tuple[HeroId, ...]:
        """Everyone covering ``role``, in a fixed order so a replay is byte-for-byte (``17.4``)."""
        return tuple(sorted(h for h, roles in self.roles.items() if role in roles))

    def interrupt(self, reason: str) -> list[Event]:
        """End the journey early because something else claimed the Company (``10.3.2``).

        An unexpected occurrence drawing the Company into a different activity for a
        significant time is a Loremaster's ruling, not a computed one, so the reason is an
        input. The journey is finished either way, and :func:`end_journey` still owes the
        Company its Fatigue relief.
        """
        if self.finished:
            raise StateError("this journey has already finished")
        self.finished = True
        return [
            Event(
                kind=EventKind.JOURNEY_INTERRUPTED,
                payload={
                    "origin": self.origin,
                    "destination": self.destination,
                    "position": self.position,
                    "remaining": self.remaining,
                    "reason": reason,
                },
            )
        ]

    def narrate_only(self) -> list[Event]:
        """Skip the journey entirely, recording only that it happened (``10.8``).

        ``10.8`` is worth exposing in a UI: these rules are not meant for every trip, and
        should rarely be used for the journey home or when there is no time pressure. No
        roll is made, no event is drawn, and nothing on any hero changes — so the only thing
        to log is the arrival and the days it took.
        """
        if self.finished:
            raise StateError("this journey has already finished")
        self.position = len(self.path)
        self.finished = True
        return [
            Event(
                kind=EventKind.JOURNEY_ENDED,
                payload={
                    "origin": self.origin,
                    "destination": self.destination,
                    "days": journey_days(self),
                    "narrated": True,
                },
            )
        ]


def plan_journey(
    origin: str,
    destination: str,
    path: Sequence[Hex],
    season: Season,
    roles: Mapping[HeroId, set[JourneyRole]],
    *,
    ctx: RulesContext,
    mounts: Mapping[HeroId, Mount] | None = None,
    mounted: bool = False,
    forced_march: bool = False,
) -> tuple[Journey, list[Warning_]]:
    """Set the journey path (``10.3`` step 1), validating what can be validated.

    Three checks, and the first is the one ``19.4`` singles out: every ``Hex.index`` must be
    its own position in ``path``, because ``10.1``'s path excludes the starting hex and a
    caller numbering from 1 would send the Company one hex too far on every march.

    An empty path is a :class:`RuleViolation` — a journey with nowhere to go is not a
    journey — while a path longer than :data:`MAX_HEXES_PER_LEG` is only a
    :class:`~tor.errors.Warning_`, exactly as ``10.1`` asks.
    """
    if not path:
        raise RuleViolation(
            "a journey needs at least one hex; the path excludes the starting hex",
            rule_reference="journey_path_empty",
            suggestion="to record a trip without resolving it, use Journey.narrate_only",
        )
    for position, hex_ in enumerate(path):
        if hex_.index != position:
            raise RuleViolation(
                f"hex at position {position} is numbered {hex_.index}; the path is 0-based "
                "and excludes the starting hex",
                rule_reference="journey_hex_index",
                suggestion="number the hexes from 0, counting the first hex *after* the origin",
            )

    validate_roles(roles, ctx=ctx)

    warnings: list[Warning_] = []
    if len(path) > MAX_HEXES_PER_LEG:
        warnings.append(
            Warning_(
                code="journey_leg_too_long",
                message=(
                    f"a path of {len(path)} hexes is longer than {MAX_HEXES_PER_LEG}; "
                    "10.1 suggests splitting it into legs, each treated as its own journey"
                ),
            )
        )

    journey = Journey(
        origin=origin,
        destination=destination,
        path=tuple(path),
        season=season,
        roles={hero: set(assigned) for hero, assigned in roles.items()},
        mounts=dict(mounts or {}),
        mounted=mounted,
        forced_march=forced_march,
    )
    warnings += journey_day_warnings(journey)
    return journey, warnings


def began_event(journey: Journey) -> Event:
    """The log entry for a journey that is about to begin."""
    return Event(
        kind=EventKind.JOURNEY_BEGAN,
        payload={
            "origin": journey.origin,
            "destination": journey.destination,
            "hexes": len(journey.path),
            "season": str(journey.season),
            "roles": {str(h): sorted(str(r) for r in roles) for h, roles in journey.roles.items()},
            "mounted": journey.mounted,
            "forced_march": journey.forced_march,
        },
    )


def validate_roles(assignment: Mapping[HeroId, set[JourneyRole]], *, ctx: RulesContext) -> None:
    """Check a role assignment against ``10.2``.

    Raises on: no Guide, more than one Guide, an uncovered role, or a hero holding several
    roles without permission.

    ``10.2`` types this ``(assignment, bus)``, with one bus. It cannot be one:
    ``JOURNEY_ROLE_LIMITS`` is carried by a *Cultural Virtue* that permits **its holder** to
    cover more than one role, so the question is per hero and needs that hero's bus. The
    context carries one per combatant and is what every other rule in the engine already
    takes.

    The permission rule is read as applying only when the Company could have avoided
    doubling up: with fewer heroes than roles, ``10.2``'s own "some must cover multiple
    roles" makes it compulsory, and a rule cannot both compel and forbid the same move.
    """
    if not assignment:
        raise RuleViolation(
            "a journey needs heroes to cover its four roles",
            rule_reference="journey_roles_empty",
        )

    guides = sorted(h for h, roles in assignment.items() if JourneyRole.GUIDE in roles)
    if not guides:
        raise RuleViolation("a journey needs a Guide", rule_reference="journey_no_guide")
    if len(guides) > 1:
        raise RuleViolation(
            f"a journey has exactly one Guide, not {len(guides)}: {guides}",
            rule_reference="journey_multiple_guides",
            suggestion="the other candidates may take Hunter, Look-out or Scout instead",
        )

    covered = {role for roles in assignment.values() for role in roles}
    if uncovered := sorted(str(r) for r in JourneyRole if r not in covered):
        raise RuleViolation(
            f"these journey roles are uncovered: {uncovered}",
            rule_reference="journey_role_uncovered",
            suggestion="every journey must cover all four roles, doubling up if need be",
        )

    forced = len(assignment) < len(JourneyRole)
    for hero_id, roles in sorted(assignment.items()):
        if len(roles) <= 1 or forced or _may_hold_multiple_roles(hero_id, len(roles), ctx=ctx):
            continue
        raise RuleViolation(
            f"{hero_id!r} holds {len(roles)} roles, and no effect permits more than one",
            rule_reference="journey_role_limit",
            suggestion=(
                "a Company of four or more covers one role each unless a Cultural Virtue "
                "says otherwise"
            ),
        )


def _may_hold_multiple_roles(hero_id: HeroId, held: int, *, ctx: RulesContext) -> bool:
    """``JOURNEY_ROLE_LIMITS``: either a flat permission or a raised limit.

    A ``FlagContribution`` lifts the one-role rule entirely — which is what the Cultural
    Virtue of ``04.3.4`` does — while a ``NumericContribution`` raises the limit by its
    delta, so a future effect can grant exactly one extra role without granting all four.
    """
    bus = ctx.bus(hero_id)
    hook_ctx = ctx.hook_context(Hook.JOURNEY_ROLE_LIMITS, hero_id, roles_held=held)
    contributions = bus.collect(Hook.JOURNEY_ROLE_LIMITS, hook_ctx)
    limit = 1
    permitted = False
    for contribution in contributions:
        if isinstance(contribution, FlagContribution) and contribution.value:
            permitted = True
        elif isinstance(contribution, NumericContribution):
            limit += contribution.delta
    allowed = permitted or held <= limit
    if allowed and contributions:
        ctx.consume(hero_id, contributions, hook_ctx)
    return allowed


# -- marching ----------------------------------------------------------------------------


def marching_distance(roll: RollResult, season: Season) -> int:
    """How far one Marching Test carries the Company (``10.3.1``).

    A success is pattern P3 with base 3 — three hexes plus one per Success icon — which
    ``RollResult.magnitude`` owns. A failure ignores the icons entirely and depends only on
    the season.
    """
    if roll.succeeded:
        return roll.magnitude(MARCHING_BASE)
    return FAILED_MARCH_WARM if season in WARM_SEASONS else FAILED_MARCH_COLD


def roll_marching_test(
    journey: Journey,
    guide: Hero,
    rng: Randomness,
    *,
    ctx: RulesContext,
    spend_hope: bool = False,
    support: SupportInput | None = None,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
) -> RollResult:
    """The Guide's TRAVEL roll (``10.3.1``).

    An ordinary Skill roll: nothing about the Marching Test changes how the dice work, which
    is the whole point of seam A. ``purpose`` is what lets an effect find it — a Virtue
    favouring Marching Tests says so on the purpose rather than on the ability, because
    TRAVEL is rolled for plenty of things that are not marches.
    """
    if journey.finished:
        raise StateError("this journey has finished; no further Marching Test is made")
    if guide.id != journey.guide:
        raise RuleViolation(
            f"{guide.id!r} is not this journey's Guide ({journey.guide!r})",
            rule_reference="journey_marching_test_guide",
            suggestion="the Marching Test is the Guide's contribution and nobody else's",
        )
    request = build_request(
        guide,
        TRAVEL,
        bus=ctx.bus(guide.id),
        target_number=attribute_tn(
            guide.attributes.score(ability_attribute(TRAVEL)), short_campaign=ctx.short_campaign
        ),
        purpose=RollPurpose.MARCHING_TEST,
        spend_hope=spend_hope,
        support=support,
        bonus_dice=bonus_dice,
        penalty_dice=penalty_dice,
        weary=guide.conditions.weary,
        eye_is_auto_failure=guide.conditions.miserable,
        scene=ctx.scene,
        environment=ctx.environment,
        extra={"season": str(journey.season), f"season_{journey.season.value}": True},
    )
    return resolve(request, rng)


@dataclass(frozen=True, slots=True)
class JourneyStep:
    """What one Marching Test came to (``10.3.2``), decided but not yet applied."""

    distance: int
    #: Where the Company will stand once this step is applied.
    position: int
    arrived: bool = False
    #: The hex the event occurs in. ``None`` on arrival — ``10.3.2`` is explicit that no
    #: event occurs when the Company simply arrives.
    event_hex: Hex | None = None
    #: Set when the march stopped because it entered a Perilous Area (``10.3.3``).
    entered_area: PerilousArea | None = None

    @property
    def events_due(self) -> int:
        """How many Events to face: none on arrival, one normally, ``peril`` inside an area."""
        if self.entered_area is not None:
            return self.entered_area.peril
        return 0 if self.arrived else 1


def resolve_step(journey: Journey, roll: RollResult) -> JourneyStep:
    """Read one Marching Test (``10.3.2``, ``10.3.3``), without moving anybody.

    ``10.3.2`` writes this as a single mutating ``step``; it is split per ``01.4``, as
    ``08.5``'s ``resolve_attack`` was, because ``10.3.3``'s Perilous Areas make the decision
    worth inspecting before it is committed — a UI wants to say "you will be stopped in the
    marshes" before the Company is.

    The order of the three outcomes is load-bearing. A Perilous Area stops the march even
    when the destination lies within reach, because ``10.3.3`` says the Company stops "as
    soon as it enters". Arrival is checked next, and only if neither applies does an event
    occur at the hex reached.
    """
    if journey.finished:
        raise StateError("this journey has finished; there is no further step to take")
    distance = marching_distance(roll, journey.season)
    reach = min(distance, journey.remaining)

    for offset in range(1, reach + 1):
        hex_ = journey.path[journey.position + offset - 1]
        if hex_.perilous_area is not None:
            return JourneyStep(
                distance=distance,
                position=journey.position + offset,
                event_hex=hex_,
                entered_area=hex_.perilous_area,
            )

    if distance >= journey.remaining:
        return JourneyStep(distance=distance, position=len(journey.path), arrived=True)

    position = journey.position + distance
    return JourneyStep(distance=distance, position=position, event_hex=journey.path[position - 1])


def apply_step(journey: Journey, step: JourneyStep) -> list[Event]:
    """Move the Company (``10.3.2``).

    Arrival finishes the journey here; the Fatigue relief of ``10.6`` is a separate call,
    because ``end_journey`` needs the heroes and this does not.
    """
    journey.position = step.position
    journey.finished = step.arrived
    events = [
        Event(
            kind=EventKind.MARCHING_TEST_RESOLVED,
            payload={
                "distance": step.distance,
                "position": step.position,
                "remaining": journey.remaining,
                "arrived": step.arrived,
                "event_hex": None if step.event_hex is None else step.event_hex.index,
                "events_due": step.events_due,
            },
        )
    ]
    if step.entered_area is not None:
        events.append(
            Event(
                kind=EventKind.PERILOUS_AREA_ENTERED,
                payload={
                    "area": step.entered_area.id,
                    "name": step.entered_area.name,
                    "peril": step.entered_area.peril,
                    "hex": step.position - 1,
                },
            )
        )
    return events


def exit_perilous_area(journey: Journey) -> list[Event]:
    """Leave the area the Company is standing in (``10.3.3`` step 3).

    Marching Tests resume from the **first hex along the path outside the area's boundary**,
    so the Company is put down on the area's last hex and the next march counts from there.
    Calling this before the area's Events have been faced is the caller's business: the
    engine cannot tell a resolved event from an unresolved one, and ``01.6`` makes a
    mis-sequenced call a :class:`StateError` only when the engine can see it.
    """
    standing = journey.current_hex
    area = None if standing is None else standing.perilous_area
    if area is None:
        raise StateError("the Company is not inside a Perilous Area")

    position = journey.position
    while position < len(journey.path):
        next_area = journey.path[position].perilous_area
        if next_area is None or next_area.id != area.id:
            break
        position += 1

    journey.position = position
    journey.finished = journey.remaining == 0
    return [
        Event(
            kind=EventKind.PERILOUS_AREA_CLEARED,
            payload={
                "area": area.id,
                "position": position,
                "remaining": journey.remaining,
                "arrived": journey.finished,
            },
        )
    ]


# -- events ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventTarget:
    """Which role an event falls on, and the Skill it challenges (``10.4.1``)."""

    role: JourneyRole
    skill: AbilityId


def select_target(rng: Randomness, *, table: LookupTable[Any]) -> EventTarget:
    """Roll one Success die for the targeted role (``10.4.1``).

    The table is content, injected like every other. Two things about it are engine rules
    rather than data, and are checked here: the Guide is never a target — their contribution
    is the Marching Test — and the Skill a role is challenged on is fixed by ``10.2``, so a
    table pairing Scouts with HUNTING is a malformed pack rather than a variant.
    """
    row = table.roll(rng)
    if not isinstance(row, Mapping):
        raise ContentError(
            f"an event target row must be an object, got {row!r}", entity_id=table.id
        )
    try:
        role = JourneyRole(str(row["role"]))
    except (KeyError, ValueError) as exc:
        raise ContentError(
            f"event target row {row!r} needs a 'role' of {sorted(r.value for r in JourneyRole)}",
            entity_id=table.id,
        ) from exc
    if role is JourneyRole.GUIDE:
        raise ContentError(
            "the Guide is never the target of a journey event; their contribution is the "
            "Marching Test itself (10.4.1)",
            entity_id=table.id,
        )
    skill = AbilityId(str(row.get("skill", ROLE_SKILL[role])))
    if skill != ROLE_SKILL[role]:
        raise ContentError(
            f"role {role.value!r} is challenged on {ROLE_SKILL[role]!r}, not {skill!r} (10.2)",
            entity_id=table.id,
        )
    return EventTarget(role=role, skill=skill)


@dataclass(frozen=True, slots=True)
class JourneyEvent:
    """One row of the events table (``10.4.3``).

    Rows differ in whether their consequence fires on failure or on success, which is why
    both op lists are here and no branch in this module asks which event it is holding.
    """

    event: str
    fatigue: int
    on_failure: tuple[Op, ...] = ()
    on_success: tuple[Op, ...] = ()


@dataclass(frozen=True, slots=True)
class EventDetermination:
    """The event drawn, and how it was drawn (``10.4.2``)."""

    event: JourneyEvent
    roll: RollResult
    policy: FeatDicePolicy
    shift: int


def determine_event(
    hero: Hero,
    hex_: Hex,
    rng: Randomness,
    *,
    ctx: RulesContext,
    table: LookupTable[Any],
) -> EventDetermination:
    """Roll the Feat die that decides which event happens (``10.4.2``).

    The region sets the policy — pattern P10, through ``policy_sources`` so that
    ``RollRequest.policy`` stays the only place a policy is decided.

    ``MODIFY_JOURNEY_EVENT_ROLL`` is collected here rather than through
    ``build_request(extra_hooks=...)``, and this is the one roll hook in the engine where
    that is true. ``10.4.2`` gives its two contribution types meanings that the roll
    pipeline does not have:

    * a **numeric** delta is a shift of the Feat die *result* (``LookupTable.lookup(shift=)``
      per ``02.2``), not a bonus Success die — a table roll has no Success dice to add, and
      an icon face must pass through a shift untouched;
    * a **favoured** flag *replaces* the region's policy rather than joining it. ``10.4.2``
      says the event resolves "as if in a Border Land ... regardless of the actual region",
      and ``02.3.2``'s cancellation would turn a Dark Land plus that Virtue into a normal
      roll instead of a Favoured one.

    The one case ``02.3.2`` still governs is two effects pulling opposite ways: a favoured
    and an ill-favoured contribution on the same roll cancel, as they do everywhere else.
    """
    bus = ctx.bus(hero.id)
    # The region twice, as resources.change_hope carries its route twice and for the same
    # reason: build_predicate's {"flag": ...} form tests a key's truthiness, while matching a
    # value needs the value.
    named: dict[str, Any] = {
        "region": str(hex_.region),
        "terrain": str(hex_.terrain),
        f"region_{hex_.region.value}": True,
    }
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_JOURNEY_EVENT_ROLL,
        hero,
        purpose=str(RollPurpose.JOURNEY_EVENT),
        **named,
    )
    contributions = bus.collect(Hook.MODIFY_JOURNEY_EVENT_ROLL, hook_ctx)

    shift = 0
    favoured: list[str] = []
    ill_favoured: list[str] = []
    for contribution in contributions:
        if isinstance(contribution, NumericContribution):
            shift += contribution.delta
        elif isinstance(contribution, FlagContribution):
            if contribution.flag == "favoured":
                favoured.append(str(contribution.source))
            elif contribution.flag == "ill_favoured":
                ill_favoured.append(str(contribution.source))
    ctx.consume(hero.id, contributions, hook_ctx)

    if favoured and ill_favoured:
        policy = FeatDicePolicy.NORMAL
        source = "journey_event_cancelled"
    elif favoured:
        policy = FeatDicePolicy.FAVOURED
        source = favoured[0]
    elif ill_favoured:
        policy = FeatDicePolicy.ILL_FAVOURED
        source = ill_favoured[0]
    else:
        policy = REGION_POLICY[hex_.region]
        source = f"region_{hex_.region.value}"

    roll = resolve(
        RollRequest(
            purpose=RollPurpose.JOURNEY_EVENT,
            **policy_sources(policy, source=source),  # type: ignore[arg-type]
        ),
        rng,
    )
    row = table.lookup(roll.kept_feat, shift=shift)
    return EventDetermination(
        event=_read_event_row(row, table_id=table.id), roll=roll, policy=policy, shift=shift
    )


def _read_event_row(row: Any, *, table_id: str) -> JourneyEvent:
    """Parse one row of a journey events table (``10.4.3``, ``05.6``)."""
    if not isinstance(row, Mapping):
        raise ContentError(
            f"a journey event row must be an object, got {row!r}", entity_id=table_id
        )
    name = str(row.get("event", ""))
    if not name:
        raise ContentError("a journey event row needs an 'event'", entity_id=table_id)
    on_failure = parse_ops(
        row.get("on_failure", ()), entity_id=table_id, pointer=f"/{name}/on_failure"
    )
    on_success = parse_ops(
        row.get("on_success", ()), entity_id=table_id, pointer=f"/{name}/on_success"
    )
    for op in (*on_failure, *on_success):
        if op.kind not in _JOURNEY_OPS:
            raise ContentError(
                f"op {op.kind.value!r} is not one a journey event may carry; known: "
                f"{sorted(k.value for k in _JOURNEY_OPS)}",
                pointer=f"/{name}",
                entity_id=table_id,
            )
    return JourneyEvent(
        event=name,
        fatigue=int(row.get("fatigue", 0)),
        on_failure=on_failure,
        on_success=on_success,
    )


def event_dice(hex_: Hex) -> tuple[int, int]:
    """``(bonus, penalty)`` dice the event hex contributes (``10.4.4``).

    Hard going costs a die and a road grants one. These are properties of the *specific
    event hex*, not of the journey, so a single road hex helps even on a mountain crossing.
    """
    return (
        ROAD_BONUS_DICE if hex_.along_road else 0,
        HARD_TERRAIN_PENALTY_DICE if hex_.hard else 0,
    )


@dataclass(frozen=True, slots=True)
class EventDeclaration:
    """What the players bring to one journey event (``10.4.4``).

    One hero among those holding the targeted role makes the roll, and **up to one other
    hero covering the same role may support** it. That the supporter shares the role is
    checked; whether the circumstances allow the support is the Loremaster's, and arrives on
    the :class:`~tor.rolls.SupportInput`.
    """

    hex: Hex
    role: JourneyRole
    skill: AbilityId
    target: HeroId
    supporter: HeroId | None = None
    support: SupportInput | None = None
    #: ``10.4.4``: consequences naming "the target" reach the supporting hero "as well, where
    #: relevant". That is a Loremaster's judgement, so it defaults to off — the engine never
    #: Wounds a second hero on its own.
    supporter_shares: bool = False
    #: Heroes a ``who: "chosen"`` op falls on.
    chosen: tuple[HeroId, ...] = ()
    spend_hope: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0


@dataclass(frozen=True, slots=True)
class JourneyEventOutcome:
    """One event, resolved but not applied (``10.4``)."""

    declaration: EventDeclaration
    event: JourneyEvent
    determination: EventDetermination
    roll: RollResult
    ops: tuple[Op, ...]
    #: After suppression. ``10.4.3``: paid regardless of the roll, except where an op says not.
    fatigue: int
    suppressed: bool
    narrative: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return self.roll.succeeded


def resolve_event(
    hero: Hero,
    determination: EventDetermination,
    declaration: EventDeclaration,
    rng: Randomness,
    *,
    ctx: RulesContext,
    journey: Journey | None = None,
) -> JourneyEventOutcome:
    """Make the Skill roll the event calls for and grade it (``10.4.4``).

    Which op list fires is the table's business, not this function's: ``10.4.3``'s rows
    differ in whether the consequence follows a failure or a success, and ``05.6``'s two
    lists are what keeps that out of the code.

    ``journey`` is optional and read only to check that the supporter shares the targeted
    role — a check that needs the role assignment and nothing else.
    """
    if declaration.supporter is not None:
        if declaration.supporter == declaration.target:
            raise RuleViolation(
                "a hero cannot support their own roll",
                rule_reference="journey_support_self",
            )
        if declaration.support is None:
            raise RuleViolation(
                f"{declaration.supporter!r} is named as supporting but no support was offered",
                rule_reference="journey_support_missing",
                suggestion="pass a Support carrying the supporter's rank and the LM's approval",
            )
        if journey is not None and declaration.supporter not in journey.heroes_in_role(
            declaration.role
        ):
            raise RuleViolation(
                f"{declaration.supporter!r} does not cover the {declaration.role.value} role, "
                "so may not support its roll",
                rule_reference="journey_support_role",
                suggestion="10.4.4 allows one other hero covering the same role to support",
            )

    bonus, penalty = event_dice(declaration.hex)
    request = build_request(
        hero,
        declaration.skill,
        bus=ctx.bus(hero.id),
        target_number=attribute_tn(
            hero.attributes.score(ability_attribute(declaration.skill)),
            short_campaign=ctx.short_campaign,
        ),
        purpose=RollPurpose.JOURNEY_EVENT,
        spend_hope=declaration.spend_hope,
        support=declaration.support,
        bonus_dice=bonus + declaration.bonus_dice,
        penalty_dice=penalty + declaration.penalty_dice,
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=ctx.environment,
        extra={
            "event": determination.event.event,
            f"event_{determination.event.event}": True,
            "role": str(declaration.role),
            "region": str(declaration.hex.region),
            "terrain": str(declaration.hex.terrain),
        },
    )
    roll = resolve(request, rng)

    event = determination.event
    ops = event.on_success if roll.succeeded else event.on_failure
    suppressed = any(op.kind is OpKind.SUPPRESS_FATIGUE for op in ops)
    return JourneyEventOutcome(
        declaration=declaration,
        event=event,
        determination=determination,
        roll=roll,
        ops=ops,
        fatigue=0 if suppressed else event.fatigue,
        suppressed=suppressed,
        narrative=tuple(op.tag for op in ops if op.kind is OpKind.NARRATIVE and op.tag),
    )


def apply_event(
    journey: Journey,
    outcome: JourneyEventOutcome,
    heroes: Mapping[HeroId, Hero],
    *,
    ctx: RulesContext,
) -> list[Event]:
    """Apply one resolved event to the Company (``10.4.3``).

    Order is fixed and matters. The event's Fatigue is paid by **everyone in the Company**
    first, then the ops fire — *Mishap* gives the target "1 extra Fatigue", which only reads
    as extra if the base has already been paid.

    Every point of Fatigue goes through ``resources.change_fatigue``, so the Cultural Virtue
    that reduces each event's Fatigue by 1 and the one that prevents it entirely both work
    without this module knowing they exist.
    """
    if journey.finished:
        raise StateError("this journey has finished; its events are already resolved")
    company = _company(journey, heroes)
    events: list[Event] = []

    if outcome.fatigue:
        for hero in company:
            events += change_fatigue(hero, outcome.fatigue, ChangeSource.JOURNEY, ctx=ctx)

    for op in outcome.ops:
        events += _apply_op(op, journey, outcome, heroes, ctx=ctx)

    standing = outcome.declaration.hex
    journey.events_resolved.append(
        JourneyEventRecord(
            hex_index=standing.index,
            event=outcome.event.event,
            role=outcome.declaration.role,
            target=outcome.declaration.target,
            succeeded=outcome.succeeded,
            fatigue=outcome.fatigue,
            perilous_area=None if standing.perilous_area is None else standing.perilous_area.id,
        )
    )
    events.append(
        Event(
            kind=EventKind.JOURNEY_EVENT_RESOLVED,
            actor=outcome.declaration.target,
            payload={
                "event": outcome.event.event,
                "hex": standing.index,
                "region": str(standing.region),
                "role": str(outcome.declaration.role),
                "supporter": outcome.declaration.supporter,
                "succeeded": outcome.succeeded,
                "fatigue": outcome.fatigue,
                "fatigue_suppressed": outcome.suppressed,
                "narrative": list(outcome.narrative),
                "day_adjustments": journey.day_adjustments,
            },
            rolls=(outcome.determination.roll, outcome.roll),
        )
    )
    return events


def _company(journey: Journey, heroes: Mapping[HeroId, Hero]) -> tuple[Hero, ...]:
    """Everyone on the journey, in id order so a replay is byte-for-byte (``17.4``)."""
    missing = sorted(str(h) for h in journey.roles if h not in heroes)
    if missing:
        raise StateError(f"these heroes hold journey roles but were not supplied: {missing}")
    return tuple(heroes[hero_id] for hero_id in sorted(journey.roles))


def _recipients(
    op: Op,
    journey: Journey,
    outcome: JourneyEventOutcome,
    heroes: Mapping[HeroId, Hero],
) -> tuple[Hero, ...]:
    """Whom one op falls on (``05.6``'s ``who``)."""
    declaration = outcome.declaration
    if op.who is Who.COMPANY:
        return _company(journey, heroes)
    if op.who is Who.CHOSEN:
        if not declaration.chosen:
            raise RuleViolation(
                f"op {op.kind.value!r} falls on a chosen hero, and none was named",
                rule_reference="journey_op_chosen",
                suggestion="set EventDeclaration.chosen before applying the event",
            )
        return tuple(heroes[hero_id] for hero_id in declaration.chosen)
    if op.who is Who.ACTOR:
        return (heroes[declaration.target],)
    # Who.TARGET — the hero who rolled, and the supporter only where the LM says so (10.4.4).
    targets = [heroes[declaration.target]]
    if declaration.supporter_shares and declaration.supporter is not None:
        targets.append(heroes[declaration.supporter])
    return tuple(targets)


def _apply_op(
    op: Op,
    journey: Journey,
    outcome: JourneyEventOutcome,
    heroes: Mapping[HeroId, Hero],
    *,
    ctx: RulesContext,
) -> list[Event]:
    """Apply one op (``05.6``).

    ``tor.content.ops`` parses and validates; this knows what each one *means* for a journey,
    which is what keeps journey from importing council or combat for their halves of the
    vocabulary.
    """
    if op.kind in (OpKind.SUPPRESS_FATIGUE, OpKind.NARRATIVE):
        # Both are read at resolve time: one cancels the Fatigue, the other only tags the
        # event for the Loremaster to narrate.
        return []
    if op.kind is OpKind.JOURNEY_DAYS:
        journey.day_adjustments += op.delta
        return []

    events: list[Event] = []
    source = ChangeSource.JOURNEY
    for hero in _recipients(op, journey, outcome, heroes):
        if op.kind is OpKind.FATIGUE:
            events += change_fatigue(hero, op.points, source, ctx=ctx)
        elif op.kind is OpKind.HOPE:
            events += change_hope(hero, op.points, source, ctx=ctx)
        elif op.kind is OpKind.ENDURANCE:
            events += change_endurance(hero, op.points, source, ctx=ctx)
        elif op.kind is OpKind.SHADOW:
            # The event's own Skill roll was the test; 11.3 grants no second one.
            gained = gain_shadow(hero, op.points, _shadow_source(op, outcome), ctx=ctx)
            events += gained.events
        elif op.kind is OpKind.WOUND:
            events += _wound(hero, outcome, ctx=ctx)
        else:  # OpKind.CONDITION
            events += _set_condition(hero, op, outcome, ctx=ctx)
    return events


def _shadow_source(op: Op, outcome: JourneyEventOutcome) -> ShadowSource:
    """``11.1``'s source, named by the table row.

    Defaults to Dread, which is what every journey event in ``10.4.3`` inflicts, but the op
    carries a ``source`` field so a supplement's table can name Sorcery or Greed instead.
    """
    if op.source is None:
        return ShadowSource.DREAD
    try:
        return ShadowSource(op.source)
    except ValueError as exc:
        raise ContentError(
            f"journey event {outcome.event.event!r} names an unknown Shadow source "
            f"{op.source!r}; known: {sorted(s.value for s in ShadowSource)}",
            entity_id=outcome.event.event,
        ) from exc


def _wound(hero: Hero, outcome: JourneyEventOutcome, *, ctx: RulesContext) -> list[Event]:
    """``10.4.3``: on a failed *Terrible Misfortune*, the target is Wounded.

    No severity roll: ``10.4.3`` says only that the hero is Wounded, where ``08.8``'s first
    Wound reads the Wound Severity table. A journey event is not an attack, so it checks the
    box and leaves the days at zero — and :func:`~tor.rules.injury.wound_hero` still applies
    ``08.8``'s escalation if the box was already checked.
    """
    return wound_hero(
        hero,
        ctx=ctx,
        source=ChangeSource.JOURNEY,
        payload={"event": outcome.event.event, "hex": outcome.declaration.hex.index},
    )


def _set_condition(
    hero: Hero, op: Op, outcome: JourneyEventOutcome, *, ctx: RulesContext
) -> list[Event]:
    """A ``condition`` op (``05.6``).

    Only Wounded is settable. ``03.8`` reserves Weary and Miserable to
    ``resources.recompute_conditions``, which derives them from Endurance against Load and
    Shadow against Hope — a table row that set either directly would be overwritten by the
    next recomputation, so it is refused instead.
    """
    if op.condition == Condition.WOUNDED and op.value:
        return _wound(hero, outcome, ctx=ctx)
    raise ContentError(
        f"journey event {outcome.event.event!r} sets condition {op.condition!r}; only "
        f"{Condition.WOUNDED.value!r} may be set directly, because 03.8 derives Weary and "
        "Miserable from Endurance, Load, Shadow and Hope",
        entity_id=outcome.event.event,
    )


# -- ending the journey ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FatigueRelief:
    """One hero's Fatigue at journey's end (``10.6``)."""

    hero: HeroId
    before: int
    #: Step 1 — the mount's Vigour, through ``MODIFY_MOUNT_VIGOUR``.
    from_mount: int
    #: Step 2 — the TRAVEL roll, 1 plus 1 per Success icon.
    from_roll: int
    #: Further relief contributed on ``ON_JOURNEY_END``.
    from_effects: int
    roll: RollResult
    #: What remains, recorded on the sheet and shed at 1 point per sheltered prolonged rest.
    residual: int
    #: Forced-march Fatigue added before any of the relief (``10.7``).
    forced_march: int = 0


@dataclass(frozen=True, slots=True)
class JourneyEndOutcome:
    """What ending the journey came to (``10.6``)."""

    days: int
    relief: tuple[FatigueRelief, ...]
    events: tuple[Event, ...]
    arrived: bool
    warnings: tuple[Warning_, ...] = ()


def end_journey(
    journey: Journey,
    heroes: Sequence[Hero],
    rng: Randomness,
    *,
    ctx: RulesContext,
) -> JourneyEndOutcome:
    """Settle the Fatigue a journey leaves behind (``10.6``).

    Three steps, in ``10.6``'s order: the mount's Vigour comes off first, then each hero
    makes a TRAVEL roll shedding 1 plus 1 per Success icon, and what is left is residual —
    shed afterwards at one point per prolonged rest taken in a sheltered, safe refuge, and
    explicitly not while on the road.

    One step precedes all three. ``10.7`` charges a forced march "1 additional Fatigue per
    day", and this engine has no per-day clock inside a journey — the path is walked in
    Marching Tests, not days — so the whole charge lands here, before the relief, where the
    number of days is finally known. Doing it afterwards would let the TRAVEL roll shed
    Fatigue the hero had not yet gained.

    Takes ``ctx`` on top of ``10.6``'s signature, like every other rule that touches a bus.
    """
    if not journey.finished:
        raise StateError(
            "this journey has not finished; arrive, leave the last Perilous Area, or "
            "interrupt it before settling its Fatigue"
        )
    days = journey_days(journey)
    forced = forced_march_fatigue(journey)
    events: list[Event] = []
    relief: list[FatigueRelief] = []

    for hero in heroes:
        if forced:
            events += change_fatigue(hero, forced, ChangeSource.JOURNEY, ctx=ctx)
        before = hero.fatigue

        from_mount = _mount_relief(hero, journey, ctx=ctx)
        if from_mount:
            events += change_fatigue(hero, -from_mount, ChangeSource.JOURNEY, ctx=ctx)

        roll = _travel_relief_roll(hero, rng, ctx=ctx)
        from_roll = roll.magnitude(FATIGUE_RELIEF_BASE)
        from_effects = _journey_end_relief(hero, ctx=ctx)
        if from_roll or from_effects:
            events += change_fatigue(
                hero, -(from_roll + from_effects), ChangeSource.JOURNEY, ctx=ctx
            )

        relief.append(
            FatigueRelief(
                hero=hero.id,
                before=before,
                from_mount=from_mount,
                from_roll=from_roll,
                from_effects=from_effects,
                roll=roll,
                residual=hero.fatigue,
                forced_march=forced,
            )
        )

    events.append(
        Event(
            kind=EventKind.JOURNEY_ENDED,
            payload={
                "origin": journey.origin,
                "destination": journey.destination,
                "days": days,
                "narrated": False,
                "arrived": journey.remaining == 0,
                "events_resolved": len(journey.events_resolved),
                "residual_fatigue": {str(r.hero): r.residual for r in relief},
            },
        )
    )
    return JourneyEndOutcome(
        days=days,
        relief=tuple(relief),
        events=tuple(events),
        arrived=journey.remaining == 0,
        warnings=tuple(journey_day_warnings(journey)),
    )


def _mount_relief(hero: Hero, journey: Journey, *, ctx: RulesContext) -> int:
    """``10.6`` step 1 — the mount's Vigour, floored at zero.

    Vigour is instance data rather than a lookup (``03.5``), because at least one Cultural
    Virtue grants a pony above the usual maximum; ``MODIFY_MOUNT_VIGOUR`` is how that Virtue
    says so. A derived-stat hook, so ``RulesContext.consume`` leaves its budget alone —
    asking twice what a mount is worth must give the same answer.
    """
    mount = journey.mounts.get(hero.id)
    if mount is None:
        return 0
    hook_ctx = ctx.hook_context(Hook.MODIFY_MOUNT_VIGOUR, hero, mount=mount)
    vigour = ctx.bus(hero.id).apply_numeric(Hook.MODIFY_MOUNT_VIGOUR, hook_ctx, mount.vigour)
    return max(0, min(hero.fatigue, vigour.value))


def _travel_relief_roll(hero: Hero, rng: Randomness, *, ctx: RulesContext) -> RollResult:
    """``10.6`` step 2 — a TRAVEL roll, which each hero *may* make.

    The roll is offered to everyone rather than only to the Guide: ``10.6`` says "each hero".
    A hero with no Fatigue left still rolls, because an effect listening on
    ``ON_ROLL_RESOLVED`` may care and the alternative is a branch that silently skips dice a
    test has scripted.
    """
    request = build_request(
        hero,
        TRAVEL,
        bus=ctx.bus(hero.id),
        target_number=attribute_tn(
            hero.attributes.score(ability_attribute(TRAVEL)), short_campaign=ctx.short_campaign
        ),
        purpose=RollPurpose.SKILL,
        weary=hero.conditions.weary,
        eye_is_auto_failure=hero.conditions.miserable,
        scene=ctx.scene,
        environment=ctx.environment,
        extra={"journey_end": True},
    )
    return resolve(request, rng)


def _journey_end_relief(hero: Hero, *, ctx: RulesContext) -> int:
    """``ON_JOURNEY_END`` — further Fatigue an effect sheds on arrival.

    ``04.3.4`` lists the hook with no example, so its meaning is fixed here: a numeric
    contribution is extra points of Fatigue shed once the journey is over. Unlike the TRAVEL
    roll it is not conditional on a success, which is what makes it worth having.
    """
    hook_ctx = ctx.hook_context(Hook.ON_JOURNEY_END, hero, fatigue=hero.fatigue)
    contributions = ctx.bus(hero.id).collect(Hook.ON_JOURNEY_END, hook_ctx)
    relief = sum(c.delta for c in contributions if isinstance(c, NumericContribution))
    ctx.consume(hero.id, contributions, hook_ctx)
    return max(0, relief)


# -- how long it took --------------------------------------------------------------------


def journey_days(journey: Journey) -> int:
    """How many days the journey took (``10.7``).

    Computed only if the group wants to know — nothing else in the rules reads it.

    One day per hex, plus one more for each hex of hard terrain. A forced march replaces the
    base rate with one day per two hexes, keeping the hard-terrain surcharge; a wholly
    mounted Company halves the total, rounding up. ``10.7``'s code block applies those two
    in an order that loses the halving whenever both are declared, and its prose says
    plainly to apply the halving afterwards — the prose wins, and
    :func:`journey_day_warnings` flags the combination for the Loremaster as ``10.7`` asks.
    """
    hard = sum(1 for hex_ in journey.path if hex_.hard)
    # A forced march replaces the base rate of one day per hex; the hard-terrain surcharge
    # rides on either rate.
    hexes = ceil(len(journey.path) / 2) if journey.forced_march else len(journey.path)
    days = hexes + hard
    if journey.mounted:
        days = ceil(days / 2)
    return max(0, days + journey.day_adjustments)


def journey_day_warnings(journey: Journey) -> list[Warning_]:
    """``10.7``'s flag: mounted and forced march together need a ruling, not a formula."""
    if not (journey.mounted and journey.forced_march):
        return []
    return [
        Warning_(
            code="mounted_forced_march",
            message=(
                "the Company is both mounted and forced-marching; 10.7 does not combine the "
                "two in an obvious way, so the halving is applied after the forced-march "
                "rate and the result wants the Loremaster's adjudication"
            ),
        )
    ]


def forced_march_fatigue(journey: Journey) -> int:
    """Fatigue a forced march costs each hero (``10.7``): one point per day of it."""
    return journey_days(journey) if journey.forced_march else 0


def residual_fatigue(relief: Iterable[FatigueRelief]) -> Mapping[HeroId, int]:
    """What each hero carries off the road (``10.6`` step 3).

    Shed at one point per prolonged rest taken in a sheltered, safe refuge — which is
    ``resources.apply_rest``'s business, and already is: ``07.6`` sheds a point only when a
    prolonged rest is ``sheltered``, and the road never is.
    """
    return {entry.hero: entry.residual for entry in relief}
