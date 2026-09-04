"""`tor.tables` — the generic die-result table (spec 02.2, 13.6)."""

from __future__ import annotations

import pytest

from tor.dice import FeatFace, ScriptedRandomness
from tor.errors import ContentError
from tor.tables import DieKind, LookupTable, TableRow, rows_from_json


def feat_table(rows: list[TableRow[str]], table_id: str = "t") -> LookupTable[str]:
    return LookupTable(id=table_id, die=DieKind.FEAT, rows=tuple(rows))


# The shape of the real Wound Severity table (08.8): an icon row each side of a range.
WOUND_SEVERITY_ROWS = [
    TableRow(FeatFace.RUNE, "moderate"),
    TableRow((1, 10), "severe"),
    TableRow(FeatFace.EYE, "grievous"),
]


class TestCoverageValidation:
    def test_a_complete_feat_table_constructs(self) -> None:
        assert feat_table(WOUND_SEVERITY_ROWS).lookup(7) == "severe"

    def test_a_gap_in_the_numeric_domain_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="does not cover its whole key domain"):
            feat_table(
                [
                    TableRow(FeatFace.RUNE, "r"),
                    TableRow((1, 9), "n"),  # 10 is missing
                    TableRow(FeatFace.EYE, "e"),
                ]
            )

    def test_a_missing_icon_row_is_rejected(self) -> None:
        # A Feat table must answer for both icon faces, not just the numbers.
        with pytest.raises(ContentError, match="missing"):
            feat_table([TableRow((1, 10), "n"), TableRow(FeatFace.EYE, "e")])

    def test_a_key_outside_the_domain_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="outside"):
            feat_table([*WOUND_SEVERITY_ROWS, TableRow(11, "impossible")])

    def test_a_success_table_covers_one_to_six(self) -> None:
        table = LookupTable(
            id="event_target",
            die=DieKind.SUCCESS,
            rows=(
                TableRow((1, 2), "scouts"),
                TableRow((3, 4), "lookouts"),
                TableRow((5, 6), "hunters"),
            ),
        )
        assert table.lookup(3) == "lookouts"

    def test_a_success_table_may_not_carry_icon_rows(self) -> None:
        with pytest.raises(ContentError, match="outside"):
            LookupTable(
                id="bad",
                die=DieKind.SUCCESS,
                rows=(TableRow((1, 6), "x"), TableRow(FeatFace.EYE, "e")),
            )

    def test_an_unknown_die_kind_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="unknown die kind"):
            LookupTable(id="bad", die="d20", rows=(TableRow((1, 10), "x"),))


class TestResolutionOrder:
    def test_icon_rows_win_over_ranges(self) -> None:
        # The eye is not a number, so the [1, 10] range must never claim it.
        table = feat_table(WOUND_SEVERITY_ROWS)
        assert table.lookup(FeatFace.EYE) == "grievous"
        assert table.lookup(FeatFace.RUNE) == "moderate"

    def test_exact_rows_win_over_ranges(self) -> None:
        table = feat_table(
            [
                TableRow(FeatFace.RUNE, "rune"),
                TableRow(FeatFace.EYE, "eye"),
                TableRow((1, 10), "range"),
                TableRow(7, "exact"),
            ]
        )
        assert table.lookup(7) == "exact"
        assert table.lookup(6) == "range"


