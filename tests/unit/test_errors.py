"""`tor.errors` — the three error families (spec 18.1, 18.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tor.errors import (
    ContentError,
    RuleViolation,
    RuleViolationGroup,
    StateError,
    TorError,
    Warning_,
)


class TestFamilies:
    @pytest.mark.parametrize("cls", [ContentError, RuleViolation, StateError])
    def test_every_family_descends_from_tor_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, TorError)


class TestContentError:
    def test_carries_file_pointer_and_id(self) -> None:
        err = ContentError(
            "unknown ability",
            file=Path("cultures.json"),
            pointer="/cultures/0/skills",
            entity_id="x",
        )
        assert err.file == Path("cultures.json")
        assert err.pointer == "/cultures/0/skills"
        assert err.entity_id == "x"
        # An author must be able to find the offending node from the message alone.
        assert "cultures.json" in str(err)
        assert "/cultures/0/skills" in str(err)
        assert "x" in str(err)

    def test_reads_cleanly_with_no_location(self) -> None:
        assert str(ContentError("something is wrong")) == "something is wrong"


class TestRuleViolation:
    def test_names_the_mechanic(self) -> None:
        err = RuleViolation("only one Guide is permitted", rule_reference="journey_roles")
        assert err.rule_reference == "journey_roles"
        assert str(err).startswith("journey_roles: ")

    def test_carries_a_suggestion_when_there_is_an_obvious_remedy(self) -> None:
        err = RuleViolation(
            "cannot take Rearward",
            rule_reference="rearward_stance_requirements",
            suggestion="two other heroes must be in a close combat stance",
        )
        assert "two other heroes" in str(err)


class TestRuleViolationGroup:
    def test_reports_every_breach_not_just_the_first(self) -> None:
        # 13.7.3 / 15.6: validating a Famous item or a set of undertakings must collect.
        violations = [
            RuleViolation("no Enchanted Reward", rule_reference="famous_item"),
            RuleViolation("four qualities", rule_reference="famous_item"),
        ]
        group = RuleViolationGroup(violations, rule_reference="famous_item")
        assert len(group.violations) == 2
        assert "no Enchanted Reward" in str(group)
        assert "four qualities" in str(group)

    def test_is_itself_a_rule_violation(self) -> None:
        group = RuleViolationGroup(
            [RuleViolation("a", rule_reference="r")], rule_reference="famous_item"
        )
        assert isinstance(group, RuleViolation)

    def test_an_empty_group_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="at least one violation"):
            RuleViolationGroup([], rule_reference="famous_item")


class TestWarning:
    def test_is_data_not_an_exception(self) -> None:
        # 18.2: warnings are returned alongside results, never raised.
        warning = Warning_(code="age_outside_range", message="52 is past typical retirement")
        assert not isinstance(warning, Exception)
        assert warning.code == "age_outside_range"
