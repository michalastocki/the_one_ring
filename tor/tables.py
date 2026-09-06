"""Generic "roll a die, read a row" lookup tables (``02-core-dice-and-rolls.md`` §2.2).

Seam C of the three cross-cutting seams. The book resolves a dozen situations this way —
Wound Severity, Endurance Loss, Journey Events, Event Target, Magical Treasure, Precious
Object generation, Hoard value, Shadow Path steps. One generic table type covers all of
them, including the rows keyed by icon rather than by number.

Tables are validated **eagerly at construction**: a table that fails to cover its whole key
domain is a load-time :class:`~tor.errors.ContentError`, never a surprise mid-session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar, cast

from tor.dice import FEAT_FACES, FeatFace, FeatValue, Randomness
from tor.errors import ContentError

__all__ = [
    "FEAT_DOMAIN",
    "SUCCESS_DOMAIN",
    "DieKind",
    "LookupTable",
    "Sentinel",
    "TableRow",
]

V = TypeVar("V")

#: Every key a Feat-die table must cover: 1..10 plus both icon faces.
FEAT_DOMAIN: frozenset[FeatValue] = frozenset(FEAT_FACES)

#: Every key a Success-die table must cover.
SUCCESS_DOMAIN: frozenset[int] = frozenset(range(1, 7))

#: Guard against a content pack whose reroll rows point back at themselves.
_MAX_REROLL_DEPTH = 8


class Sentinel(StrEnum):
    """Placeholders a pack may write inside a row ``value`` (``05.6``).

    ``"$roll"`` means "the numeric die result itself" — the Wound Severity table's injury
    days and the Endurance Loss table's loss both use it. The loader converts the literal
    string into this marker so a consuming subsystem can test identity rather than sniff
    for a magic string. It is still a ``str``, so a row round-trips through JSON unchanged.
    """

    ROLL = "$roll"


def _mark_sentinels(value: object, _seen: frozenset[int] = frozenset()) -> object:
    """Rewrite the ``"$roll"`` literal to :data:`Sentinel.ROLL`, at any depth.

    ``_seen`` guards against a container that holds itself. Decoded JSON never can, but a
    reroll chain is re-parsed from values already in memory, and a hand-built cycle would
    otherwise recurse until the interpreter gave up. A cycle is returned untouched so the
    reroll depth guard is the one that reports it.
    """
    if isinstance(value, str):
        return Sentinel.ROLL if value == Sentinel.ROLL.value else value
    if id(value) in _seen:
        return value
    if isinstance(value, dict):
        seen = _seen | {id(value)}
        return {key: _mark_sentinels(item, seen) for key, item in value.items()}
    if isinstance(value, list):
        seen = _seen | {id(value)}
        return [_mark_sentinels(item, seen) for item in value]
    return value


class DieKind(StrEnum):
    """Which die a table is read with. Determines the key domain it must cover."""

    FEAT = "feat"
    SUCCESS = "success"


@dataclass(frozen=True, slots=True)
class TableRow(Generic[V]):
    """One row: an exact number, an inclusive numeric range, or an icon face."""

    match: int | tuple[int, int] | FeatFace
    value: V

    def matches(self, key: FeatValue) -> bool:
        if isinstance(self.match, FeatFace):
            return key is self.match
        if isinstance(key, FeatFace):
            return False
        if isinstance(self.match, tuple):
            low, high = self.match
            return low <= key <= high
        return key == self.match

    @property
    def keys(self) -> frozenset[FeatValue]:
        """Every key this row answers for — used for the coverage check."""
        if isinstance(self.match, FeatFace):
            return frozenset({self.match})
        if isinstance(self.match, tuple):
            low, high = self.match
            return frozenset(range(low, high + 1))
        return frozenset({self.match})


@dataclass(frozen=True, slots=True)
class LookupTable(Generic[V]):
    """A die-result table.

    Resolution order is **icon rows first** (exact face identity), then exact integers,
    then ranges. That ordering lets a table give the eye its own row while a ``[1, 10]``
    range covers the numbers, which is exactly the shape of the Wound Severity table.
    """

    id: str
    die: str
    rows: tuple[TableRow[V], ...]
    _icon_rows: tuple[TableRow[V], ...] = field(init=False, repr=False, compare=False)
    _exact_rows: tuple[TableRow[V], ...] = field(init=False, repr=False, compare=False)
    _range_rows: tuple[TableRow[V], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        icons = tuple(r for r in self.rows if isinstance(r.match, FeatFace))
        exact = tuple(r for r in self.rows if isinstance(r.match, int))
        ranges = tuple(r for r in self.rows if isinstance(r.match, tuple))
        object.__setattr__(self, "_icon_rows", icons)
        object.__setattr__(self, "_exact_rows", exact)
        object.__setattr__(self, "_range_rows", ranges)
        self._validate_coverage()

    def _validate_coverage(self) -> None:
        domain: frozenset[FeatValue]
        if self.die == DieKind.FEAT:
            domain = FEAT_DOMAIN
        elif self.die == DieKind.SUCCESS:
            domain = frozenset(SUCCESS_DOMAIN)
        else:
            raise ContentError(
                f"unknown die kind {self.die!r}; expected 'feat' or 'success'",
                entity_id=self.id,
            )

        covered: set[FeatValue] = set()
        for row in self.rows:
            covered |= row.keys

        if missing := domain - covered:
            raise ContentError(
                f"lookup table does not cover its whole key domain; missing "
                f"{sorted(missing, key=str)}",
                pointer="/rows",
                entity_id=self.id,
            )
        if stray := covered - domain:
            raise ContentError(
                f"lookup table has rows outside the {self.die} die's key domain: "
                f"{sorted(stray, key=str)}",
                pointer="/rows",
                entity_id=self.id,
            )

    def lookup(self, key: FeatValue, *, shift: int = 0) -> V:
        """Read the row for ``key``.

        ``shift`` is applied to **numeric keys only**, clamped into the die's numeric
        range; icon faces pass through untouched. That is the *Ponder Storied and Figured
        Maps* +1 (``10.4.2``), and it lives here so the journey subsystem never
        reimplements it.
        """
        if shift and not isinstance(key, FeatFace):
            high = 10 if self.die == DieKind.FEAT else 6
            key = max(1, min(high, key + shift))

        for group in (self._icon_rows, self._exact_rows, self._range_rows):
            for row in group:
                if row.matches(key):
                    return row.value

        raise ContentError(  # pragma: no cover - unreachable while coverage is validated
            f"no row matches {key!r}", entity_id=self.id
        )

    def roll(self, rng: Randomness, *, shift: int = 0) -> V:
        """Roll the table's own die and read the row."""
        key: FeatValue = rng.feat() if self.die == DieKind.FEAT else rng.success().face
        return self.lookup(key, shift=shift)

    def resolve(self, rng: Randomness, *, shift: int = 0) -> V:
        """Roll, then follow any ``reroll`` chain to a terminal value (``13.6``).

        The Precious Object generation tables chain: a material row may say "roll again to
        distinguish the sub-variety". One recursion, shared by any table that needs it, so
        the treasure subsystem does not grow its own.
        """
        return cast("V", _follow_rerolls(self.roll(rng, shift=shift), rng, table_id=self.id))


