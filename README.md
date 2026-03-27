# tokencap

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

`tokencap.wrap()` returns a client that behaves identically to the original. Every
method, every attribute, every call pattern works unchanged. You use it exactly as
you would use the unwrapped client.

```python
client = tokencap.wrap(anthropic.Anthropic())

# Sync
response = client.messages.create(model="claude-sonnet-4-6", ...)

# Streaming
with client.messages.stream(model="claude-sonnet-4-6", ...) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

# Async
import asyncio
async_client = tokencap.wrap(anthropic.AsyncAnthropic())
response = await async_client.messages.create(model="claude-sonnet-4-6", ...)

# Any other attribute passes through
client.api_key        # same as anthropic.Anthropic().api_key
client.base_url       # same as anthropic.Anthropic().base_url
```

For OpenAI the pattern is identical:

```python
import openai
client = tokencap.wrap(openai.OpenAI())
response = client.chat.completions.create(model="gpt-4o", ...)
```

tokencap wraps the client object in-process. There is no proxy, no network change,
and no modification to the underlying SDK.

---

## Adding a limit

One argument.

```python
client = tokencap.wrap(anthropic.Anthropic(), limit=50_000)
```

When the session hits 50,000 tokens, `BudgetExceeded` is raised before the next call
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

Raises `tokencap.BudgetExceeded` with full state in the exception. The API call
is never made.

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

All dimensions are checked and incremented atomically. No partial updates.

```bash
pip install tokencap[redis]
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
pip install tokencap[otel]
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

| Provider | Package | Token estimation |
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

Minimal install, no required dependencies:

```bash
pip install tokencap
```

With provider support:

```bash
pip install tokencap[anthropic]   # Anthropic SDK + accurate token estimation
pip install tokencap[openai]      # OpenAI SDK + tiktoken
pip install tokencap[redis]       # Redis backend for distributed enforcement
pip install tokencap[otel]        # OpenTelemetry metrics and traces
pip install tokencap[all]         # Everything
```

Requires Python 3.10+.

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
AI monitoring tools give you dashboards, traces, and historical spend analysis. They are
excellent at telling you what happened. tokencap is complementary, not competing: it
enforces policy in your agent code before and during calls. Many teams use both, an
observability platform for the ops dashboard and tokencap for the enforcement layer in
their application code. The two work together naturally via tokencap's OTEL emission.

**No tool at all.** The most common situation. Most teams set a provider-level
spending cap and find out about runaway costs from the bill. tokencap is for teams
who want enforcement in the code, not reactive alerts after the money is spent.

---

## License

Apache 2.0
