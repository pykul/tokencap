# tokencap: Architecture Document

> **For Claude Code:** Read this entire document before writing any code. This is the
> single source of truth for all architectural decisions. When in doubt, refer back here.

---

## Executive Summary

tokencap is a Python client wrapper library that gives developers visibility into
token usage across agents and enforcement of token budgets. It wraps LLM provider
clients in-process, tracks usage against multi-dimensional budgets, and executes
configurable policy actions when thresholds are crossed.

It works in two modes:

- **Zero-infra mode** (default): SQLite-backed, no external dependencies. Multiple
  agents and processes on the same machine share state via a single file.
  Works out of the box with `pip install tokencap`.
- **Distributed mode**: Redis-backed, atomic enforcement across any number of
  machines and processes. One-line upgrade. Identical public API.

The public API is identical in both modes. The backend is an implementation detail
the developer switches with a single constructor argument.

---

## What This System Does (And Deliberately Does Not Do)

**Does:**
- Wrap LLM provider clients (Anthropic, OpenAI) in-process. No proxy, no network change.
- Provide real-time visibility into token usage across agents and dimensions
- Estimate tokens before each call and reconcile actual usage after
- Track usage against multiple simultaneous budget dimensions (per-session, per-tenant,
  per-day, or any custom dimension the developer defines)
- Execute policy actions at configurable thresholds: WARN, BLOCK, DEGRADE, WEBHOOK
- Emit OTEL metrics after each call (no-ops silently if opentelemetry-api not installed)
- Expose programmatic budget status via get_status()

**Does NOT:**
- Proxy traffic at the network layer (no MITM, no HTTP interception)
- Store prompt or response content anywhere
- Manage billing, invoicing, or payment
- Provide a UI, dashboard, or HTTP server in v0
- Support providers other than Anthropic and OpenAI in v0
- Make opinions about which model to degrade to (caller supplies the target model)

---

## Architecture Diagram

```
                        User Code
                 (anthropic.Anthropic() /
                    openai.OpenAI())
                          |
               wrap_anthropic() / wrap_openai()
                          |
              +-----------+-----------+
              |                       |
     GuardedAnthropic          GuardedOpenAI
      (interceptor/)            (interceptor/)
              |                       |
              +-----------+-----------+
                          |
                 interceptor/base.py
              call() / call_async() / call_stream()
                          |
          +---------------+---------------+
          |               |               |
      Backend          Provider         Policy
      Protocol         Protocol         Engine
    (backends/)      (providers/)    (core/policy.py)
          |               |
    +-----+-----+    +----+----+
    |           |    |         |
 SQLite       Redis  Anthropic  OpenAI
 Backend     Backend Provider  Provider
 (default)  (multi-
            machine)
          |
          +-- Telemetry (telemetry/otel.py)
          |   no-op if opentelemetry-api not installed
          |
          +-- Status (status/api.py)
              get_status() -> StatusResponse
```

**Data flow for a single LLM call:**

```
User code calls client.messages.create(...)
    |
    v
GuardedAnthropic.messages.create()
    |
    v
call(real_fn, kwargs, guard)          [interceptor/base.py]
    |-- provider.estimate_tokens(request_kwargs)
    |-- backend.check_and_increment(keys, estimated)
    |       |-- allowed=False: raise BudgetExceededError (call never made)
    |       |-- allowed=True:  continue
    |-- _evaluate_thresholds(guard, keys, states, kwargs)
    |       |-- fire WARN callbacks
    |       |-- post WEBHOOK in background thread
    |       |-- [if BLOCK] raise BudgetExceededError
    |       |-- [if DEGRADE] copy kwargs, swap model
    |
    v
Actual LLM API call
    |
    v
    |-- provider.extract_usage(response)
    |-- backend.force_increment(keys, delta)  [never rejects]
    |-- guard.telemetry.emit(span, metrics)
    |
    v
Response returned to user code unchanged
```

---

## Repository Structure

```
tokencap/
├── ARCHITECTURE.md          # This file
├── DECISIONS.md             # Why decisions were made
├── CLAUDE.md                # Standing rules for Claude Code sessions
├── README.md                # User-facing documentation
├── pyproject.toml           # Package metadata and dependencies
├── Makefile                 # lint, test, build, publish targets
│
├── tokencap/
│   ├── __init__.py          # Public API surface only: no logic here
│   ├── py.typed             # PEP 561 marker: enables mypy for downstream users
│   │
│   ├── core/
│   │   ├── types.py         # BudgetKey, BudgetState, CheckResult,
│   │   │                    # TokenUsage: pure dataclasses, no logic
│   │   ├── policy.py        # Policy, DimensionPolicy, Threshold, Action
│   │   ├── guard.py         # Guard: main orchestrator, owns backend + providers
│   │   └── exceptions.py    # BudgetExceededError, BackendError, ConfigurationError
│   │
│   ├── backends/
│   │   ├── protocol.py      # Backend Protocol: the seam between Guard and storage
│   │   ├── sqlite.py        # SQLiteBackend: zero-infra default
│   │   └── redis.py         # RedisBackend: distributed mode
│   │
│   ├── providers/
│   │   ├── protocol.py      # Provider Protocol: token estimation + usage extraction
│   │   ├── anthropic.py     # AnthropicProvider
│   │   └── openai.py        # OpenAIProvider
│   │
│   ├── interceptor/
│   │   ├── base.py          # call(), call_async(), call_stream(): provider-agnostic intercept functions
│   │   ├── anthropic.py     # GuardedAnthropic: wraps sync + async Anthropic clients
│   │   └── openai.py        # GuardedOpenAI: wraps openai.OpenAI
│   │
│   ├── telemetry/
│   │   └── otel.py          # OTEL metric emission: no-ops if not installed
│   │
│   └── status/
│       └── api.py           # StatusResponse dataclass + get_status() implementation
│
└── tests/
    ├── unit/
    │   ├── test_backends.py
    │   ├── test_policy.py
    │   ├── test_providers.py
    │   └── test_interceptor.py
    ├── integration/
    │   ├── __init__.py
    │   └── test_full_pipeline.py  # HTTP layer mocked with pytest-httpx, always runs
    ├── live/
    │   └── __init__.py
    └── conftest.py                # Shared fixtures: mock providers, mock backends
```

---

## Core Types (core/types.py)

Pure dataclasses. No business logic. Every other module imports from here.
Nothing in this file imports from any other tokencap module.

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetKey:
    """Uniquely identifies a budget counter in the backend store."""
    dimension: str   # e.g. "session", "tenant_daily", "tenant_monthly"
    identifier: str  # e.g. "run_abc123", "tenant_acme:2026-03-27"


