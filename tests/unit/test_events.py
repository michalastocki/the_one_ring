"""`tor.events` — the event record and its kind registry (spec 17.4).

The record lives below `tor.rules` rather than in `tor.session`, because 07.1 types every
resources function `-> list[Event]` and 01.1 forbids a subsystem importing the shell. The
two log coordinates are what that costs, and what these tests pin.
"""

from __future__ import annotations

import dataclasses

import pytest

from tor.events import Event, EventKind
from tor.model.ids import HeroId


class TestEventKind:
    """20.11 — a value that reaches the save file is a permanent, snake_case id."""

    def test_every_value_is_snake_case(self) -> None:
        for kind in EventKind:
            assert kind.value == kind.value.lower()
            assert kind.value.replace("_", "").isalnum(), kind.value
            assert not kind.value.startswith("_")

    def test_values_are_unique(self) -> None:
        values = [kind.value for kind in EventKind]
        assert len(values) == len(set(values))

    def test_the_name_matches_the_value(self) -> None:
        # A drifting pair is how a save file ends up keyed on something nobody meant.
        for kind in EventKind:
            assert kind.name.lower() == kind.value


class TestEvent:
    """17.4's record, with seq and timestamp defaulted because a rule cannot know them."""

    def test_a_rule_may_build_one_without_log_coordinates(self) -> None:
        event = Event(kind=EventKind.HOPE_CHANGED, actor=HeroId("bilbo"))
        assert event.seq is None
        assert event.timestamp is None
        assert event.payload == {}
        assert event.rolls == ()
        assert event.gm_only is False

    def test_stamping_assigns_both_coordinates(self) -> None:
        event = Event(kind=EventKind.RESTED, actor=HeroId("bilbo"))
        stamped = event.stamped(seq=7, timestamp=(3, 120))
        assert stamped.seq == 7
        assert stamped.timestamp == (3, 120)

    def test_stamping_leaves_the_original_untouched(self) -> None:
        # Frozen, so a log that stamps an event cannot mutate what the rule returned.
        event = Event(kind=EventKind.RESTED)
        event.stamped(seq=1, timestamp=(0, 0))
        assert event.seq is None

    def test_stamping_preserves_every_other_field(self) -> None:
        event = Event(
            kind=EventKind.CONDITION_CHANGED,
            actor=HeroId("bilbo"),
            payload={"condition": "weary", "active": True},
            gm_only=True,
        )
        stamped = event.stamped(seq=2, timestamp=(1, 30))
        assert stamped.kind is event.kind
        assert stamped.actor == event.actor
        assert stamped.payload == event.payload
        assert stamped.gm_only is True

    def test_it_is_frozen(self) -> None:
        event = Event(kind=EventKind.RESTED)
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.seq = 3  # type: ignore[misc]

    def test_it_is_keyword_only(self) -> None:
        # kw_only keeps 17.4's field order readable despite the defaults; constructing
        # positionally must stay impossible so the order can change without silent breakage.
        with pytest.raises(TypeError):
            Event(EventKind.RESTED)  # type: ignore[misc]

    def test_an_actor_is_optional(self) -> None:
        # A Fellowship spend belongs to the Company, not to any one hero.
        assert Event(kind=EventKind.FELLOWSHIP_SPENT).actor is None
