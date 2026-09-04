"""The declarative op language (``05.6``).

A tiny vocabulary shared by journey events, revelation episodes and sources of injury. It
exists so that adding a supplement's new journey-event table requires no code:

.. code-block:: json

    {"match": 1, "value": {"event": "despair", "fatigue": 2,
                           "on_failure": [{"op": "shadow", "who": "company",
                                           "points": 1, "source": "dread"}]}}

This module *parses and validates* ops; applying them belongs to the subsystem that owns
the state being changed, which is what keeps journey from importing shadow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tor.errors import ContentError

__all__ = ["Op", "OpKind", "Who", "parse_ops"]


class OpKind(StrEnum):
    WOUND = "wound"
    SHADOW = "shadow"
    HOPE = "hope"
    ENDURANCE = "endurance"
    FATIGUE = "fatigue"
    JOURNEY_DAYS = "journey_days"
    SUPPRESS_FATIGUE = "suppress_fatigue"
    CONDITION = "condition"
    NARRATIVE = "narrative"
    #: Used by Revelation episodes (``14.6``).
    COUNCIL_RESISTANCE_STEP = "council_resistance_step"
    GRANT_FELL_ABILITY = "grant_fell_ability"
    BONUS_DRIVE_POOL = "bonus_drive_pool"


class Who(StrEnum):
    """Whom an op falls on.

    ``target`` is the hero who made the roll — and, where relevant, the companion who
    supported it (``10.4.4``).
    """

    TARGET = "target"
    COMPANY = "company"
    ACTOR = "actor"
    CHOSEN = "chosen"


#: Which fields each op accepts beyond ``op`` itself.
_FIELDS: Mapping[OpKind, frozenset[str]] = {
    OpKind.WOUND: frozenset({"who"}),
    OpKind.SHADOW: frozenset({"who", "points", "source"}),
    OpKind.HOPE: frozenset({"who", "points"}),
    OpKind.ENDURANCE: frozenset({"who", "points"}),
    OpKind.FATIGUE: frozenset({"who", "points"}),
    OpKind.JOURNEY_DAYS: frozenset({"delta"}),
    OpKind.SUPPRESS_FATIGUE: frozenset(),
    OpKind.CONDITION: frozenset({"who", "condition", "value"}),
    OpKind.NARRATIVE: frozenset({"tag"}),
    OpKind.COUNCIL_RESISTANCE_STEP: frozenset({"steps"}),
    OpKind.GRANT_FELL_ABILITY: frozenset({"ability", "params"}),
    OpKind.BONUS_DRIVE_POOL: frozenset({"dice"}),
}


@dataclass(frozen=True, slots=True)
class Op:
    """One parsed instruction."""

    kind: OpKind
    who: Who = Who.TARGET
    points: int = 0
    delta: int = 0
    source: str | None = None
    condition: str | None = None
    value: bool = True
    tag: str | None = None
    steps: int = 0
    ability: str | None = None
    dice: int = 0
    params: Mapping[str, Any] | None = None


def parse_ops(
    raw: Sequence[Mapping[str, Any]],
    *,
    entity_id: str = "",
    pointer: str = "",
) -> tuple[Op, ...]:
    """Parse an ``on_failure`` / ``on_success`` list, rejecting anything unrecognised."""
    parsed: list[Op] = []
    for index, entry in enumerate(raw):
        at = f"{pointer}/{index}"
        if "op" not in entry:
            raise ContentError("an op needs an 'op' field", pointer=at, entity_id=entity_id)
        try:
            kind = OpKind(entry["op"])
        except ValueError as exc:
            raise ContentError(
                f"unknown op {entry['op']!r}; known: {sorted(k.value for k in OpKind)}",
                pointer=at,
                entity_id=entity_id,
            ) from exc

        if stray := set(entry) - {"op"} - _FIELDS[kind]:
            raise ContentError(
                f"op {kind.value!r} does not take {sorted(stray)}",
                pointer=at,
                entity_id=entity_id,
            )

        who = Who.TARGET
        if "who" in entry:
            try:
                who = Who(entry["who"])
            except ValueError as exc:
                raise ContentError(
                    f"unknown 'who' {entry['who']!r}", pointer=at, entity_id=entity_id
                ) from exc

        parsed.append(
            Op(
                kind=kind,
                who=who,
                points=int(entry.get("points", 0)),
                delta=int(entry.get("delta", 0)),
                source=entry.get("source"),
                condition=entry.get("condition"),
                value=bool(entry.get("value", True)),
                tag=entry.get("tag"),
                steps=int(entry.get("steps", 0)),
                ability=entry.get("ability"),
                dice=int(entry.get("dice", 0)),
                params=entry.get("params"),
            )
        )
    return tuple(parsed)