@dataclass
class BudgetState:
    """Current snapshot of a single budget dimension. Read-only view."""
    key: BudgetKey
    limit: int        # tokens: the authoritative enforcement unit
    used: int         # tokens consumed so far in this period
    remaining: int    # tokens left (may be negative after force_increment)
    pct_used: float   # used / limit: may exceed 1.0 after reconciliation
```

`cost_usd` is not included in `BudgetState`. Dollar cost calculation requires a
pricing table that cannot be kept accurate without a machine-readable provider API.
See D-045.

```python
@dataclass
class CheckResult:
    """Outcome of a check_and_increment call."""
    allowed: bool
    states: dict[str, BudgetState]  # keyed by dimension name
    violated: list[str]             # dimension names that caused allowed=False


@dataclass
class TokenUsage:
    """Actual token counts extracted from a provider response."""
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        """Input + output tokens only. Cache tokens are tracked separately for
        cost calculation but excluded from the enforcement total to avoid
        double-counting on Anthropic prompt cache hits."""
        return self.input_tokens + self.output_tokens
```

---

## Exceptions (core/exceptions.py)

```python
class BudgetExceededError(Exception):
    """
    Raised by the BLOCK action before an LLM call is made.
    The call is never sent to the provider.

    Attributes:
        check_result: CheckResult
            Full state of every dimension at the time of the block.
            check_result.violated lists the dimension names that caused the block.
            check_result.states maps every dimension name to its BudgetState.
    """
    def __init__(self, check_result: CheckResult) -> None: ...
    check_result: CheckResult


class BackendError(Exception):
    """
    Raised when the storage backend encounters an unrecoverable error,
    such as a lost Redis connection during check_and_increment.
    The LLM call is not made when this is raised.
    """


class ConfigurationError(Exception):
    """
    Raised during Guard initialisation when the policy or backend
    configuration is invalid. For example, a DimensionPolicy with a
    limit of zero, or an unrecognised backend type.
    """
```

---

## The Backend Protocol (backends/protocol.py)

The most critical interface in the system. Both `SQLiteBackend` and `RedisBackend`
implement it. The Guard never touches backend internals. It uses only this protocol.
Adding methods here requires updating ARCHITECTURE.md first.

```python
from __future__ import annotations
from typing import Protocol, runtime_checkable
from tokencap.core.types import BudgetKey, BudgetState, CheckResult


@runtime_checkable
class Backend(Protocol):

    def check_and_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> CheckResult:
        """
        Atomic check-then-increment across all keys.

        If ALL keys are within their limits: increment all by `tokens` and
        return CheckResult(allowed=True, states={...}, violated=[]).

        If ANY key is at or over its limit: increment nothing and return
        CheckResult(allowed=False, states={...}, violated=[...]).

        Atomicity is non-negotiable. Partial increments corrupt the ledger.
        Used for enforcement decisions only. For post-call reconciliation
        use force_increment().
        """
        ...

    def force_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> dict[str, BudgetState]:
        """
        Unconditional increment. Never rejects, never raises.

        Used exclusively for post-call reconciliation: debiting the delta
        between the pre-call estimate and the actual token count. A completed
        API call cannot be undone, so this increment must always succeed
        regardless of current budget state.

        Returns updated states for telemetry. Does not evaluate thresholds.
        """
        ...

    def get_states(self, keys: list[BudgetKey]) -> dict[str, BudgetState]:
        """
        Non-atomic read of current state for a list of keys.
        Used for status queries only. Never for enforcement decisions.
        """
        ...

    def set_limit(self, key: BudgetKey, limit: int) -> None:
        """
        Register or update a budget limit for a key. Idempotent.
        Called during Guard initialisation for each configured dimension.
        """
        ...

    def reset(self, key: BudgetKey) -> None:
        """
        Reset used_tokens to zero. Does not remove or change the limit.
        Also clears all fired threshold records for this key.
        Used for period resets (daily, hourly schedules) and in tests.
        """
        ...

    def is_threshold_fired(self, key: BudgetKey, at_pct: float) -> bool:
        """
        Returns True if the threshold at at_pct has already fired for this key
        in the current budget period. Used to enforce the fire-once rule.
        """
        ...

    def mark_threshold_fired(self, key: BudgetKey, at_pct: float) -> None:
        """
        Record that the threshold at at_pct has fired for this key.
        Subsequent calls to is_threshold_fired for the same key and at_pct
        will return True until reset() is called.
        """
        ...
```

### SQLiteBackend (backends/sqlite.py)

Default file path: `tokencap.db` in the current working directory.
Overridable: `SQLiteBackend(path="./data/tokencap.db")`.

Multiple agents or processes on the same machine share state automatically as
long as they point to the same file. `check_and_increment` uses `BEGIN IMMEDIATE`
to serialise writes, so concurrent increments are safe. The limitation is machine
scope: SQLite does not work across separate machines. For that, use `RedisBackend`.

`force_increment` uses a plain `BEGIN` transaction with no limit check.

Schema:
```sql
CREATE TABLE IF NOT EXISTS budgets (
    key_dimension  TEXT    NOT NULL,
    key_identifier TEXT    NOT NULL,
    limit_tokens   INTEGER NOT NULL,
    used_tokens    INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL,
    PRIMARY KEY (key_dimension, key_identifier)
);

CREATE TABLE IF NOT EXISTS fired_thresholds (
    key_dimension  TEXT    NOT NULL,
    key_identifier TEXT    NOT NULL,
    at_pct         REAL    NOT NULL,
    fired_at       TEXT    NOT NULL,
    PRIMARY KEY (key_dimension, key_identifier, at_pct)
);
```

`reset()` deletes rows from `fired_thresholds` matching the key as well as
zeroing `used_tokens` in `budgets`. Both operations run in the same transaction.

### RedisBackend (backends/redis.py)

All writes use Lua scripts. Lua execution in Redis is atomic by definition.
Two scripts:

**check_and_increment Lua script:**
1. Read `used` for all keys
2. Read `limit` for all keys
3. If any `(used + tokens) > limit`: return REJECT with violating key names
4. Otherwise: `INCRBY` all used keys by `tokens`, return ALLOW with final states

**force_increment Lua script:**
1. `INCRBY` all used keys by `tokens` unconditionally
2. Return updated states

Key format: `tokencap:used:{dimension}:{identifier}`
Limit key format: `tokencap:limit:{dimension}:{identifier}`
Threshold fired key format: `tokencap:fired:{dimension}:{identifier}:{at_pct}`

The `{identifier}` portion is treated as an opaque string. Colons within the
identifier (e.g. `acme:2026-03-27`) are fine. Redis keys are plain strings, not
parsed paths.

`is_threshold_fired` does a Redis `EXISTS` on the fired key.
`mark_threshold_fired` does a Redis `SET` with no expiry (TTL is managed by
`reset()`, which calls `DEL` on all fired keys for a given `BudgetKey`).
`reset()` uses a Lua script to zero the used key and delete all fired threshold
keys for the dimension+identifier in one atomic operation.

Constructor: `RedisBackend(url="redis://localhost:6379")`

If `redis` is not installed and `RedisBackend` is instantiated, raise:
```python
raise ImportError(
    "RedisBackend requires the redis package. "
    "Install it with: pip install tokencap[redis]"
)
```
Never let a bare `ModuleNotFoundError` reach user code.

---

## The Provider Protocol (providers/protocol.py)

```python
from __future__ import annotations
from typing import Any, Protocol
from tokencap.core.types import TokenUsage


