"""Interceptor base functions: call(), call_async(), call_stream().

Module-level functions, not a class. All state lives in Guard.
All functions take guard as an explicit argument. See D-028.
Provider is passed explicitly by the caller, not stored on Guard.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Any, Callable

from tokencap.core.enums import ActionKind
from tokencap.core.exceptions import BackendError, BudgetExceededError
from tokencap.core.guard import Guard
from tokencap.core.types import BudgetKey, BudgetState, CheckResult, TokenUsage


def _build_keys(guard: Guard) -> list[BudgetKey]:
    """Build the list of BudgetKeys for the current call from guard state."""
    return [
        BudgetKey(dimension=dim, identifier=guard.identifiers[dim])
        for dim in guard.policy.dimensions
    ]


def _evaluate_thresholds(
    guard: Guard,
    keys: list[BudgetKey],
    states: dict[str, BudgetState],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all thresholds against current states.

    BLOCK thresholds are exempt from the fire-once rule (D-037). Every call
    that crosses a BLOCK threshold is blocked. WARN and WEBHOOK actions on
    the same threshold fire before the exception is raised. DEGRADE is
    skipped when BLOCK is present.

    Non-BLOCK thresholds follow the fire-once rule: they fire once per
    budget period, then are recorded as fired and skipped on subsequent calls.

    Returns a copy of kwargs with model swapped if DEGRADE fired,
    or the original kwargs dict unchanged if no DEGRADE.
    Never mutates the caller's kwargs.
    """
    call_kwargs = kwargs  # start with original, only copy if DEGRADE fires

    for dim, state in states.items():
        policy = guard.policy.dimensions[dim]
        for threshold in policy.thresholds:
            if state.pct_used < threshold.at_pct:
                continue

            has_block = any(a.kind == ActionKind.BLOCK for a in threshold.actions)
            key = BudgetKey(dimension=dim, identifier=guard.identifiers[dim])

            if not has_block:
                # Fire-once rule: skip if already fired this period
                if guard.backend.is_threshold_fired(key, threshold.at_pct):
                    continue
                guard.backend.mark_threshold_fired(key, threshold.at_pct)

            # Execute WARN and WEBHOOK actions
            for action in threshold.actions:
                if action.kind == ActionKind.WARN and action.callback:
                    try:
                        action.callback(guard.get_status())
                    except Exception:
                        pass  # WARN callback failure never propagates
                elif action.kind == ActionKind.WEBHOOK and action.webhook_url:
                    _fire_webhook(action.webhook_url, guard.get_status())

            if has_block:
                # BLOCK: raise after WARN/WEBHOOK have fired.
                # DEGRADE is skipped when BLOCK is present.
                check_result = CheckResult(
                    allowed=False,
                    states=states,
                    violated=[dim],
                )
                raise BudgetExceededError(check_result)

            # DEGRADE (only when no BLOCK on this threshold)
            for action in threshold.actions:
                if action.kind == ActionKind.DEGRADE and action.degrade_to:
                    call_kwargs = dict(kwargs)  # copy on first DEGRADE
                    call_kwargs["model"] = action.degrade_to

    return call_kwargs


def _fire_webhook(url: str, status: Any) -> None:
    """Fire a webhook POST in a background daemon thread. Never blocks."""
    def post() -> None:
        try:
            data = json.dumps({"status": str(status)}).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):  # noqa: S310
                pass
        except Exception:
            logging.getLogger("tokencap").warning(
                "Webhook POST to %s failed", url, exc_info=True
            )
    t = threading.Thread(target=post, daemon=True)
    t.start()


def call(
    real_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    guard: Guard,
    provider: Any,
) -> Any:
    """Sync call path.

    1. Estimate tokens
    2. Atomic check-and-increment
    3. Raise BudgetExceededError if blocked
    4. Evaluate thresholds (WARN, WEBHOOK, BLOCK, DEGRADE)
    5. Make the real SDK call
    6. Reconcile actual vs estimated via force_increment
    7. Emit OTEL
    8. Return response
    """
    estimated = provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    try:
        result = guard.backend.check_and_increment(keys, estimated)
    except BudgetExceededError:
        raise
    except Exception as err:
        raise BackendError(f"check_and_increment failed: {err}") from err
    if not result.allowed:
        raise BudgetExceededError(result)

    call_kwargs = _evaluate_thresholds(guard, keys, result.states, kwargs)
    original_model = kwargs.get("model", "")

    response = real_fn(**call_kwargs)

    actual = provider.extract_usage(response)
    delta = actual.total - estimated
    if delta > 0:
        try:
            final_states = guard.backend.force_increment(keys, delta)
        except Exception as err:
            raise BackendError(f"force_increment failed: {err}") from err
    else:
        final_states = result.states

    guard.telemetry.emit(
        estimated=estimated,
        actual=actual,
        original_model=original_model,
        actual_model=call_kwargs.get("model", original_model),
        states=final_states,
    )

    return response