class TestShift:
    """The Ponder Storied and Figured Maps +1 (10.4.2), implemented once, here."""

    def test_shift_moves_a_numeric_key(self) -> None:
        table = feat_table(
            [
                TableRow(FeatFace.RUNE, "r"),
                TableRow(FeatFace.EYE, "e"),
                TableRow(1, "one"),
                TableRow((2, 10), "rest"),
            ]
        )
        assert table.lookup(1) == "one"
        assert table.lookup(1, shift=1) == "rest"

    def test_shift_leaves_icon_faces_untouched(self) -> None:
        table = feat_table(WOUND_SEVERITY_ROWS)
        assert table.lookup(FeatFace.EYE, shift=1) == "grievous"
        assert table.lookup(FeatFace.RUNE, shift=1) == "moderate"

    def test_shift_clamps_at_the_top_of_the_numeric_range(self) -> None:
        table = feat_table(
            [
                TableRow(FeatFace.RUNE, "r"),
                TableRow(FeatFace.EYE, "e"),
                TableRow((1, 9), "low"),
                TableRow(10, "ten"),
            ]
        )
        assert table.lookup(10, shift=3) == "ten"

    def test_shift_clamps_at_the_bottom(self) -> None:
        table = feat_table(
            [
                TableRow(FeatFace.RUNE, "r"),
                TableRow(FeatFace.EYE, "e"),
                TableRow(1, "one"),
                TableRow((2, 10), "rest"),
            ]
        )
        assert table.lookup(2, shift=-5) == "one"


class TestRolling:
    def test_roll_uses_the_feat_die_for_a_feat_table(self) -> None:
        rng = ScriptedRandomness(feats=[FeatFace.EYE])
        assert feat_table(WOUND_SEVERITY_ROWS).roll(rng) == "grievous"
        assert rng.exhausted

    def test_roll_uses_the_success_die_for_a_success_table(self) -> None:
        table = LookupTable(
            id="t", die=DieKind.SUCCESS, rows=(TableRow((1, 3), "low"), TableRow((4, 6), "high"))
        )
        rng = ScriptedRandomness(successes=[5])
        assert table.roll(rng) == "high"
        assert rng.exhausted


class TestRerollChains:
    """13.6 — a material row that chains into a second roll."""

    def test_resolve_follows_a_reroll_to_a_terminal_value(self) -> None:
        table: LookupTable[object] = LookupTable(
            id="material",
            die=DieKind.SUCCESS,
            rows=(
                TableRow((1, 4), "gold"),
                TableRow(
                    (5, 6),
                    {
                        "reroll": [
                            {"match": [1, 2], "value": "adamant"},
                            {"match": [3, 4], "value": "white_gem"},
                            {"match": [5, 6], "value": "clear_crystal"},
                        ]
                    },
                ),
            ),
        )
        rng = ScriptedRandomness(successes=[5, 3])
        assert table.resolve(rng) == "white_gem"
        assert rng.exhausted

    def test_resolve_passes_a_plain_value_straight_through(self) -> None:
        rng = ScriptedRandomness(feats=[4])
        assert feat_table(WOUND_SEVERITY_ROWS).resolve(rng) == "severe"
        assert rng.exhausted


class TestRowsFromJson:
    def test_parses_every_match_shape(self) -> None:
        rows = rows_from_json(
            [
                {"match": "eye", "value": "e"},
                {"match": "rune", "value": "r"},
                {"match": 7, "value": "seven"},
                {"match": [1, 6], "value": "low"},
            ],
            table_id="t",
        )
        assert [r.match for r in rows] == [FeatFace.EYE, FeatFace.RUNE, 7, (1, 6)]

    def test_a_row_missing_value_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="'match' and 'value'"):
            rows_from_json([{"match": 1}], table_id="t")

    def test_an_inverted_range_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="inverted"):
            rows_from_json([{"match": [6, 2], "value": "x"}], table_id="t")

    def test_an_unparseable_match_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="invalid row match"):
            rows_from_json([{"match": "sometimes", "value": "x"}], table_id="t")

    def test_a_boolean_match_is_rejected(self) -> None:
        # bool is a subclass of int; without an explicit guard `true` would parse as 1.
        with pytest.raises(ContentError, match="invalid row match"):
            rows_from_json([{"match": True, "value": "x"}], table_id="t")

    def test_the_error_points_at_the_offending_row(self) -> None:
        with pytest.raises(ContentError) as excinfo:
            rows_from_json(
                [{"match": 1, "value": "a"}, {"match": "nope", "value": "b"}], table_id="t"
            )
        assert excinfo.value.pointer == "/rows/1"
        assert excinfo.value.entity_id == "t"
