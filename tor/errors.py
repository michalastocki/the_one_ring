"""The error model.

Three families, and nothing else (``18-api-and-errors.md`` §18.1). Every raise in the
engine must be one of these.

**Never raise for a legal but unlucky outcome.** Failing a roll, losing a council, dying —
all are results, not errors. Catching an exception to handle a game outcome means the
design is wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ContentError",
    "RuleViolation",
    "RuleViolationGroup",
    "StateError",
    "TorError",
    "Warning_",
]


class TorError(Exception):
    """Base of every error the engine raises."""


class ContentError(TorError):
    """A pack is malformed or references a missing id.

    Raised only at load time — never during play. Carries enough to point an author
    straight at the offending node.
    """

    def __init__(
        self,
        message: str,
        *,
        file: Path | None = None,
        pointer: str = "",
        entity_id: str | None = None,
    ) -> None:
        self.file = file
        self.pointer = pointer
        self.entity_id = entity_id
        location = " ".join(
            part
            for part in (
                f"{file}" if file is not None else "",
                f"at {pointer}" if pointer else "",
                f"(id: {entity_id})" if entity_id is not None else "",
            )
            if part
        )
        super().__init__(f"{message} [{location}]" if location else message)


class RuleViolation(TorError):
    """An action is illegal by the rules of the game.

    ``rule_reference`` names the mechanic in a form that maps to the spec, so a UI can
    link to the relevant section and a player can see *why* the engine refused.
    ``suggestion`` carries the remedy where there is an obvious one.
    """

    def __init__(
        self,
        detail: str,
        *,
        rule_reference: str,
        suggestion: str | None = None,
    ) -> None:
        self.rule_reference = rule_reference
        self.detail = detail
        self.suggestion = suggestion
        message = f"{rule_reference}: {detail}"
        if suggestion:
            message = f"{message} — {suggestion}"
        super().__init__(message)


class RuleViolationGroup(RuleViolation):
    """Several breaches reported together.

    Validating a Famous item (``13.7.3``) or a set of undertakings (``15.6``) must report
    *every* breach, not just the first one it trips over.
    """

    def __init__(self, violations: Sequence[RuleViolation], *, rule_reference: str) -> None:
        if not violations:
            raise ValueError("a RuleViolationGroup needs at least one violation")
        self.violations = tuple(violations)
        detail = "; ".join(v.detail for v in self.violations)
        super().__init__(detail, rule_reference=rule_reference)


class StateError(TorError):
    """The engine was driven into an impossible state. Indicates a caller bug.

    Also raised by ``ScriptedRandomness`` when a test drains its dice queue: the engine
    asking for more dice than the test scripted is exactly such a bug.
    """


@dataclass(frozen=True, slots=True)
class Warning_:
    """A situation that is legal but probably unintended (``18.2``).

    Returned alongside results, never raised and never silently dropped. An age outside a
    culture's typical range, a Combat Proficiency no allowed weapon uses, a Calling
    re-marking an already-Favoured Skill, a journey longer than 20 hexes.
    """

    code: str
    message: str
