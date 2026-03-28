"""Tests for tokencap.status.api."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.conftest import make_action, make_dimension_policy, make_policy, make_threshold
from tokencap.core.guard import Guard
from tokencap.core.types import BudgetKey, BudgetState
from tokencap.status.api import StatusResponse, get_status


class TestGetStatus:
    """Tests for get_status()."""

    def test_returns_correct_fields(self, mock_backend: MagicMock) -> None:
        """get_status returns StatusResponse with correct fields."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = get_status(guard)
        assert isinstance(status, StatusResponse)
        assert status.active_policy == "test"
        assert "session" in status.dimensions
        assert len(status.timestamp) > 0

    def test_next_threshold_nearest_unfired(self, mock_backend: MagicMock) -> None:
        """Returns the nearest unfired threshold."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[
                make_threshold(at_pct=0.5, actions=[make_action(kind="WARN")]),
                make_threshold(at_pct=0.8, actions=[make_action(kind="WARN")]),
            ],
        )})
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=400, remaining=600, pct_used=0.4        )
        mock_backend.get_states.return_value = {"session": state}
        # First threshold not fired
        mock_backend.is_threshold_fired.return_value = False
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = get_status(guard)
        assert status.next_threshold is not None
        assert status.next_threshold.at_pct == 0.5
        assert status.next_threshold.dimension == "session"

    def test_next_threshold_skips_fired(self, mock_backend: MagicMock) -> None:
        """Skips fired thresholds, returns next unfired."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[
                make_threshold(at_pct=0.5, actions=[make_action(kind="WARN")]),
                make_threshold(at_pct=0.8, actions=[make_action(kind="WARN")]),
            ],
        )})
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        mock_backend.get_states.return_value = {"session": state}
        # First threshold fired, second not
        mock_backend.is_threshold_fired.side_effect = lambda k, pct: pct == 0.5
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = get_status(guard)
        assert status.next_threshold is not None
        assert status.next_threshold.at_pct == 0.8

    def test_next_threshold_none_when_no_thresholds(self, mock_backend: MagicMock) -> None:
        """No thresholds configured returns None."""
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = get_status(guard)
        assert status.next_threshold is None

    def test_next_threshold_none_when_all_fired(self, mock_backend: MagicMock) -> None:
        """All thresholds fired returns None."""
        policy = make_policy(dimensions={"session": make_dimension_policy(
            limit=1000,
            thresholds=[
                make_threshold(at_pct=0.5, actions=[make_action(kind="WARN")]),
            ],
        )})
        key = BudgetKey("session", "test-id")
        state = BudgetState(
            key=key, limit=1000, used=600, remaining=400, pct_used=0.6        )
        mock_backend.get_states.return_value = {"session": state}
        mock_backend.is_threshold_fired.return_value = True
        guard = Guard(policy=policy, identifiers={"session": "test-id"},
                      backend=mock_backend, quiet=True)
        status = get_status(guard)
        assert status.next_threshold is None

    def test_next_threshold_across_dimensions(self, mock_backend: MagicMock) -> None:
        """Returns threshold with smallest gap across all dimensions."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=1000,
                thresholds=[make_threshold(at_pct=0.8, actions=[make_action(kind="WARN")])],
            ),
            "tenant": make_dimension_policy(
                limit=5000,
                thresholds=[make_threshold(at_pct=0.9, actions=[make_action(kind="WARN")])],
            ),
        })
        session_state = BudgetState(
            key=BudgetKey("session", "s-id"), limit=1000, used=700,
            remaining=300, pct_used=0.7,
        )
        tenant_state = BudgetState(
            key=BudgetKey("tenant", "t-id"), limit=5000, used=4400,
            remaining=600, pct_used=0.88,
        )
        mock_backend.get_states.return_value = {
            "session": session_state, "tenant": tenant_state,
        }
        mock_backend.is_threshold_fired.return_value = False
        guard = Guard(
            policy=policy,
            identifiers={"session": "s-id", "tenant": "t-id"},
            backend=mock_backend, quiet=True,
        )
        status = get_status(guard)
        assert status.next_threshold is not None
        # tenant at 0.88, threshold at 0.9 -> gap 0.02
        # session at 0.70, threshold at 0.8 -> gap 0.10
        assert status.next_threshold.dimension == "tenant"
        assert status.next_threshold.at_pct == 0.9
