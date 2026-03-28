"""Guard stub for Phase 2.

Minimal implementation to make the interceptor code importable and testable.
The full Guard with public API, startup message, and wrap() is Phase 3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from tokencap.backends.protocol import Backend
from tokencap.core.policy import Policy
from tokencap.core.types import BudgetKey


@dataclass
class _NoopTelemetry:
    """No-op telemetry stub. Silently discards all emit calls."""

    def emit(self, **kwargs: Any) -> None:
        """Accept and discard all telemetry data."""


class Guard:
    """Minimal Guard stub for Phase 2 interceptor testing.

    Owns the backend, policy, provider, identifiers, and telemetry references
    that interceptor/base.py needs. Does not implement the full public API.
    """

    def __init__(
        self,
        policy: Policy,
        backend: Backend,
        provider: Any,
        identifiers: dict[str, str] | None = None,
    ) -> None:
        """Initialise the Guard stub with policy, backend, and provider."""
        self.policy = policy
        self.backend = backend
        self.provider = provider
        self.telemetry = _NoopTelemetry()
        self.current_model: str = ""

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

    def get_status(self) -> Any:
        """Return a status snapshot. Stub returns a dict for Phase 2 testing."""
        keys = [
            BudgetKey(dimension=dim, identifier=self.identifiers[dim])
            for dim in self.policy.dimensions
        ]
        states = self.backend.get_states(keys)
        return {
            "dimensions": states,
            "active_policy": self.policy.name,
        }
