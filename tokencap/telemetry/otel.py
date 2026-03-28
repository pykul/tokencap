"""OTEL telemetry emission for tokencap.

Optional. No-ops silently if opentelemetry-api is not installed.
A telemetry failure must never surface to user code. See CLAUDE.md.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from opentelemetry import metrics, trace

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

_log = logging.getLogger("tokencap.telemetry")


class OtelEmitter:
    """Emits OpenTelemetry metrics and spans after each LLM call.

    When opentelemetry-api is not installed, all methods are no-ops.
    When it is installed, metrics and spans are emitted using the
    globally configured provider. Failures are logged at WARNING
    and never propagated.
    """

    def __init__(self) -> None:
        """Initialise meters and tracers if OTEL is available."""
        if not OTEL_AVAILABLE:
            return
        try:
            meter = metrics.get_meter("tokencap")
            self._tokens_used = meter.create_counter(
                "tokencap.tokens.used",
                description="Total tokens used",
            )
            self._tokens_remaining = meter.create_up_down_counter(
                "tokencap.tokens.remaining",
                description="Tokens remaining in budget",
            )
            self._pct_used = meter.create_up_down_counter(
                "tokencap.budget.pct_used",
                description="Budget percentage used",
            )
            self._action_fired = meter.create_counter(
                "tokencap.policy.action_fired",
                description="Policy actions fired",
            )
            self._tracer = trace.get_tracer("tokencap")
        except Exception:
            _log.warning("Failed to initialise OTEL meters", exc_info=True)

    def emit(self, **kwargs: Any) -> None:
        """Emit metrics and span attributes for a completed call.

        Accepts: estimated, actual, original_model, actual_model, states.
        Never raises.
        """
        if not OTEL_AVAILABLE:
            return
        try:
            self._emit_inner(**kwargs)
        except Exception:
            _log.warning("OTEL emission failed", exc_info=True)

    def _emit_inner(self, **kwargs: Any) -> None:
        """Internal emission logic. May raise — caller catches."""
        actual = kwargs.get("actual")
        estimated = kwargs.get("estimated", 0)
        original_model = kwargs.get("original_model", "")
        actual_model = kwargs.get("actual_model", "")
        states = kwargs.get("states", {})

        # Emit token counter
        if actual is not None:
            total = actual.total
            for dim in states:
                self._tokens_used.add(
                    total,
                    {"model": actual_model, "dimension": dim},
                )

        # Emit per-dimension gauges
        for dim, state in states.items():
            attrs = {"dimension": dim, "identifier": state.key.identifier}
            self._tokens_remaining.add(state.remaining, attrs)
            self._pct_used.add(int(state.pct_used * 100), attrs)

        # Emit span
        with self._tracer.start_as_current_span("tokencap.call") as span:
            span.set_attribute("tokencap.model.original", original_model)
            span.set_attribute("tokencap.model.actual", actual_model)
            span.set_attribute("tokencap.tokens.estimated", estimated)
            if actual is not None:
                span.set_attribute("tokencap.tokens.actual", actual.total)
                span.set_attribute("tokencap.tokens.delta", actual.total - estimated)
            for dim, state in states.items():
                span.set_attribute(f"tokencap.dim.{dim}.pct_used", state.pct_used)
