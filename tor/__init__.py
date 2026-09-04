"""A rules engine for The One Ring (2nd edition).

The specification in ``00-README.md``..``20-identifiers.md`` is normative; where this code
and the spec disagree, the spec is right.

The engine ships zero game data. Cultures, virtues, weapons, adversaries and every other
table live in JSON content packs under ``content/``, transcribed by the implementer from
their own licensed copy of the rulebook.
"""

from __future__ import annotations

__version__ = "0.1.0"
