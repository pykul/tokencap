"""Status API: StatusResponse, ThresholdInfo, and get_status().

get_status() is a synchronous read. It calls backend.get_states() only.
It never writes and never blocks on the call path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tokencap.core.enums import ActionKind
from tokencap.core.types import BudgetKey, BudgetState

if TYPE_CHECKING:
    from tokencap.core.guard import Guard


@dataclass
class ThresholdInfo:
    """The next unfired threshold across all active dimensions."""

    dimension: str
    at_pct: float
    action_kinds: list[str]
    triggers_at_tokens: int


@dataclass
class StatusResponse:
    """Point-in-time snapshot of all budget dimensions."""

    timestamp: str
    dimensions: dict[str, BudgetState]
    active_policy: str
    next_threshold: ThresholdInfo | None


def get_status(guard: Guard) -> StatusResponse:
    """Build a StatusResponse from the current Guard state.

    Reads backend state only. Never writes. Safe to call from any thread.
    """
    keys = [
        BudgetKey(dimension=dim, identifier=guard.identifiers[dim])
        for dim in guard.policy.dimensions
    ]
    states = guard.backend.get_states(keys)

    # Find the next unfired threshold: smallest (at_pct - pct_used) gap
    next_thresh: ThresholdInfo | None = None
    smallest_gap = float("inf")

    for dim, dim_policy in guard.policy.dimensions.items():
        state = states.get(dim)
        if state is None:
            continue
        key = BudgetKey(dimension=dim, identifier=guard.identifiers[dim])
        for threshold in dim_policy.thresholds:
            if guard.backend.is_threshold_fired(key, threshold.at_pct):
                continue
            # Skip BLOCK thresholds from next_threshold — they are exempt
            # from fire-once and always fire, so "next unfired" doesn't apply
            has_block = any(a.kind == ActionKind.BLOCK for a in threshold.actions)
            if has_block:
                continue
            gap = threshold.at_pct - state.pct_used
            if gap < smallest_gap:
                smallest_gap = gap
                next_thresh = ThresholdInfo(
                    dimension=dim,
                    at_pct=threshold.at_pct,
                    action_kinds=[a.kind for a in threshold.actions],
                    triggers_at_tokens=int(dim_policy.limit * threshold.at_pct),
                )

    return StatusResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        dimensions=states,
        active_policy=guard.policy.name,
        next_threshold=next_thresh,
    )
