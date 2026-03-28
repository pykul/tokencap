"""Policy types for tokencap budget configuration.

Defines the policy hierarchy: Policy > DimensionPolicy > Threshold > Action.
These are pure dataclasses with validation in __post_init__. No business logic
beyond invariant checking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from tokencap.status.api import StatusResponse


@dataclass
class Action:
    """A single action executed when a threshold is crossed."""

    kind: Literal["WARN", "BLOCK", "DEGRADE", "WEBHOOK"]
    webhook_url: str | None = None
    degrade_to: str | None = None
    callback: Callable[[StatusResponse], None] | None = None


@dataclass
class Threshold:
    """A trigger point within a dimension. Fires at most once per budget period.

    at_pct must be in the range (0.0, 1.0]. Values outside this range raise
    ValueError in __post_init__.
    """

    at_pct: float
    actions: list[Action]

    def __post_init__(self) -> None:
        """Validate at_pct is in (0.0, 1.0]."""
        if not (0.0 < self.at_pct <= 1.0):
            raise ValueError(
                f"Threshold.at_pct must be in (0.0, 1.0], got {self.at_pct}"
            )


@dataclass
class DimensionPolicy:
    """Budget configuration for a single named dimension.

    reset_every is defined but not yet implemented. Periodic resets are
    planned for v0.2. Until then, call reset() manually on the backend.
    """

    limit: int
    thresholds: list[Threshold] = field(default_factory=list)
    reset_every: Literal["day", "hour"] | None = None

    def __post_init__(self) -> None:
        """Ensure thresholds are always evaluated in ascending order."""
        self.thresholds = sorted(self.thresholds, key=lambda t: t.at_pct)


@dataclass
class Policy:
    """Complete budget policy across all dimensions."""

    dimensions: dict[str, DimensionPolicy]
    name: str = "default"
