"""tokencap: Token budget enforcement for LLM client SDKs.

Public API surface. All public symbols are listed in __all__.
No logic beyond the module-level singleton management for the drop-in API.
"""

from __future__ import annotations

import threading
from typing import Any

from tokencap.core.exceptions import BackendError, BudgetExceededError, ConfigurationError
from tokencap.core.guard import Guard
from tokencap.core.policy import Action, DimensionPolicy, Policy, Threshold
from tokencap.status.api import StatusResponse

__all__ = [
    "wrap",
    "init",
    "get_status",
    "teardown",
    "Guard",
    "Policy",
    "DimensionPolicy",
    "Threshold",
    "Action",
    "BudgetExceededError",
    "BackendError",
    "StatusResponse",
]

_guard: Guard | None = None
_lock = threading.Lock()


def init(
    policy: Policy,
    identifiers: dict[str, str] | None = None,
    backend: Any = None,
    otel_enabled: bool = True,
    quiet: bool = False,
) -> None:
    """Pre-configure the global Guard.

    Optional. Use when you need custom identifiers, a non-default backend,
    or shared state across multiple wrap() calls. If you skip init() and
    call wrap() directly, the Guard is created with defaults.
    """
    global _guard  # noqa: PLW0603
    with _lock:
        _guard = Guard(
            policy=policy,
            identifiers=identifiers,
            backend=backend,
            otel_enabled=otel_enabled,
            quiet=quiet,
        )


def wrap(
    client: Any,
    limit: int | None = None,
    policy: Policy | None = None,
    quiet: bool = False,
) -> Any:
    """Wrap an Anthropic or OpenAI client for token tracking and enforcement.

    Three tiers:
    - wrap(client): tracking only, no enforcement
    - wrap(client, limit=N): hard BLOCK at N tokens on "session" dimension
    - wrap(client, policy=my_policy): full policy control

    limit and policy are mutually exclusive. Passing both raises ConfigurationError.
    """
    global _guard  # noqa: PLW0603

    if limit is not None and policy is not None:
        raise ConfigurationError(
            "wrap() accepts limit or policy, not both. "
            "Use limit=N for a simple token cap, or policy=Policy(...) for full control."
        )

    with _lock:
        if _guard is None:
            if policy is not None:
                _guard = Guard(policy=policy, quiet=quiet)
            elif limit is not None:
                _guard = Guard(
                    policy=Policy(
                        dimensions={
                            "session": DimensionPolicy(
                                limit=limit,
                                thresholds=[
                                    Threshold(
                                        at_pct=1.0,
                                        actions=[Action(kind="BLOCK")],
                                    ),
                                ],
                            ),
                        }
                    ),
                    quiet=quiet,
                )
            else:
                # Tracking only: very large limit, no thresholds
                import sys as _sys

                _guard = Guard(
                    policy=Policy(
                        dimensions={
                            "session": DimensionPolicy(limit=_sys.maxsize),
                        }
                    ),
                    quiet=quiet,
                )

    return _detect_and_wrap(client, _guard)


def get_status() -> StatusResponse:
    """Return a StatusResponse from the global Guard.

    Raises ConfigurationError if no Guard has been initialised.
    """
    if _guard is None:
        raise ConfigurationError(
            "No Guard initialised. Call tokencap.wrap() or tokencap.init() first."
        )
    return _guard.get_status()


def teardown() -> None:
    """Tear down the global Guard, close backend connections."""
    global _guard  # noqa: PLW0603
    with _lock:
        if _guard is not None:
            _guard.teardown()
            _guard = None


def _detect_and_wrap(client: Any, guard: Guard) -> Any:
    """Detect client type via isinstance and wrap with the appropriate provider."""
    try:
        import anthropic

        if isinstance(client, (anthropic.Anthropic, anthropic.AsyncAnthropic)):
            return guard.wrap_anthropic(client)
    except ImportError:
        pass

    try:
        import openai

        if isinstance(client, (openai.OpenAI, openai.AsyncOpenAI)):
            return guard.wrap_openai(client)
    except ImportError:
        pass

    raise ConfigurationError(
        f"Unsupported client type: {type(client).__name__}. "
        "tokencap supports anthropic.Anthropic, anthropic.AsyncAnthropic, "
        "openai.OpenAI, and openai.AsyncOpenAI."
    )