class Provider(Protocol):

    def estimate_tokens(self, request_kwargs: dict[str, Any]) -> int:
        """
        Estimate token count from request kwargs before the API call.
        May undercount. Actual usage is reconciled post-call via force_increment.
        Must never raise. Return a conservative estimate on any failure.
        """
        ...

    def extract_usage(self, response: Any) -> TokenUsage:
        """
        Extract actual token usage from the provider response object.
        Must handle all response types the provider returns (sync, streaming).
        Must never raise. Return TokenUsage(0, 0) on any failure.
        """
        ...

    def get_model(self, request_kwargs: dict[str, Any]) -> str:
        """
        Extract the model name string from request kwargs.
        Returns an empty string on failure. Never raises.
        """
        ...

```

### AnthropicProvider (providers/anthropic.py)

- `estimate_tokens`: calls `anthropic.Anthropic().count_tokens()` on the messages
  list if available. Falls back to `sum(len(str(m)) for m in messages) // 4`.
- `extract_usage`: if the response has a callable `.parse()` method (raw response
  wrapper from `with_raw_response`), calls it first to get the parsed message.
  Then reads `response.usage.input_tokens`, `response.usage.output_tokens`,
  `response.usage.cache_read_input_tokens`,
  `response.usage.cache_creation_input_tokens`. All fields default to 0 if absent.

`token_cost_usd()` is not implemented. See D-045.

### OpenAIProvider (providers/openai.py)

- `estimate_tokens`: uses `tiktoken.encoding_for_model(model)` if tiktoken is
  installed. Falls back to character count // 4. Never raises.
- `extract_usage`: if the response has a callable `.parse()` method (raw response
  wrapper from `with_raw_response`), calls it first to get the parsed completion.
  Then reads `response.usage.prompt_tokens` and
  `response.usage.completion_tokens`. Defaults to 0 if absent.

`token_cost_usd()` is not implemented. See D-045.

---

## The Interceptor (interceptor/)

This section describes the interception mechanism in full. Claude Code must
understand every call path before implementing any file in this package.

---

### How Python attribute lookup enables interception

When a developer writes `client.messages.create(...)`, Python resolves this
in two steps:

1. `client.messages`: Python looks up `messages` on the object
2. `.create(...)`: Python calls `create` on whatever step 1 returned

If `messages` were not defined on `GuardedAnthropic` at all, Python would call
`__getattr__("messages")`, which returns `self._client.messages`, the real SDK
object. Any `.create()` call on that would go directly to the SDK. No interception.

The fix is to define `messages` as a `@property` on `GuardedAnthropic`. Python
checks the class for a descriptor before falling back to `__getattr__`. The
property returns a `GuardedMessages` instance, not the real messages object.
From there, `create()` and `stream()` are defined as real methods on
`GuardedMessages`, so tokencap code runs before the SDK is ever called.

This is the entire interception mechanism. `@property` intercepts the resource
access. Defined methods on the proxy intercept the call. `__getattr__` handles
everything else as pass-through.

---

### base.py: intercept functions

`base.py` contains module-level functions, not a class. There is no
`InterceptorBase` instance. Guard holds config (policy, backend, identifiers,
telemetry). Provider is passed explicitly by the caller — it lives on the
wrapped client, not on Guard. All functions take `guard` and `provider` as
explicit arguments.

```python
from tokencap.core.types import BudgetKey, BudgetState, CheckResult, TokenUsage
from tokencap.core.exceptions import BudgetExceededError
from tokencap.core.guard import Guard
from typing import Any, Callable
import threading
import urllib.request
import json


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
    """
    Evaluate all thresholds against current states.

    - BLOCK thresholds are exempt from the fire-once rule. Every call
      that crosses a BLOCK threshold is blocked. WARN and WEBHOOK actions
      on the same threshold fire before the exception is raised. DEGRADE
      is skipped when BLOCK is present.
    - Non-BLOCK thresholds follow the fire-once rule: they fire once per
      budget period, then are recorded as fired and skipped on subsequent
      calls.

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

            has_block = any(a.kind == "BLOCK" for a in threshold.actions)
            key = BudgetKey(dimension=dim, identifier=guard.identifiers[dim])

            if not has_block:
                # Fire-once rule: skip if already fired this period
                if guard.backend.is_threshold_fired(key, threshold.at_pct):
                    continue
                guard.backend.mark_threshold_fired(key, threshold.at_pct)

            # Execute WARN and WEBHOOK actions
            for action in threshold.actions:
                if action.kind == "WARN" and action.callback:
                    try:
                        action.callback(guard.get_status())
                    except Exception:
                        pass  # WARN callback failure never propagates
                elif action.kind == "WEBHOOK" and action.webhook_url:
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
                if action.kind == "DEGRADE" and action.degrade_to:
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
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            import logging
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
    """
    Sync call path. Used by GuardedMessages.create() and
    GuardedCompletions.create().

    1. Estimate tokens
    2. Atomic check-and-increment
    3. Raise BudgetExceededError if blocked
    4. Evaluate thresholds (WARN, WEBHOOK, DEGRADE)
    5. Make the real SDK call
    6. Reconcile actual vs estimated via force_increment
    7. Emit OTEL
    8. Return response
    """
    estimated = provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    result = guard.backend.check_and_increment(keys, estimated)
    if not result.allowed:
        raise BudgetExceededError(result)

    call_kwargs = _evaluate_thresholds(guard, keys, result.states, kwargs)
    original_model = kwargs.get("model", "")

    response = real_fn(**call_kwargs)

    actual = provider.extract_usage(response)
    delta = actual.total - estimated
    if delta > 0:
        final_states = guard.backend.force_increment(keys, delta)
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
    """
    Async call path. Identical logic to call() with await where needed.
    Used by GuardedMessages.create() on AsyncAnthropic clients.

    Note: check_and_increment() and force_increment() are synchronous
    blocking calls. In high-throughput asyncio agents, these serialize
    through the backend lock. For async agents with strict throughput
    requirements, use RedisBackend. Full asyncio.to_thread() wrapping
    is planned for v0.2.
    """
    estimated = provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    result = guard.backend.check_and_increment(keys, estimated)
    if not result.allowed:
        raise BudgetExceededError(result)

    call_kwargs = _evaluate_thresholds(guard, keys, result.states, kwargs)
    original_model = kwargs.get("model", "")

    response = await real_fn(**call_kwargs)

    actual = provider.extract_usage(response)
    delta = actual.total - estimated
    if delta > 0:
        final_states = guard.backend.force_increment(keys, delta)
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
) -> "GuardedStream":
    """
    Streaming call path. Returns a GuardedStream context manager.
    The pre-call check runs immediately. Token usage is reconciled
    when the stream context manager exits.
    """
    estimated = guard.provider.estimate_tokens(kwargs)
    keys = _build_keys(guard)

    result = guard.backend.check_and_increment(keys, estimated)
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
    """
    Context manager that wraps the SDK stream context manager.
    Reconciles token usage on exit, including early exit.

    Usage mirrors the SDK exactly:
        with client.messages.stream(...) as stream:
            for text in stream.text_stream:
                print(text)

    On normal exit: usage is extracted from the final message, reconciled.
    On early exit (break, exception): the estimated token count is used as
    the final count. A warning is logged. This prevents silent undercount
    in the ledger.
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
        self._real_fn = real_fn
        self._call_kwargs = call_kwargs
        self._estimated = estimated
        self._keys = keys
        self._original_model = original_model
        self._guard = guard
        self._provider = provider
        self._stream_ctx: Any = None
        self._usage: TokenUsage | None = None

    def __enter__(self) -> Any:
        self._stream_ctx = self._real_fn(**self._call_kwargs).__enter__()
        return self._stream_ctx

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        result = self._stream_ctx.__exit__(exc_type, exc_val, exc_tb)

        # Extract usage from the completed stream if available
        try:
            usage = self._provider.extract_usage(self._stream_ctx)
        except Exception:
            usage = None

        if usage is None or usage.total == 0:
            # Early exit or provider gave no usage, fall back to estimate
            import logging
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
```

