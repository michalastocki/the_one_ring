"""The event record (``17.4``) and its kind registry.

Every state change a rule makes is described by one :class:`Event`. The log that orders,
timestamps and replays them lives at L5 (``tor.session``); the record itself must live
below ``tor.rules``, because a subsystem may not import the shell.

.. note::
   The spec is internally inconsistent on one point, and this module resolves it
   deliberately. ``07.1`` gives every ``tor.rules.resources`` function the return type
   ``list[Event]`` and ``01.4`` types every ``apply_`` the same way, while ``17.4``
   defines ``Event`` in ``tor.session`` — L5, above the L4 subsystems that ``01.1``
   forbids from importing it. Both cannot hold. We keep the ``07.1`` signature, the more
   specific and more numerous statement, and lift the record into ``tor.events``: one
   layer above ``tor.rolls``, because an Event carries the :class:`RollResult` values that
   produced it, and below ``tor.content`` and the subsystems.

   ``17.4``'s ``seq`` and ``timestamp`` stay, defaulting to ``None``. They are *log
   coordinates*, not properties of the change: a pure rule function cannot know where its
   event will be written, so ``EventLog.append`` assigns them through :meth:`Event.stamped`
   when the event enters the log. That direction is also the better design — an event that
   had to know its own sequence number could not be produced by a function that has not yet
   been told there is a log.

The alternative resolution — rules return only Outcome objects and the session layer
synthesises Events from them — was rejected. To synthesise an event, L5 must know *what*
changed; it would either re-derive that (duplicating the rule logic ``01.3``'s DRY
catalogue exists to prevent) or receive a full change description, which is an Event under
another name. ``tor.rules.resources.recompute_conditions`` is the clean case: only it knows
a condition flag flipped, and ``03.8`` names emitting that event as its job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from tor.model.ids import AdversaryInstanceId, HeroId
from tor.rolls import RollResult

__all__ = ["Event", "EventKind"]


class EventKind(StrEnum):
    """What happened.

    ``20-identifiers.md`` fixes no EventKind registry, so this enum is engine-coined and
    grows as each subsystem lands. Values are ``snake_case`` per ``20.11``, because they
    reach the save file and are therefore permanent once published. Note that ``07.5`` and
    ``07.7`` name two of these in prose as ``ConditionChanged`` and ``WoundHealed``;
    ``20.11``'s convention governs anything a save file keys on, and wins.
    """

    # -- resources (``07``) -------------------------------------------------------------
    ENDURANCE_CHANGED = "endurance_changed"
    HOPE_CHANGED = "hope_changed"
    FATIGUE_CHANGED = "fatigue_changed"
    TREASURE_CHANGED = "treasure_changed"
    TREASURE_MOVED = "treasure_moved"
    CONDITION_CHANGED = "condition_changed"
    HERO_UNCONSCIOUS = "hero_unconscious"
    HERO_DYING = "hero_dying"
    WOUND_HEALED = "wound_healed"
    RESTED = "rested"
    FELLOWSHIP_SPENT = "fellowship_spent"


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """One state change (``17.4``).

    Keyword-only, so ``17.4``'s field order survives the two log coordinates carrying
    defaults. Construct by name; never positionally.

    ``gm_only`` marks information the players must not see — an item's secret lifting
    condition (``13.8``), a council's Resistance before it is revealed, an adversary's
    remaining drive. The persistence layer and any UI must honour it (``17.5`` req. 4).
    """

    seq: int | None = None
    timestamp: tuple[int, int] | None = None
    kind: EventKind
    actor: HeroId | AdversaryInstanceId | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    rolls: tuple[RollResult, ...] = ()
    gm_only: bool = False

    def stamped(self, *, seq: int, timestamp: tuple[int, int]) -> Event:
        """The copy an ``EventLog`` writes: the one place coordinates are assigned."""
        return replace(self, seq=seq, timestamp=timestamp)
