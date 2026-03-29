"""tokencap: Token budget enforcement for LLM client SDKs.

Public API surface. All public symbols are listed in __all__.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

from tokencap.core.enums import ActionKind, Provider, ResetPeriod
from tokencap.core.exceptions import BackendError, BudgetExceededError, ConfigurationError
from tokencap.core.guard import Guard
from tokencap.core.policy import Action, DimensionPolicy, Policy, Threshold
from tokencap.status.api import StatusResponse

__all__ = [
    "wrap",
    "init",
    "patch",
    "unpatch",
    "get_status",
    "teardown",
    "Guard",
    "Policy",
    "DimensionPolicy",
    "Threshold",
    "Action",
    "ActionKind",
    "Provider",
    "ResetPeriod",
    "BudgetExceededError",
    "BackendError",
    "ConfigurationError",
    "StatusResponse",
]

_guard: Guard | None = None
_lock = threading.Lock()
_patched: bool = False
_patched_providers: set[str] = set()
_original_inits: dict[str, Any] = {}
_VALID_PROVIDERS = {Provider.ANTHROPIC, Provider.OPENAI}
_log = logging.getLogger("tokencap")


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


def _build_guard(
    limit: int | None, policy: Policy | None, quiet: bool
) -> Guard:
    """Build a Guard from limit or policy parameters."""
    if policy is not None:
        return Guard(policy=policy, quiet=quiet)
    if limit is not None:
        return Guard(
            policy=Policy(
                dimensions={
                    "session": DimensionPolicy(
                        limit=limit,
                        thresholds=[
                            Threshold(at_pct=1.0, actions=[Action(kind=ActionKind.BLOCK)]),
                        ],
                    ),
                }
            ),
            quiet=quiet,
        )
    return Guard(
        policy=Policy(
            dimensions={"session": DimensionPolicy(limit=sys.maxsize)},
        ),
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
            _guard = _build_guard(limit, policy, quiet)
        elif limit is not None or policy is not None:
            _log.warning(
                "tokencap: wrap() called with a new policy but a Guard is already "
                "active. The existing Guard will be used. Call teardown() first to "
                "start a new session."
            )

    return _detect_and_wrap(client, _guard)


def patch(
    limit: int | None = None,
    policy: Policy | None = None,
    quiet: bool = False,
    providers: list[Provider | str] | None = None,
) -> None:
    """Monkey-patch SDK constructors so all new clients are automatically wrapped.

    Works with agent frameworks that construct SDK clients internally (LangChain,
    CrewAI, LlamaIndex, AutoGen). Clients constructed after patch() is called are
    automatically tracked and enforced. Existing client instances are not affected.

    limit and policy are mutually exclusive. Passing both raises ConfigurationError.
    providers defaults to ["anthropic", "openai"]. Pass a subset to patch only
    specific SDKs.
    Call unpatch() to reverse all changes.
    """
    global _guard, _patched  # noqa: PLW0603

    if limit is not None and policy is not None:
        raise ConfigurationError(
            "patch() accepts limit or policy, not both. "
            "Use limit=N for a simple token cap, or policy=Policy(...) for full control."
        )

    target_providers = providers if providers is not None else sorted(_VALID_PROVIDERS)
    if not target_providers:
        raise ConfigurationError(
            "providers must not be empty. "
            f"Valid providers: {', '.join(sorted(_VALID_PROVIDERS))}"
        )
    unknown = set(target_providers) - _VALID_PROVIDERS
    if unknown:
        raise ConfigurationError(
            f"Unknown providers: {', '.join(sorted(unknown))}. "
            f"Valid providers: {', '.join(sorted(_VALID_PROVIDERS))}"
        )

    with _lock:
        if _patched:
            raise ConfigurationError(
                "tokencap is already patched. Call unpatch() before patching again."
            )

        _guard = _build_guard(limit, policy, quiet=True)
        patched_sdks: list[str] = []

        # Patch by replacing classes in the SDK module namespace with
        # factory functions that construct-then-wrap.
        if Provider.ANTHROPIC in target_providers:
            try:
                import anthropic

                _original_inits["anthropic.Anthropic"] = anthropic.Anthropic
                _original_inits["anthropic.AsyncAnthropic"] = anthropic.AsyncAnthropic

                orig_anth = anthropic.Anthropic
                orig_async_anth = anthropic.AsyncAnthropic

                def _make_anthropic(*args: Any, **kwargs: Any) -> Any:
                    real = orig_anth(*args, **kwargs)
                    return _guard.wrap_anthropic(real) if _guard is not None else real

                def _make_async_anthropic(*args: Any, **kwargs: Any) -> Any:
                    real = orig_async_anth(*args, **kwargs)
                    return _guard.wrap_anthropic(real) if _guard is not None else real

                anthropic.Anthropic = _make_anthropic  # type: ignore[assignment,misc]
                anthropic.AsyncAnthropic = _make_async_anthropic  # type: ignore[assignment,misc]
                patched_sdks.append("anthropic")
            except ImportError:
                pass

        if Provider.OPENAI in target_providers:
            try:
                import openai

                _original_inits["openai.OpenAI"] = openai.OpenAI
                _original_inits["openai.AsyncOpenAI"] = openai.AsyncOpenAI

                orig_oai = openai.OpenAI
                orig_async_oai = openai.AsyncOpenAI

                def _make_openai(*args: Any, **kwargs: Any) -> Any:
                    real = orig_oai(*args, **kwargs)
                    return _guard.wrap_openai(real) if _guard is not None else real

                def _make_async_openai(*args: Any, **kwargs: Any) -> Any:
                    real = orig_async_oai(*args, **kwargs)
                    return _guard.wrap_openai(real) if _guard is not None else real

                openai.OpenAI = _make_openai  # type: ignore[assignment,misc]
                openai.AsyncOpenAI = _make_async_openai  # type: ignore[assignment,misc]
                patched_sdks.append("openai")
            except ImportError:
                pass

        _patched = True
        _patched_providers.update(patched_sdks)

        if not quiet:
            sdk_str = " + ".join(patched_sdks) if patched_sdks else "none"
            backend_name = _guard._backend_display_name()
            dim_policy = list(_guard.policy.dimensions.values())[0]
            if dim_policy.thresholds:
                limit_str = f"limit={dim_policy.limit} tokens"
            else:
                limit_str = "(no limit set)"
            print(
                f"[tokencap] patched: {sdk_str}\n"
                f"           backend={backend_name} {limit_str}",
                file=sys.stdout,
            )


def unpatch() -> None:
    """Reverse all monkey-patches applied by patch() and tear down the Guard.

    Only restores providers that were actually patched.
    """
    global _patched  # noqa: PLW0603

    with _lock:
        if not _patched:
            return

        if Provider.ANTHROPIC in _patched_providers:
            try:
                import anthropic

                if "anthropic.Anthropic" in _original_inits:
                    anthropic.Anthropic = _original_inits.pop("anthropic.Anthropic")  # type: ignore[misc]
                if "anthropic.AsyncAnthropic" in _original_inits:
                    anthropic.AsyncAnthropic = _original_inits.pop("anthropic.AsyncAnthropic")  # type: ignore[misc]
            except ImportError:
                pass

        if Provider.OPENAI in _patched_providers:
            try:
                import openai

                if "openai.OpenAI" in _original_inits:
                    openai.OpenAI = _original_inits.pop("openai.OpenAI")  # type: ignore[misc]
                if "openai.AsyncOpenAI" in _original_inits:
                    openai.AsyncOpenAI = _original_inits.pop("openai.AsyncOpenAI")  # type: ignore[misc]
            except ImportError:
                pass

        _patched = False
        _patched_providers.clear()

    teardown()


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

        from tokencap.interceptor.anthropic import GuardedAnthropic

        orig_anth = _original_inits.get("anthropic.Anthropic", anthropic.Anthropic)
        orig_async = _original_inits.get("anthropic.AsyncAnthropic", anthropic.AsyncAnthropic)
        if isinstance(client, (orig_anth, orig_async)):
            return guard.wrap_anthropic(client)
        if isinstance(client, GuardedAnthropic):
            return client  # already wrapped
    except ImportError:
        pass

    try:
        import openai

        from tokencap.interceptor.openai import GuardedOpenAI

        orig_oai = _original_inits.get("openai.OpenAI", openai.OpenAI)
        orig_async_oai = _original_inits.get("openai.AsyncOpenAI", openai.AsyncOpenAI)
        if isinstance(client, (orig_oai, orig_async_oai)):
            return guard.wrap_openai(client)
        if isinstance(client, GuardedOpenAI):
            return client  # already wrapped
    except ImportError:
        pass

    raise ConfigurationError(
        f"Unsupported client type: {type(client).__name__}. "
        "tokencap supports anthropic.Anthropic, anthropic.AsyncAnthropic, "
        "openai.OpenAI, and openai.AsyncOpenAI."
    )