**Key points about streaming:**

The pre-call check (`check_and_increment`) runs in `call_stream()` before the
context manager is returned to the developer. If the budget is exceeded, `BudgetExceededError`
is raised before the stream is opened. The developer never enters the `with` block.

Token usage reconciliation runs in `GuardedStream.__exit__()`. This fires whether
the developer exits normally, via `break`, or via an exception. The pre-call estimate
is already in the ledger. If usage data is available, the delta is reconciled via
`force_increment`. If not (early exit, exception, provider returned nothing), the
estimate stands and a warning is logged. The ledger is never in an unknown state.

---

### anthropic.py: GuardedAnthropic and GuardedMessages

```python
from typing import Any
import anthropic
from tokencap.core.guard import Guard
from tokencap.interceptor.base import call, call_async, call_stream


class GuardedMessages:
    """
    Proxy for anthropic.resources.Messages.
    Intercepts create() and stream(). Everything else passes through.
    """

    def __init__(
        self,
        messages: anthropic.resources.Messages,
        guard: Guard,
        provider: Any,
        *,
        is_async: bool,
    ) -> None:
        self._messages = messages
        self._guard = guard
        self._provider = provider
        self._is_async = is_async

    def create(self, **kwargs: Any) -> anthropic.types.Message:
        if self._is_async:
            return call_async(self._messages.create, kwargs, self._guard, self._provider)
        return call(self._messages.create, kwargs, self._guard, self._provider)

    def stream(self, **kwargs: Any) -> "GuardedStream":
        return call_stream(self._messages.stream, kwargs, self._guard, self._provider)

    def __getattr__(self, name: str) -> Any:
        # batch, count_tokens, and any other messages attributes pass through
        return getattr(self._messages, name)


class GuardedAnthropic:
    """
    Proxy for anthropic.Anthropic and anthropic.AsyncAnthropic.

    @property intercepts .messages before __getattr__ is considered.
    This is how the interception works, not through __getattr__.

    All client-returning methods (with_options, with_raw_response,
    with_streaming_response) are implemented explicitly with *args/**kwargs
    passthrough so tokencap never owns the SDK signature.

    Everything else delegates via __getattr__.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        guard: Guard,
        provider: Any,
    ) -> None:
        self._client = client
        self._guard = guard
        self._provider = provider
        self._is_async = isinstance(client, anthropic.AsyncAnthropic)

    @property
    def messages(self) -> GuardedMessages:
        return GuardedMessages(
            self._client.messages,
            self._guard,
            self._provider,
            is_async=self._is_async,
        )

    def with_options(self, *args: Any, **kwargs: Any) -> "GuardedAnthropic":
        return GuardedAnthropic(
            self._client.with_options(*args, **kwargs), self._guard, self._provider
        )

    @property
    def with_raw_response(self) -> "GuardedAnthropic":
        return GuardedAnthropic(self._client.with_raw_response, self._guard, self._provider)

    @property
    def with_streaming_response(self) -> "GuardedAnthropic":
        return GuardedAnthropic(self._client.with_streaming_response, self._guard, self._provider)

    def get_status(self) -> StatusResponse:
        return self._guard.get_status()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
```

**Why `@property` and not `__getattr__` for `.messages`:**
Python checks the class `__dict__` and MRO for data descriptors (like `@property`)
before calling `__getattr__`. If `messages` were not a property, `__getattr__`
would fire and return `self._client.messages`, the real SDK object, bypassing
the entire interception chain.

**Why `GuardedMessages` also has `__getattr__`:**
`client.messages.batch`, `client.messages.count_tokens`, and any other method on
the Anthropic messages resource pass through to the real `self._messages` object.
They are not intercepted and not tracked.

**Async detection:**
`tokencap.wrap()` checks `isinstance(client, anthropic.AsyncAnthropic)` and calls
`create_async` instead of `create` on the `GuardedMessages` accordingly. The
developer always calls `client.messages.create(...)`. The routing to async is
internal.

---

### openai.py: GuardedOpenAI and GuardedCompletions

