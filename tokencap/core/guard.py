"""Guard: stateless config holder and factory for wrapped clients.

Guard holds policy, identifiers, backend, and telemetry. It does not hold
provider or current_model — those are call-time state that lives on the
wrapped client. See D-042.
"""

from __future__ import annotations

import sys
import uuid
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from tokencap.status.api import StatusResponse

from tokencap.backends.protocol import Backend
from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.exceptions import ConfigurationError
from tokencap.core.policy import Policy
from tokencap.core.types import BudgetKey


class Telemetry(Protocol):
    """Protocol for telemetry emitters (real OTEL or no-op)."""

    def emit(self, **kwargs: Any) -> None:
        """Emit telemetry data. Never raises."""
        ...


class _NoopTelemetry:
    """No-op telemetry stub. Silently discards all emit calls."""

    def emit(self, **kwargs: Any) -> None:
        """Accept and discard all telemetry data."""


class Guard:
    """Stateless config holder and factory for wrapped clients.

    Owns the backend, policy, identifiers, and telemetry references
    that interceptor/base.py needs. Does not hold provider or current_model.
    Provider is created per wrap call and stored on the wrapped client.
    """

    def __init__(
        self,
        policy: Policy,
        identifiers: dict[str, str] | None = None,
        backend: Backend | None = None,
        otel_enabled: bool = True,
        quiet: bool = False,
    ) -> None:
        """Initialise the Guard.

        Args:
            policy: Budget policy defining dimensions, limits, and thresholds.
            identifiers: Maps dimension names to runtime identifier strings.
                Dimensions not listed receive an auto-generated UUID.
            backend: Storage backend. Defaults to SQLiteBackend("tokencap.db").
            otel_enabled: Whether to emit OTEL metrics and spans.
            quiet: Suppress the startup stdout message.
        """
        self.policy = policy
        self.backend: Backend = backend or SQLiteBackend("tokencap.db")
        self._otel_enabled = otel_enabled

        # Wire telemetry: real OtelEmitter if enabled and available, else no-op
        if otel_enabled:
            try:
                from tokencap.telemetry.otel import OTEL_AVAILABLE, OtelEmitter

                if OTEL_AVAILABLE:
                    self.telemetry: Telemetry = OtelEmitter()
                else:
                    self.telemetry = _NoopTelemetry()
            except Exception:
                self.telemetry = _NoopTelemetry()
        else:
            self.telemetry = _NoopTelemetry()

        # Auto-generate UUID identifiers for dimensions not explicitly provided
        provided = identifiers or {}
        self.identifiers: dict[str, str] = {}
        for dim in policy.dimensions:
            self.identifiers[dim] = provided.get(dim, str(uuid.uuid4()))

        # Register limits in the backend
        for dim, dim_policy in policy.dimensions.items():
            self.backend.set_limit(
                BudgetKey(dimension=dim, identifier=self.identifiers[dim]),
                dim_policy.limit,
            )

        # Print startup message unless suppressed
        if not quiet:
            self._print_startup()

    def _print_startup(self) -> None:
        """Print startup message to stdout. One line per dimension."""
        backend_name = self._backend_display_name()
        for dim, dim_policy in self.policy.dimensions.items():
            ident = self.identifiers[dim]
            if dim_policy.thresholds:
                limit_str = f"limit={dim_policy.limit} tokens"
            else:
                limit_str = "(no limit set)"
            print(
                f"[tokencap] session started: {dim}={ident} "
                f"backend={backend_name} {limit_str}",
                file=sys.stdout,
            )

    def _backend_display_name(self) -> str:
        """Return a human-readable backend name for the startup message."""
        if isinstance(self.backend, SQLiteBackend):
            path = getattr(self.backend, "_path", "tokencap.db")
            return f"sqlite:{path}"
        cls_name = type(self.backend).__name__
        return cls_name.lower()

    def wrap_anthropic(self, client: Any) -> Any:
        """Wrap an Anthropic client. Returns a GuardedAnthropic.

        Creates an AnthropicProvider and passes it to the wrapper.
        Raises ConfigurationError if the anthropic SDK is not installed.
        """
        try:
            from tokencap.interceptor.anthropic import GuardedAnthropic
            from tokencap.providers.anthropic import AnthropicProvider
        except ImportError as err:
            raise ConfigurationError(
                "wrap_anthropic() requires the anthropic package. "
                "Install it with: pip install tokencap[anthropic]"
            ) from err
        provider = AnthropicProvider()
        return GuardedAnthropic(client, self, provider)

    def wrap_openai(self, client: Any) -> Any:
        """Wrap an OpenAI client. Returns a GuardedOpenAI.

        Creates an OpenAIProvider and passes it to the wrapper.
        Raises ConfigurationError if the openai SDK is not installed.
        """
        try:
            from tokencap.interceptor.openai import GuardedOpenAI
            from tokencap.providers.openai import OpenAIProvider
        except ImportError as err:
            raise ConfigurationError(
                "wrap_openai() requires the openai package. "
                "Install it with: pip install tokencap[openai]"
            ) from err
        provider = OpenAIProvider()
        return GuardedOpenAI(client, self, provider)

    def get_status(self) -> StatusResponse:
        """Return a StatusResponse snapshot of all dimensions."""
        from tokencap.status.api import get_status
        return get_status(self)

    def teardown(self) -> None:
        """Close backend connections and reset internal state."""
        self.backend.close()
