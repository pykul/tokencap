"""Tests for tokencap.core.policy dataclasses."""

from __future__ import annotations

import pytest

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.policy import Policy


class TestThreshold:
    """Tests for Threshold validation."""

    def test_rejects_zero(self) -> None:
        """at_pct=0.0 raises ValueError."""
        with pytest.raises(ValueError, match="must be in"):
            make_threshold(at_pct=0.0)

    def test_rejects_above_one(self) -> None:
        """at_pct=1.5 raises ValueError."""
        with pytest.raises(ValueError, match="must be in"):
            make_threshold(at_pct=1.5)

    def test_accepts_one(self) -> None:
        """at_pct=1.0 is valid."""
        t = make_threshold(at_pct=1.0)
        assert t.at_pct == 1.0

    def test_accepts_small_value(self) -> None:
        """at_pct=0.01 is valid."""
        t = make_threshold(at_pct=0.01)
        assert t.at_pct == 0.01


class TestDimensionPolicy:
    """Tests for DimensionPolicy."""

    def test_sorts_thresholds(self) -> None:
        """Thresholds are sorted by at_pct on construction."""
        dp = make_dimension_policy(
            thresholds=[
                make_threshold(at_pct=1.0, actions=[make_action(kind="BLOCK")]),
                make_threshold(at_pct=0.5, actions=[make_action(kind="WARN")]),
                make_threshold(at_pct=0.8, actions=[make_action(kind="DEGRADE", degrade_to="x")]),
            ]
        )
        pcts = [t.at_pct for t in dp.thresholds]
        assert pcts == [0.5, 0.8, 1.0]


class TestPolicy:
    """Tests for Policy."""

    def test_name_defaults_to_default(self) -> None:
        """Policy() without name defaults to 'default'."""
        p = Policy(dimensions={"session": make_dimension_policy()})
        assert p.name == "default"

    def test_factory_name_defaults_to_test(self) -> None:
        """make_policy() defaults name to 'test'."""
        p = make_policy()
        assert p.name == "test"

    def test_custom_name(self) -> None:
        """Policy name can be overridden."""
        p = make_policy(name="production")
        assert p.name == "production"


class TestAction:
    """Tests for Action."""

    def test_valid_kinds(self) -> None:
        """All valid kinds construct without error."""
        for kind in ("WARN", "BLOCK", "DEGRADE", "WEBHOOK"):
            a = make_action(kind=kind)  # type: ignore[arg-type]
            assert a.kind == kind

    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        a = make_action()
        assert a.webhook_url is None
        assert a.degrade_to is None
        assert a.callback is None