```python
from typing import Any
import openai
from tokencap.core.guard import Guard
from tokencap.interceptor.base import call, call_async, call_stream


class GuardedCompletions:
    """
    Proxy for openai.resources.chat.Completions.
    Intercepts create(). Everything else passes through.
    """

    def __init__(
        self,
        completions: openai.resources.chat.Completions,
        guard: Guard,
        provider: Any,
        *,
        is_async: bool,
    ) -> None:
        self._completions = completions
        self._guard = guard
        self._provider = provider
        self._is_async = is_async

    def create(self, **kwargs: Any) -> Any:
        # For streaming OpenAI calls, inject stream_options to get usage data.
        # OpenAI does not return token usage in streaming by default.
        # This is done in a copy of kwargs, never mutates the caller's dict.
        if kwargs.get("stream"):
            kwargs = dict(kwargs)
            kwargs.setdefault(
                "stream_options", {"include_usage": True}
            )
            return call_stream(self._completions.create, kwargs, self._guard, self._provider)
        if self._is_async:
            return call_async(self._completions.create, kwargs, self._guard, self._provider)
        return call(self._completions.create, kwargs, self._guard, self._provider)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)


class GuardedChat:
    """Proxy for openai.resources.Chat. Intercepts .completions."""

    def __init__(self, chat: Any, guard: Guard, provider: Any, *, is_async: bool) -> None:
        self._chat = chat
        self._guard = guard
        self._provider = provider
        self._is_async = is_async

    @property
    def completions(self) -> GuardedCompletions:
        return GuardedCompletions(
            self._chat.completions, self._guard, self._provider, is_async=self._is_async
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class GuardedOpenAI:
    """
    Proxy for openai.OpenAI and openai.AsyncOpenAI.
    Same pattern as GuardedAnthropic. Intercepts .chat via @property.
    """

    def __init__(self, client: openai.OpenAI, guard: Guard, provider: Any) -> None:
        self._client = client
        self._guard = guard
        self._provider = provider
        self._is_async = isinstance(client, openai.AsyncOpenAI)

    @property
    def chat(self) -> GuardedChat:
        return GuardedChat(
            self._client.chat, self._guard, self._provider, is_async=self._is_async
        )

    def with_options(self, *args: Any, **kwargs: Any) -> "GuardedOpenAI":
        return GuardedOpenAI(
            self._client.with_options(*args, **kwargs), self._guard, self._provider
        )

    @property
    def with_raw_response(self) -> "GuardedOpenAI":
        return GuardedOpenAI(self._client.with_raw_response, self._guard, self._provider)

    @property
    def with_streaming_response(self) -> "GuardedOpenAI":
        return GuardedOpenAI(self._client.with_streaming_response, self._guard, self._provider)

    def get_status(self) -> StatusResponse:
        return self._guard.get_status()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
```

