# tokencap

[![CI](https://github.com/pykul/tokencap/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pykul/tokencap/actions/workflows/ci.yml)

**Token usage visibility and budget enforcement for AI agents. Works out of the box. Scales to Redis when you need it.**

```bash
pip install tokencap
```

---

## The problem

AI agents are unpredictable by design. An agent might make 3 LLM calls or 300. Without
visibility into what each agent is spending, and without the ability to enforce limits,
you find out about runaway costs from the bill, not an alert.

tokencap gives you both. See exactly what every agent is spending. Enforce hard limits,
soft warnings, model degradation, or webhook alerts, whichever policy fits your use case.

---

## Quickstart

Two lines. Your existing code does not change.

```python
import tokencap
import anthropic

client = tokencap.wrap(anthropic.Anthropic())

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Summarize this document."}],
)

print(tokencap.get_status())
```

On the first call, tokencap prints what it is doing so there are no surprises:

```
[tokencap] session started: session=a3f1c2d4 backend=sqlite:tokencap.db (no limit set)
```

By default, tokencap tracks token usage with no enforcement. Add a limit to change that.

---

## The wrapped client

`tokencap.wrap()` returns a client that proxies the original through `__getattr__`
delegation. The common paths work unchanged. Here is exactly what is intercepted
and what is not.

**Intercepted (tokencap tracks and enforces these):**
- `client.messages.create()`: sync
- `client.messages.stream()`: streaming
- `client.messages.create()` on async client: awaitable
- `client.with_options(...)`: returns a new wrapped client
- `client.with_raw_response(...)`: returns a new wrapped client
- `client.with_streaming_response(...)`: returns a new wrapped client

**Pass-through (tokencap does not see these calls):**
- `client.models.list()` and all non-messages endpoints
- `client.beta.messages.create()`: beta features, pass through untracked
- `client.messages.batch`: batch API, passes through untracked
- All attributes: `client.api_key`, `client.base_url`, etc.

**Client-returning methods return a wrapped client.**
`with_options()`, `with_raw_response()`, and `with_streaming_response()` all
create new client instances in the SDK. tokencap intercepts all three and wraps
the result, so enforcement stays active:

```python
client = tokencap.wrap(anthropic.Anthropic())
client2 = client.with_options(timeout=30)       # GuardedAnthropic
client3 = client.with_raw_response()             # GuardedAnthropic
client4 = client.with_streaming_response()       # GuardedAnthropic
```

**`isinstance` returns False.**
`isinstance(wrapped_client, anthropic.Anthropic)` is `False`. This is a known
limitation of the proxy pattern in Python. If your code type-checks the client,
use the wrapper type or restructure to avoid the check. There is no workaround
that does not involve modifying the Anthropic SDK itself.

For OpenAI the same rules apply: `chat.completions.create()` is intercepted,
everything else passes through.

```python
client = tokencap.wrap(anthropic.Anthropic())

# These are tracked and enforced
response = client.messages.create(model="claude-sonnet-4-6", ...)

with client.messages.stream(model="claude-sonnet-4-6", ...) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

# These pass through untracked
models = client.models.list()
api_key = client.api_key

# Async works the same way
async_client = tokencap.wrap(anthropic.AsyncAnthropic())
response = await async_client.messages.create(model="claude-sonnet-4-6", ...)
```

---

## Adding a limit

One argument.

```python
client = tokencap.wrap(anthropic.Anthropic(), limit=50_000)
```

When the session hits 50,000 tokens, `BudgetExceededError` is raised before the next call
is made. tokencap tells you what it set up:

```
[tokencap] session started: session=a3f1c2d4 backend=sqlite:tokencap.db limit=50000 tokens
```

---

## What the defaults are

tokencap never does anything silently. When you call `wrap()` without `init()`,
these defaults apply:

| Setting | Default value |
|---|---|
| Dimension name | `"session"` |
| Session identifier | auto-generated UUID (printed on first call) |
| Backend | SQLite file `tokencap.db` in the current directory |
| Enforcement | none (tracking only) unless `limit=` is passed |

Pass `quiet=True` to `wrap()` or `init()` to suppress the startup message.

---

## Checking status

```python
status = tokencap.get_status()

for dim, state in status.dimensions.items():
    print(f"{dim}: {state.used:,} / {state.limit:,} tokens ({state.pct_used:.1%})")
    print(f"  cost so far: ${state.cost_usd:.4f}")

# session: 31,200 / 50,000 tokens (62.4%)
#   cost so far: $0.0936
```

---

## Policy actions

The `limit=` shorthand always uses BLOCK. For more control, define thresholds explicitly.

### BLOCK: raise an exception before the call

```python
tokencap.DimensionPolicy(
    limit=50_000,
    thresholds=[
        tokencap.Threshold(
            at_pct=1.0,
            actions=[tokencap.Action(kind="BLOCK")],
        ),
    ],
)
```

Raises `tokencap.BudgetExceededError` before the API call is made. The exception carries
the full state of every dimension so you can see which one was violated and by how much.

```python
try:
    response = client.messages.create(...)
except tokencap.BudgetExceededError as e:
    for dim in e.check_result.violated:
        state = e.check_result.states[dim]
        print(f"{dim} exceeded: {state.used:,} / {state.limit:,} tokens")
```

### WARN: fire a callback and continue

```python
def on_warn(status):
    print(f"Warning: {status.dimensions['session'].pct_used:.0%} of session budget used")

tokencap.Threshold(
    at_pct=0.8,
    actions=[tokencap.Action(kind="WARN", callback=on_warn)],
)
```

The callback fires once when the threshold is first crossed. The call proceeds.

### DEGRADE: swap to a cheaper model transparently

```python
tokencap.Threshold(
    at_pct=0.9,
    actions=[tokencap.Action(kind="DEGRADE", degrade_to="claude-haiku-4-5")],
)
```

From this threshold onward, all calls use the degraded model. The calling code
never changes. OTEL records both the original and actual model on every span.

### WEBHOOK: fire an HTTP POST and continue

```python
tokencap.Threshold(
    at_pct=0.8,
    actions=[tokencap.Action(kind="WEBHOOK", webhook_url="https://your-app.com/alerts")],
)
```

Posts a JSON payload with the full status to your endpoint. Fire-and-forget in a
background thread, does not add latency to the call path.

---

## Full policy

When you need more than a simple limit, use `init()` before `wrap()`.

```python
import tokencap
import anthropic

tokencap.init(
    policy=tokencap.Policy(
        dimensions={
            "session": tokencap.DimensionPolicy(
                limit=50_000,
                thresholds=[
                    tokencap.Threshold(at_pct=0.8, actions=[tokencap.Action(kind="WARN")]),
                    tokencap.Threshold(at_pct=0.95, actions=[tokencap.Action(kind="DEGRADE", degrade_to="claude-haiku-4-5")]),
                    tokencap.Threshold(at_pct=1.0, actions=[tokencap.Action(kind="BLOCK")]),
                ],
            ),
            "tenant_daily": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[
                    tokencap.Threshold(at_pct=1.0, actions=[tokencap.Action(kind="BLOCK")]),
                ],
            ),
        }
    ),
    identifiers={
        "session": "session_abc123",
        "tenant_daily": "acme:2026-03-27",
    },
)

client = tokencap.wrap(anthropic.Anthropic())
response = client.messages.create(...)
print(tokencap.get_status())
tokencap.teardown()
```

A call is blocked if any dimension is at its limit. All dimensions are checked
and incremented atomically. No partial updates.

---

## Multi-agent and distributed usage

Agents share a budget by using the same identifier for the same dimension. tokencap
does not wire agents together automatically. If two Guard instances use the same
identifier string and point at the same backend, they increment the same counter.
If they use different identifiers, their budgets are independent.

**Same machine, multiple agents or processes:** use SQLite with a shared file path.
No Redis required.

```python
from tokencap import Guard, Policy, DimensionPolicy, Threshold, Action
from tokencap.backends.sqlite import SQLiteBackend

policy = Policy(
    dimensions={
        "tenant_daily": DimensionPolicy(
            limit=1_000_000,
            thresholds=[Threshold(at_pct=1.0, actions=[Action(kind="BLOCK")])],
        ),
    }
)
shared = SQLiteBackend(path="/shared/tokencap.db")
shared_ids = {"tenant_daily": "acme:2026-03-27"}

agent_a = Guard(policy=policy, identifiers=shared_ids, backend=shared)
agent_b = Guard(policy=policy, identifiers=shared_ids, backend=shared)
```

**Across machines:** switch to Redis. The API is identical.

```python
from tokencap.backends.redis import RedisBackend

shared = RedisBackend("redis://redis-host:6379")
shared_ids = {"tenant_daily": "acme:2026-03-27"}

agent_a = Guard(policy=policy, identifiers=shared_ids, backend=shared)
agent_b = Guard(policy=policy, identifiers=shared_ids, backend=shared)
```

`policy` is defined the same way as in the SQLite example above.

All dimensions are checked and incremented atomically. No partial updates.

```bash
pip install redis
```

---

## Explicit mode

For applications that need multiple guards with different policies, or where a
global instance is not appropriate:

```python
from tokencap import Guard, Policy, DimensionPolicy, Threshold, Action
from tokencap.backends.redis import RedisBackend

guard = Guard(
    policy=Policy(
        name="production",
        dimensions={
            "session": DimensionPolicy(limit=50_000),
            "tenant_daily": DimensionPolicy(limit=1_000_000),
        },
    ),
    identifiers={
        "session": "session_abc123",
        "tenant_daily": "acme:2026-03-27",
    },
    backend=RedisBackend("redis://localhost:6379"),
)

anthropic_client = guard.wrap_anthropic(anthropic.Anthropic())
openai_client = guard.wrap_openai(openai.OpenAI())
```

Both clients share the same guard and the same budget dimensions.

---

## OTEL integration

tokencap emits OpenTelemetry metrics after every call if `opentelemetry-api` is
installed. No configuration required. It uses the globally configured tracer and
meter provider.

```bash
pip install opentelemetry-api
```

Metrics emitted per call:

| Metric | Type | Labels |
|---|---|---|
| `tokencap.tokens.used` | Counter | provider, model, dimension |
| `tokencap.tokens.remaining` | Gauge | dimension, identifier |
| `tokencap.budget.pct_used` | Gauge | dimension, identifier |
| `tokencap.call.cost_usd` | Histogram | provider, model |
| `tokencap.policy.action_fired` | Counter | action_kind, dimension |

Each call also produces a span with `tokencap.model.original` and
`tokencap.model.actual` (useful for tracking DEGRADE events), plus
token counts and per-dimension budget state.

If `opentelemetry-api` is not installed, all telemetry is a no-op. No errors,
no warnings, no effect on behavior.

---

## Supported providers

| Provider | Install | Token estimation |
|---|---|---|
| Anthropic | `pip install tokencap[anthropic]` | Anthropic SDK counter |
| OpenAI | `pip install tokencap[openai]` | tiktoken |

Estimation runs before the call. Actual usage is reconciled after. The delta is
debited automatically. You never pay twice.

---

## Supported models

**Anthropic:** claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5, claude-3-opus,
claude-3-sonnet, claude-3-haiku

**OpenAI:** gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-4, gpt-3.5-turbo, o1, o1-mini,
o3, o3-mini, o4-mini

Version-suffixed model names (e.g. `claude-sonnet-4-6-20251022`) fall back to
their base model pricing automatically.

---

## Installation

```bash
pip install tokencap[anthropic]   # Anthropic SDK + token estimation
pip install tokencap[openai]      # OpenAI SDK + tiktoken
pip install tokencap[all]         # both providers
```

For distributed mode across machines: `pip install redis`

For OTEL metrics and traces: `pip install opentelemetry-api`

Requires Python 3.9+.

---

## Why tokencap is easy to use

Most budget tools track dollars. The problem is that dollar cost changes every
time a provider reprices a model, and different call types (cached tokens, batch
API, streaming) cost different amounts. You end up with thresholds that silently
mean something different after a pricing update.

tokencap uses token counts directly. You set a limit of 50,000 tokens. That limit
means exactly the same thing regardless of which model you use, how the provider
prices it, or whether tokens are cached. Dollar cost is computed for display, but
it never drives enforcement decisions.

This also makes limits easy to reason about. If you know your task takes roughly
5,000 tokens per call and you want to cap at 10 calls, you set a limit of 50,000.
No conversion needed.

---

## How tokencap fits alongside other tools

**Observability platforms.** Platforms like LangSmith, Helicone, and infrastructure-level
AI monitoring tools give you dashboards, traces, and historical spend analysis. They
tell you what happened. tokencap enforces policy before and during calls. Many teams
use both: an observability platform for the ops dashboard, tokencap for enforcement
in the application code. They connect via tokencap's OTEL emission.

**No tool at all.** The most common situation. Most teams set a provider-level
spending cap and find out about runaway costs from the bill. tokencap is for teams
who want enforcement in the code, not reactive alerts after the money is spent.

## API reference

### Module-level functions

```python
tokencap.wrap(client, limit=None, quiet=False)
```
Wraps an Anthropic or OpenAI client (sync or async). Returns a guarded client of
the same type. If called without `init()`, creates an implicit Guard with defaults.
`limit` is a token count shorthand for BLOCK at 100%. `quiet` suppresses the startup
message.

```python
tokencap.init(policy, identifiers=None, backend=None, otel_enabled=True, quiet=False)
```
Sets up the global Guard instance. Call before `wrap()` when you need full policy
control. If you skip `init()` and call `wrap()` directly, the Guard is created
with defaults.

```python
tokencap.get_status()  # returns StatusResponse
tokencap.teardown()    # closes backend connections, resets global Guard
```

### Guard (explicit mode)

```python
from tokencap import Guard

guard = Guard(policy, identifiers=None, backend=None, otel_enabled=True, quiet=False)
guard.wrap_anthropic(client)  # returns GuardedAnthropic
guard.wrap_openai(client)     # returns GuardedOpenAI
guard.get_status()            # returns StatusResponse
guard.teardown()
```

### Backends

```python
from tokencap.backends.sqlite import SQLiteBackend
SQLiteBackend(path="tokencap.db")  # default path

from tokencap.backends.redis import RedisBackend
RedisBackend(url="redis://localhost:6379")
```

### Exceptions

```python
tokencap.BudgetExceededError    # e.check_result.violated: list[str]
                           # e.check_result.states: dict[str, BudgetState]
tokencap.BackendError      # unrecoverable storage failure
```

### StatusResponse fields

```python
status = tokencap.get_status()
status.timestamp             # str, ISO 8601 UTC
status.dimensions            # dict[str, BudgetState]
status.active_policy         # str, policy name
status.next_threshold        # ThresholdInfo | None

state = status.dimensions["session"]
state.limit                  # int, tokens
state.used                   # int, tokens
state.remaining              # int, tokens
state.pct_used               # float, e.g. 0.624
state.cost_usd               # float, display only
```

### A note on types

`tokencap.wrap()` returns a `GuardedAnthropic` or `GuardedOpenAI` object, not the
original client type. Static type checkers will see the wrapper type. If your codebase
has type annotations expecting `anthropic.Anthropic` directly, you will see type
errors. Stub files (`.pyi`) are planned for v0.2. For now, annotate the wrapped
client as the wrapper type or use `# type: ignore` on the wrap call.

---

## License

Apache 2.0
