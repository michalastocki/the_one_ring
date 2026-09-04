"""Identifier aliases (``03-domain-model.md`` §3.1).

``NewType`` rather than bare ``str`` so a ``CultureId`` cannot be passed where an
``AbilityId`` belongs. Ids are ``snake_case``, ASCII, and stable forever — content packs
and save files key off them, so renaming one is a breaking change requiring a migration.

Canonical spellings for the ids the *engine itself* knows are fixed in
``20-identifiers.md``. Everything else is content, and its ids belong to whoever authors
the pack.
"""

from __future__ import annotations

from typing import NewType

__all__ = [
    "AbilityId",
    "AdversaryId",
    "AdversaryInstanceId",
    "CallingId",
    "CombatantId",
    "CultureId",
    "EffectId",
    "HeroId",
    "ItemId",
    "PatronId",
    "TaskId",
]

HeroId = NewType("HeroId", str)
CultureId = NewType("CultureId", str)
CallingId = NewType("CallingId", str)
#: A Skill, a Combat Proficiency, ``valour``, ``wisdom``, or the pseudo-ability ``brawling``.
AbilityId = NewType("AbilityId", str)
EffectId = NewType("EffectId", str)
ItemId = NewType("ItemId", str)
AdversaryId = NewType("AdversaryId", str)
AdversaryInstanceId = NewType("AdversaryInstanceId", str)
PatronId = NewType("PatronId", str)
#: Scopes an :class:`~tor.rolls.AttemptLedger`; discarded when the task resolves.
TaskId = NewType("TaskId", str)

CombatantId = HeroId | AdversaryInstanceId
