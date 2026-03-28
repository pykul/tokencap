"""Tests for tokencap.telemetry.otel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tokencap.core.types import BudgetKey, BudgetState, TokenUsage
from tokencap.telemetry.otel import OtelEmitter


def _sample_kwargs() -> dict[str, object]:
    """Build sample emit kwargs."""
    key = BudgetKey("session", "test-id")
    state = BudgetState(key=key, limit=1000, used=500, remaining=500, pct_used=0.5)
    return {
        "estimated": 100,
        "actual": TokenUsage(input_tokens=60, output_tokens=40),
        "original_model": "claude-sonnet-4-6",
        "actual_model": "claude-sonnet-4-6",
        "states": {"session": state},
    }


class TestOtelEmitterNoOtel:
    """Tests when opentelemetry is not installed."""

    def test_noop_when_not_installed(self) -> None:
        """emit() is a no-op when OTEL_AVAILABLE is False."""
        with patch("tokencap.telemetry.otel.OTEL_AVAILABLE", False):
            emitter = OtelEmitter()
            # Should not raise
            emitter.emit(**_sample_kwargs())


class TestOtelEmitterWithOtel:
    """Tests when opentelemetry is mocked as installed."""

    def test_metrics_emitted(self) -> None:
        """Correct metrics are created when OTEL is available."""
        mock_meter = MagicMock()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
            return_value=mock_span
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with (
            patch("tokencap.telemetry.otel.OTEL_AVAILABLE", True),
            patch("tokencap.telemetry.otel.metrics", create=True) as mock_metrics,
            patch("tokencap.telemetry.otel.trace", create=True) as mock_trace,
        ):
            mock_metrics.get_meter.return_value = mock_meter
            mock_trace.get_tracer.return_value = mock_tracer

            emitter = OtelEmitter()
            emitter.emit(**_sample_kwargs())

            # Verify meter was used to create instruments
            assert mock_meter.create_counter.call_count >= 1
            assert mock_meter.create_up_down_counter.call_count >= 1

    def test_failure_does_not_propagate(self) -> None:
        """OTEL failure inside emit() does not raise."""
        with patch("tokencap.telemetry.otel.OTEL_AVAILABLE", True):
            emitter = OtelEmitter()
            # Force _emit_inner to raise
            emitter._emit_inner = MagicMock(side_effect=RuntimeError("otel broke"))  # type: ignore[method-assign]
            # Should not raise
            emitter.emit(**_sample_kwargs())


class TestOtelDisabled:
    """Tests when otel_enabled is False on Guard."""

    def test_guard_uses_noop_when_disabled(self) -> None:
        """Guard with otel_enabled=False uses _NoopTelemetry."""
        from tests.conftest import make_dimension_policy, make_policy
        from tokencap.core.guard import Guard, _NoopTelemetry

        backend = MagicMock()
        policy = make_policy(dimensions={"session": make_dimension_policy()})
        guard = Guard(policy=policy, backend=backend, otel_enabled=False, quiet=True)
        assert isinstance(guard.telemetry, _NoopTelemetry)
