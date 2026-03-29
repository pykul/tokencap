"""Backend Protocol: the seam between Guard and storage.

Both SQLiteBackend and RedisBackend implement this protocol. The Guard never
touches backend internals — it uses only this interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tokencap.core.types import BudgetKey, BudgetState, CheckResult


@runtime_checkable
class Backend(Protocol):
    """Storage backend protocol for budget state management."""

    def check_and_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> CheckResult:
        """Atomic check-then-increment across all keys.

        If ALL keys are within their limits: increment all by ``tokens`` and
        return CheckResult(allowed=True).

        If ANY key is at or over its limit: increment nothing and return
        CheckResult(allowed=False).
        """
        ...

    def force_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> dict[str, BudgetState]:
        """Unconditional increment. Never rejects, never raises.

        Used exclusively for post-call reconciliation.
        """
        ...

    def get_states(self, keys: list[BudgetKey]) -> dict[str, BudgetState]:
        """Non-atomic read of current state for a list of keys.

        Used for status queries only. Never for enforcement decisions.
        """
        ...

    def set_limit(self, key: BudgetKey, limit: int) -> None:
        """Register or update a budget limit for a key. Idempotent."""
        ...

    def reset(self, key: BudgetKey) -> None:
        """Reset used_tokens to zero and clear fired thresholds for this key."""
        ...

    def is_threshold_fired(self, key: BudgetKey, at_pct: float) -> bool:
        """Return True if the threshold at ``at_pct`` has already fired for this key."""
        ...

    def mark_threshold_fired(self, key: BudgetKey, at_pct: float) -> None:
        """Record that the threshold at ``at_pct`` has fired for this key."""
        ...

    def close(self) -> None:
        """Close backend connections and release resources."""
        ...