**OpenAI streaming usage injection:**
OpenAI's streaming API does not return token usage by default. Without
`stream_options={"include_usage": True}`, `extract_usage()` gets zero tokens
and reconciliation never fires. `GuardedCompletions.create()` detects `stream=True`
and injects `stream_options` into a copy of kwargs using `setdefault` (so it
does not override the developer's own `stream_options` if they set one).
This is done before calling `call_stream()`.

**Two levels of proxying for OpenAI:**
OpenAI's resource hierarchy is `client.chat.completions.create()`. This requires
two proxy layers: `GuardedChat` intercepts `.completions` via `@property`,
`GuardedCompletions` intercepts `.create()`. The same `@property` pattern applies
at each level.

---

### Attribute lookup resolution order (summary)

For any attribute access on a guarded client, Python resolves in this order:

```
1. Data descriptors on the class (@property, __slots__)
   -> .messages on GuardedAnthropic returns GuardedMessages
   -> .chat on GuardedOpenAI returns GuardedChat

2. Instance __dict__
   -> _client, _guard stored here

3. Non-data descriptors and class attributes
   -> with_options, with_raw_response, with_streaming_response

4. __getattr__ (only if nothing above matched)
   -> api_key, base_url, models, beta, and everything else
   -> delegates to self._client unchanged
```

This ordering is why `@property` works for interception and `__getattr__` works
for pass-through. They occupy different slots in the lookup chain.

---

## The Policy Engine (core/policy.py)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from tokencap.status.api import StatusResponse


@dataclass
class Action:
    """A single action executed when a threshold is crossed."""
    kind: Literal["WARN", "BLOCK", "DEGRADE", "WEBHOOK"]
    webhook_url: str | None = None                              # WEBHOOK only
    degrade_to: str | None = None                              # DEGRADE only
    callback: Callable[[StatusResponse], None] | None = None   # WARN only


@dataclass
class Threshold:
    """
    A trigger point within a dimension. Fires at most once per budget period.

    at_pct must be in the range (0.0, 1.0]. Values outside this range raise
    ValueError in __post_init__.
    """
    at_pct: float           # e.g. 0.8 fires at 80% of the limit
    actions: list[Action]   # executed in order when this threshold is newly crossed

    def __post_init__(self) -> None:
        if not (0.0 < self.at_pct <= 1.0):
            raise ValueError(
                f"Threshold.at_pct must be in (0.0, 1.0], got {self.at_pct}"
            )


@dataclass
class DimensionPolicy:
    """Budget configuration for a single named dimension."""
    limit: int                                        # tokens
    thresholds: list[Threshold] = field(default_factory=list)
    reset_every: Literal["day", "hour"] | None = None  # v0.2: not yet implemented

    def __post_init__(self) -> None:
        # Ensure thresholds are always evaluated in ascending order
        self.thresholds = sorted(self.thresholds, key=lambda t: t.at_pct)


@dataclass
class Policy:
    """Complete budget policy across all dimensions."""
    dimensions: dict[str, DimensionPolicy]
    name: str = "default"
```

**Threshold fire-once rule:** A threshold fires exactly once per budget period
per `BudgetKey`. After firing, the backend records it as fired. It does not
re-fire until the period resets via `backend.reset()`. This prevents alert storms
when many calls are made after crossing a threshold.

**No thresholds means tracking only:** A `DimensionPolicy` with an empty thresholds
list tracks token usage but takes no action at any usage level. This is intentional
and correct. Visibility without enforcement is a valid use case. See D-019.

**Action execution order:** All actions on a threshold run in list order. If BLOCK
is present, it executes last. WARN and WEBHOOK actions fire first so the caller
has observable context before the exception is raised.

---

## The Guard (core/guard.py)

Stateless config holder and factory. Owns the backend, policy, identifiers, and
OTEL emitter. Does not hold provider or current_model — those are call-time state
that lives on the wrapped client (GuardedAnthropic / GuardedOpenAI). Guard creates
wrapped clients via wrap_anthropic() and wrap_openai(), which instantiate the
appropriate provider and pass it to the wrapper. This allows a single Guard to wrap
both Anthropic and OpenAI clients without cross-contamination.

Instantiated once per application lifetime (or once globally via the drop-in API).

```python
class Guard:
    def __init__(
        self,
        policy: Policy,
        identifiers: dict[str, str] | None = None,
        backend: Backend | None = None,
        otel_enabled: bool = True,
        quiet: bool = False,
    ) -> None:
        """
        Args:
            policy: Budget policy defining dimensions, limits, and thresholds.
            identifiers: Maps dimension names to their runtime identifier strings.
                e.g. {"session": "session_abc123", "tenant_daily": "acme:2026-03-27"}
                Dimensions not listed here receive an auto-generated UUID identifier.
            backend: Storage backend. Defaults to SQLiteBackend().
            otel_enabled: Whether to emit OTEL metrics and spans.
            quiet: Suppress the startup stdout message. Default False.
        """
        ...

    def wrap_anthropic(self, client: anthropic.Anthropic) -> GuardedAnthropic: ...
    def wrap_openai(self, client: openai.OpenAI) -> GuardedOpenAI: ...
    def get_status(self) -> StatusResponse: ...
    def teardown(self) -> None: ...
```

Guard calls `backend.set_limit()` for every configured dimension during `__init__`.
Limits are guaranteed to be registered before any call is intercepted.

**How cross-agent budget sharing works**

tokencap does not do anything special to share budgets across agents. Sharing
happens because multiple Guard instances write to the same backend counter, and
a counter is identified solely by its `(dimension, identifier)` pair.

Two agents share a budget if and only if:
- They use the same dimension name
- They use the same identifier string for that dimension
- They point to the same backend (same SQLite file path or same Redis instance)

Two agents have independent budgets if they use different identifier strings,
even if they share a backend.

The developer controls identifiers. tokencap does not generate or manage them.

**Identifier patterns**

A `run` identifier is typically unique per pipeline execution. Each run gets
its own counter, so agents within the same run share a budget, but different
runs do not.

A `tenant_daily` identifier is shared across all agents serving the same tenant
on the same day. All agents for that tenant increment the same counter.

**Concrete example: three agents sharing a daily tenant budget**

```python
# All three agents use the same identifier for "tenant_daily".
# They point to the same Redis instance.
# Every call from any of them debits the same counter.

shared_backend = RedisBackend("redis://redis-host:6379")
shared_identifiers = {"tenant_daily": "acme:2026-03-27"}

agent_a = Guard(policy=policy, identifiers=shared_identifiers, backend=shared_backend)
agent_b = Guard(policy=policy, identifiers=shared_identifiers, backend=shared_backend)
agent_c = Guard(policy=policy, identifiers=shared_identifiers, backend=shared_backend)
```

With SQLite on the same machine, replace `shared_backend` with
`SQLiteBackend(path="/shared/tokencap.db")` and the same sharing behaviour
applies without Redis.

---

## Public API (__init__.py)

`__init__.py` contains the module-level drop-in API: `wrap()`, `init()`,
`get_status()`, `teardown()`, and the thread-safe global Guard singleton.
No business logic beyond what is required to implement these four functions.
All other logic lives in `guard.py`, the interceptors, and the backends.
All public symbols are listed explicitly in `__all__`.

Three usage tiers. All use `wrap()`. Each tier adds opt-in configuration.
Defaults are always documented, never silent.

### Tier 1: wrap(client) — tracking only

```python
import tokencap
import anthropic

client = tokencap.wrap(anthropic.Anthropic())
response = client.messages.create(...)
print(tokencap.get_status())
```

`wrap()` creates an implicit global Guard with these defaults:

- Dimension: `"session"` with an auto-generated UUID identifier
- Backend: `SQLiteBackend("tokencap.db")` in the current working directory
- No thresholds: usage is tracked, nothing is enforced

On first call, tokencap prints to stdout:

```
[tokencap] session started: session=<uuid> backend=sqlite:tokencap.db (no limit set)
```

### Tier 2: wrap(client, limit=N) — hard limit

```python
client = tokencap.wrap(anthropic.Anthropic(), limit=50_000)
```

Equivalent to a `"session"` dimension with a BLOCK threshold at 100%.
Auto UUID session identifier. Same SQLite default backend.

```
[tokencap] session started: session=<uuid> backend=sqlite:tokencap.db limit=50000 tokens
```

### Tier 3: wrap(client, policy=...) — full policy control

```python
import tokencap
import anthropic

policy = tokencap.Policy(
    dimensions={
        "session": tokencap.DimensionPolicy(
            limit=50_000,
            thresholds=[
                tokencap.Threshold(at_pct=0.8, actions=[tokencap.Action(kind="WARN")]),
                tokencap.Threshold(at_pct=1.0, actions=[tokencap.Action(kind="BLOCK")]),
            ],
        ),
    }
)

client = tokencap.wrap(anthropic.Anthropic(), policy=policy)
response = client.messages.create(...)
print(tokencap.get_status())
tokencap.teardown()
```

`limit` and `policy` are mutually exclusive. Passing both raises
`ConfigurationError`.

### Advanced: init() for pre-configuration

`init()` is optional. Use it when you need to configure the global Guard
before the first `wrap()` call, or when sharing state across multiple
`wrap()` calls with different clients.

```python
tokencap.init(
    policy=my_policy,
    identifiers={"session": "session_abc123", "tenant_daily": "acme:2026-03-27"},
)
anthropic_client = tokencap.wrap(anthropic.Anthropic())
openai_client = tokencap.wrap(openai.OpenAI())
```

### Advanced: explicit Guard for multiple guards in one process

```python
from tokencap import Guard, Policy, DimensionPolicy
from tokencap.backends.redis import RedisBackend

guard = Guard(
    policy=Policy(dimensions={"session": DimensionPolicy(limit=50_000)}),
    identifiers={"session": "session_abc123"},
    backend=RedisBackend("redis://localhost:6379"),
)
client = guard.wrap_anthropic(anthropic.Anthropic())
```

### Default behaviour table

| What you write | Dimension | Identifier | Enforcement | Backend |
|---|---|---|---|---|
| `wrap(client)` | `"session"` | auto UUID | none, tracking only | SQLite |
| `wrap(client, limit=N)` | `"session"` | auto UUID | BLOCK at 100% | SQLite |
| `wrap(client, policy=...)` | as configured | auto UUID per dim | as configured | SQLite |
| `init(policy=...) + wrap(client)` | as configured | as configured | as configured | as configured |

Defaults are printed to stdout on first call in all cases.
Stdout output can be suppressed with `quiet=True` on `wrap()` or `init()`.

**Public API surface (`__all__` in __init__.py):**

| Symbol | Description |
|---|---|
| `wrap(client, limit=None, policy=None, quiet=False)` | Wrap client, progressive config |
| `init(policy, identifiers, backend, otel_enabled, quiet)` | Pre-configure global Guard (optional) |
| `get_status()` | Returns `StatusResponse` from the global Guard |
| `teardown()` | Tear down global Guard, close backend connections |
| `Guard` | Explicit-mode entry point |
| `Policy` | Top-level policy container |
| `DimensionPolicy` | Per-dimension limit and threshold configuration |
| `Threshold` | Threshold trigger definition |
| `Action` | Policy action definition |
| `BudgetExceededError` | Raised on BLOCK, carries full `CheckResult` |
| `BackendError` | Raised on unrecoverable storage failures |
| `StatusResponse` | Returned by `get_status()`. Carries per-dimension `BudgetState`, active policy name, and next unfired threshold. |
| `patch(limit=None, policy=None, quiet=False)` | Monkey-patch SDK constructors for framework integration. See D-050. |
| `unpatch()` | Reverse all monkey-patches applied by `patch()` |

All other symbols are internal and may change without notice.

### Patch mode (patch())

#### Why patch mode exists

Most agent frameworks — LangChain, CrewAI, AutoGen, LlamaIndex — construct
their own SDK client instances internally. The developer does not call
`anthropic.Anthropic()` directly; the framework does. `wrap()` cannot intercept
these because it requires a reference to the client object. `patch()` solves
this by intercepting at the constructor level: once patched, every
`anthropic.Anthropic()` call anywhere in the process returns a
`GuardedAnthropic` instead.

#### How the mechanism works

`patch()` stores the original classes `anthropic.Anthropic`,
`anthropic.AsyncAnthropic`, `openai.OpenAI`, and `openai.AsyncOpenAI`. It
replaces each in the module namespace with a factory function that calls the
original constructor, then wraps the newly constructed client against the global
Guard. `unpatch()` restores all original classes and calls `teardown()` to
clear the global Guard.

The interception happens at construction time, not at import time. Clients
constructed before `patch()` is called are not affected. Clients constructed
after `patch()` is called are automatically wrapped.

`patch()` accepts the same `limit=` and `policy=` parameters as `wrap()`.

#### Trade-offs vs wrap()

| | `wrap()` | `patch()` |
|---|---|---|
| Client construction | Developer-controlled | Framework-controlled |
| Testability | Explicit, easy to mock | Global side effect |
| Status call | `client.get_status()` | `tokencap.get_status()` |
| Global side effects | No | Yes |
| Recommended for | Direct SDK use, libraries | Framework integration |

With `wrap()`, the developer holds a reference to the wrapped client and can
call `client.get_status()` directly. With `patch()`, tokencap manages the
clients internally and status is only available via `tokencap.get_status()`.

`wrap()` is recommended for direct SDK use. `patch()` is recommended for
framework integration where client construction is not developer-controlled.

#### Supported frameworks

`patch()` works with any framework that constructs Anthropic or OpenAI clients
internally, including: LangChain, CrewAI, LlamaIndex, AutoGen, and the OpenAI
Agents SDK. No framework-specific configuration is needed.

#### Known limitations

- Only clients constructed after `patch()` is called are intercepted. Existing
  client instances are not retroactively wrapped.
- `isinstance(wrapped_client, anthropic.Anthropic)` returns `False`. `.pyi`
  stub files planned for v0.2 will address type checker compatibility.
- `patch()` is a global side effect. It is not suitable for library code that
  will be imported by others. Use `wrap()` in libraries. `patch()` is for
  application-level agent code only.
- Backend calls in `call_async()` are synchronous. See the async blocking note
  in the interceptor section.

#### Cleanup

Always call `tokencap.unpatch()` when done:

```python
tokencap.patch(limit=50_000)
try:
    # run your agent
finally:
    tokencap.unpatch()
```

---

## OTEL Telemetry (telemetry/otel.py)

Optional. Guard all imports at module level:

```python
try:
    from opentelemetry import metrics, trace
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
```

All emission functions check `OTEL_AVAILABLE` before doing anything. They never
raise. A telemetry failure must never surface to user code.

Metrics emitted after each post-call reconciliation:

| Metric | Type | Labels |
|--------|------|--------|
| `tokencap.tokens.used` | Counter | `provider`, `model`, `dimension` |
| `tokencap.tokens.remaining` | Gauge | `dimension`, `identifier` |
| `tokencap.budget.pct_used` | Gauge | `dimension`, `identifier` |
| `tokencap.policy.action_fired` | Counter | `action_kind`, `dimension` |

Span attributes per call:

| Attribute | Value |
|-----------|-------|
| `tokencap.provider` | `"anthropic"` or `"openai"` |
| `tokencap.model.original` | Model as requested by caller |
| `tokencap.model.actual` | Model after any DEGRADE swap |
| `tokencap.tokens.estimated` | Pre-call estimate |
| `tokencap.tokens.actual` | Post-call actual |
| `tokencap.tokens.delta` | `actual - estimated` |
| `tokencap.allowed` | `True` / `False` |
| `tokencap.dim.<n>.pct_used` | Per-dimension percentage at call time |

---

## Status API (status/api.py)

```python
from __future__ import annotations
from dataclasses import dataclass
from tokencap.core.types import BudgetState


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
    timestamp: str                        # ISO 8601 UTC
    dimensions: dict[str, BudgetState]    # keyed by dimension name
    active_policy: str                    # policy.name
    next_threshold: ThresholdInfo | None  # nearest unfired threshold
```

`get_status()` is a synchronous read that calls `backend.get_states()` only. It
never writes and never blocks on the call path. Safe to call inside agent loops
or from multiple threads concurrently.

`next_threshold` excludes BLOCK thresholds. A BLOCK threshold is not "upcoming"
— it fires unconditionally on every call once crossed (D-037). `next_threshold`
only considers WARN, WEBHOOK, and DEGRADE thresholds that follow the fire-once
rule. See D-044.

---

## Dependencies

### Required (always installed)
None. Zero required dependencies beyond Python 3.9+.

### Optional extras

| Extra | Installs | When to use |
|-------|----------|-------------|
| `tokencap[anthropic]` | `anthropic` | Anthropic SDK wrapping |
| `tokencap[openai]` | `openai`, `tiktoken` | OpenAI SDK wrapping |
| `tokencap[all]` | `anthropic`, `openai`, `tiktoken` | Both providers |

For distributed mode, install `redis` independently: `pip install redis`.
For OTEL, install `opentelemetry-api` independently: `pip install opentelemetry-api`.

These are not tokencap extras because they are independent libraries with their
own release cycles. tokencap imports them lazily and handles absence gracefully
in both cases.

The core library must import cleanly with zero optional dependencies installed.
Every optional import is guarded. Every missing-dependency error message tells the
user exactly what to run.

---

## Phases

### Phase 1: Foundation (types + backends)

Deliverables:
- `tokencap/core/types.py`: all shared types, fully typed, frozen where appropriate
- `tokencap/core/exceptions.py`: `BudgetExceededError`, `BackendError`, `ConfigurationError`
- `tokencap/backends/protocol.py`: `Backend` Protocol including `force_increment`, `is_threshold_fired`, `mark_threshold_fired`
- `tokencap/backends/sqlite.py`: `SQLiteBackend`, atomic transactions
- `tokencap/py.typed`: PEP 561 marker file (empty)
- `pyproject.toml`: package metadata, optional dependency groups, no required deps
- `Makefile`: `lint` (ruff + mypy), `test` (pytest), `build`, `publish` targets
- `tests/unit/test_backends.py`: full `SQLiteBackend` coverage
- `tests/conftest.py`: shared fixtures

Acceptance criteria:
- `SQLiteBackend` passes concurrent write test: 10 threads × 100 increments of
  1 token each against a single key with limit 2000. Final `used_tokens` must
  equal exactly 1000.
- `check_and_increment` returns `allowed=False` with zero increment when limit exceeded
- `force_increment` succeeds and increments even when limit is exceeded
- `mypy --strict` passes on all Phase 1 files with zero errors
- `pip install -e .` with no extras succeeds and `import tokencap` works
- CI workflow present at `.github/workflows/ci.yml` and triggers on
  pull_request to main and push to main only
- CI runs as a single job covering Python 3.9, 3.10, 3.11, 3.12, 3.13
- `make lint` and `make test` both pass in CI on all Python versions
- All unit tests mock at the function/class level with no real I/O
  beyond `tmp_path` and no real API calls
- Test file naming mirrors the source tree 1:1

### Phase 2: Providers + Interceptor

Deliverables:
- `tokencap/providers/protocol.py`: `Provider` Protocol, fully typed
- `tokencap/providers/anthropic.py`: `AnthropicProvider`
- `tokencap/providers/openai.py`: `OpenAIProvider`
- `tokencap/interceptor/base.py`: module-level functions `call()`, `call_async()`, `call_stream()`, `GuardedStream`
- `tokencap/interceptor/anthropic.py`: `GuardedAnthropic`
- `tokencap/interceptor/openai.py`: `GuardedOpenAI`
- `tokencap/status/api.py`: `StatusResponse` stub for `TYPE_CHECKING` import in `policy.py`
- `tests/unit/test_providers.py`
- `tests/unit/test_interceptor.py`
- Unit tests for all Phase 2 components
- Integration tests for the full Anthropic and OpenAI call paths,
  HTTP layer mocked with pytest-httpx

Acceptance criteria:
- Token estimation within 10% of actual for standard message payloads (fixtures,
  no live API calls)
- Post-call reconciliation uses `force_increment`, never `check_and_increment`
- `GuardedAnthropic` passes all attribute access to underlying client except `.messages`
- `GuardedOpenAI` passes all attribute access to underlying client except `.chat`
- BLOCK raises `BudgetExceededError` before the API call is made (verified with mock)
- DEGRADE swaps model in a copy of `request_kwargs`. Caller's dict is not mutated.
- WEBHOOK fires in a background thread, does not block the call path
- `mypy --strict` passes on all Phase 2 files
- All unit and integration tests pass with `make test`
- No real API calls and no credentials required for any test
- `mypy --strict` passes on all new source files under `tokencap/`

### Phase 3: Policy Engine + Guard + Public API

Deliverables:
- `tokencap/core/policy.py`: `Policy`, `DimensionPolicy`, `Threshold`, `Action`
- `tokencap/core/guard.py`: `Guard` orchestrator
- `tokencap/__init__.py`: full public API with explicit `__all__`
- `tokencap/status/api.py`: `StatusResponse`, `ThresholdInfo`, `get_status()`
- `tests/unit/test_policy.py`
- Unit tests for all Phase 3 components
- Integration tests covering the full drop-in API end-to-end,
  all four action kinds, and multi-dimensional budgets,
  HTTP layer mocked with pytest-httpx

Acceptance criteria:
- `tokencap.init()` + `tokencap.wrap()` + `client.messages.create()` works
  end-to-end with a mocked Anthropic client
- WARN fires callback and call proceeds
- BLOCK fires any preceding WARN/WEBHOOK actions first, then raises `BudgetExceededError`
- DEGRADE swaps model transparently, original `request_kwargs` dict is not mutated
- WEBHOOK fires async, verified with a local test HTTP server
- `Threshold(at_pct=1.5)` raises `ValueError` at construction time
- `Threshold(at_pct=0.0)` raises `ValueError` at construction time
- Threshold does not re-fire in the same budget period (fire-once rule verified)
- `get_status()` returns correct `BudgetState` for all configured dimensions
- `mypy --strict` passes on all Phase 3 files
- All unit and integration tests pass with `make test`
- No real API calls and no credentials required for any test
- `mypy --strict` passes on all new source files under `tokencap/`

### Phase 4: Redis Backend + OTEL

Deliverables:
- `tokencap/backends/redis.py`: `RedisBackend` with two Lua scripts
- `tokencap/telemetry/otel.py`: OTEL emission, no-ops if not installed
- `tokencap/__init__.py`: `patch()` and `unpatch()` added to public API
  for opt-in framework integration via monkey-patching
- Integration test: backend test suite parametrized over both backends
- `tests/unit/test_backends.py` updated for `RedisBackend` (mocked `redis-py`)

Acceptance criteria:
- `RedisBackend` passes the same concurrent write test as `SQLiteBackend`
- `force_increment` on `RedisBackend` succeeds even when limit is exceeded
- Switching `backend=RedisBackend(...)` produces identical behaviour to `SQLiteBackend`
  (parametrized test suite)
- `import tokencap` with no optional deps: no mention of Redis or OTEL in output
- `import tokencap` with `opentelemetry-api` absent: OTEL calls are no-ops, no error
- `RedisBackend(...)` with `redis` absent: raises `ImportError` with install command
- `mypy --strict` passes on all Phase 4 files
- `patch()` wraps all `anthropic.Anthropic()` and `openai.OpenAI()` clients
  constructed after the call
- `unpatch()` fully restores original SDK constructors
- `patch()` raises `ConfigurationError` when called twice without `unpatch()`
- `patch` and `unpatch` are listed in `__all__`

### Phase 5: Tests + Docs + Publish

Deliverables:
- `tests/integration/test_full_pipeline.py`: real API calls, skipped without keys
- `README.md`: complete, both modes shown, all actions documented, examples tested
- `DECISIONS.md`: finalised with all decisions from the build
- `CLAUDE.md`: finalised standing rules
- PyPI publish via `make publish`
- dev.to post draft

Acceptance criteria:
- `mypy --strict` passes clean across the entire codebase
- All unit tests pass with zero failures and zero unexpected skips
- `pip install tokencap` then README quickstart works against real provider APIs
- Package appears on PyPI within 5 minutes of `make publish`