def _follow_rerolls(value: object, rng: Randomness, *, table_id: str, depth: int = 0) -> object:
    """Resolve a ``{"reroll": [...]}`` value by rolling its nested rows.

    Reroll payloads arrive from JSON, so this rung of the ladder is untyped by nature;
    :meth:`LookupTable.resolve` casts back to the table's value type at the boundary.
    """
    if depth > _MAX_REROLL_DEPTH:
        raise ContentError(
            f"reroll chain exceeded {_MAX_REROLL_DEPTH} levels; the table is probably "
            "self-referential",
            entity_id=table_id,
        )
    if not isinstance(value, dict) or "reroll" not in value:
        return value

    nested_rows = value["reroll"]
    if not isinstance(nested_rows, list):
        raise ContentError("'reroll' must be an array of rows", entity_id=table_id)

    nested: LookupTable[object] = LookupTable(
        id=f"{table_id}#reroll{depth + 1}",
        die=DieKind.SUCCESS,
        rows=rows_from_json(nested_rows, table_id=table_id),
    )
    return _follow_rerolls(nested.roll(rng), rng, table_id=table_id, depth=depth + 1)


def rows_from_json(
    raw: Sequence[dict[str, object]], *, table_id: str
) -> tuple[TableRow[object], ...]:
    """Parse the ``rows`` array of a serialised table (``05.6``).

    ``match`` is ``"eye"``/``"rune"`` for an icon, an integer for an exact face, or a
    two-element ``[low, high]`` array for an inclusive range.
    """
    parsed: list[TableRow[object]] = []
    for index, row in enumerate(raw):
        pointer = f"/rows/{index}"
        if "match" not in row or "value" not in row:
            raise ContentError(
                "a table row needs both 'match' and 'value'", pointer=pointer, entity_id=table_id
            )
        value = _mark_sentinels(row["value"])
        match raw_match := row["match"]:
            case "eye":
                parsed.append(TableRow(FeatFace.EYE, value))
            case "rune":
                parsed.append(TableRow(FeatFace.RUNE, value))
            case bool():
                raise ContentError(
                    f"invalid row match {raw_match!r}", pointer=pointer, entity_id=table_id
                )
            case int():
                parsed.append(TableRow(raw_match, value))
            case [int() as low, int() as high]:
                if low > high:
                    raise ContentError(
                        f"range [{low}, {high}] is inverted", pointer=pointer, entity_id=table_id
                    )
                parsed.append(TableRow((low, high), value))
            case _:
                raise ContentError(
                    f"invalid row match {raw_match!r}; expected an int, a [low, high] pair, "
                    "'eye' or 'rune'",
                    pointer=pointer,
                    entity_id=table_id,
                )
    return tuple(parsed)