async def call_async(
    real_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    guard: Guard,
    provider: Any,
) -> Any:
    """Async call path. Identical logic to call() with await where needed."""
    estimated = provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    try:
        result = guard.backend.check_and_increment(keys, estimated)
    except BudgetExceededError:
        raise
    except Exception as err:
        raise BackendError(f"check_and_increment failed: {err}") from err
    if not result.allowed:
        raise BudgetExceededError(result)

    call_kwargs = _evaluate_thresholds(guard, keys, result.states, kwargs)
    original_model = kwargs.get("model", "")

    response = await real_fn(**call_kwargs)

    actual = provider.extract_usage(response)
    delta = actual.total - estimated
    if delta > 0:
        try:
            final_states = guard.backend.force_increment(keys, delta)
        except Exception as err:
            raise BackendError(f"force_increment failed: {err}") from err
    else:
        final_states = result.states

    guard.telemetry.emit(
        estimated=estimated,
        actual=actual,
        original_model=original_model,
        actual_model=call_kwargs.get("model", original_model),
        states=final_states,
    )

    return response


def call_stream(
    real_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    guard: Guard,
    provider: Any,
) -> GuardedStream:
    """Streaming call path. Returns a GuardedStream context manager.

    The pre-call check runs immediately. Token usage is reconciled
    when the stream context manager exits.
    """
    estimated = provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    try:
        result = guard.backend.check_and_increment(keys, estimated)
    except BudgetExceededError:
        raise
    except Exception as err:
        raise BackendError(f"check_and_increment failed: {err}") from err
    if not result.allowed:
        raise BudgetExceededError(result)

    call_kwargs = _evaluate_thresholds(guard, keys, result.states, kwargs)
    original_model = kwargs.get("model", "")

    return GuardedStream(
        real_fn=real_fn,
        call_kwargs=call_kwargs,
        estimated=estimated,
        keys=keys,
        original_model=original_model,
        guard=guard,
        provider=provider,
    )


class GuardedStream:
    """Context manager wrapping the SDK stream context manager.

    Reconciles token usage on exit, including early exit.

    On normal exit: usage is extracted from the final message, reconciled.
    On early exit (break, exception): the estimated token count is used as
    the final count. A warning is logged. See D-029.
    """

    def __init__(
        self,
        real_fn: Callable[..., Any],
        call_kwargs: dict[str, Any],
        estimated: int,
        keys: list[BudgetKey],
        original_model: str,
        guard: Guard,
        provider: Any,
    ) -> None:
        """Initialise the stream wrapper."""
        self._real_fn = real_fn
        self._call_kwargs = call_kwargs
        self._estimated = estimated
        self._keys = keys
        self._original_model = original_model
        self._guard = guard
        self._provider = provider
        self._stream_ctx: Any = None

    def __enter__(self) -> Any:
        """Enter the underlying SDK stream context manager."""
        self._stream_ctx = self._real_fn(**self._call_kwargs).__enter__()
        return self._stream_ctx

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """Exit the stream and reconcile token usage. Never raises."""
        result: bool = self._stream_ctx.__exit__(exc_type, exc_val, exc_tb)

        # Extract usage from the completed stream if available
        try:
            usage: TokenUsage | None = self._provider.extract_usage(
                self._stream_ctx
            )
        except Exception:
            usage = None

        if usage is None or usage.total == 0:
            # Early exit or provider gave no usage, fall back to estimate
            if exc_type is not None:
                logging.getLogger("tokencap").warning(
                    "Stream exited early or returned no usage. "
                    "Using pre-call estimate (%d tokens) for reconciliation.",
                    self._estimated,
                )
            # No delta to reconcile, pre-call already debited the estimate
            final_states = self._guard.backend.get_states(self._keys)
        else:
            delta = usage.total - self._estimated
            if delta > 0:
                final_states = self._guard.backend.force_increment(
                    self._keys, delta
                )
            else:
                final_states = self._guard.backend.get_states(self._keys)

        self._guard.telemetry.emit(
            estimated=self._estimated,
            actual=usage or TokenUsage(
                input_tokens=self._estimated,
                output_tokens=0,
            ),
            original_model=self._original_model,
            actual_model=self._call_kwargs.get("model", self._original_model),
            states=final_states,
        )

        return result
