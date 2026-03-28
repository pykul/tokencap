"""Status API stub for Phase 2.

Full implementation is Phase 3. This stub provides the StatusResponse type
so that policy.py's TYPE_CHECKING import resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StatusResponse:
    """Point-in-time snapshot of all budget dimensions. Stub for Phase 2."""

    timestamp: str
    dimensions: dict[str, Any]
    active_policy: str
    next_threshold: Any = None
