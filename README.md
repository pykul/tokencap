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
soft warnings, model degradation, or webhook alerts, whichever fits your use case.

---

## Quickstart

Two lines. Your existing code does not change.

```python
import tokencap
import anthropic

client = tokencap.wrap(anthropic.Anthropic())
# [tokencap] session started: session=a3f1c2d4 backend=sqlite:tokencap.db (no limit set)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Summarize this document."}],
)
```

`wrap()` prints a startup message to stdout so there are no surprises. By default,
tokencap tracks token usage with no enforcement.

---

## Add a limit

One argument. No other changes.

```python
client = tokencap.wrap(anthropic.Anthropic(), limit=50_000)
# [tokencap] session started: session=a3f1c2d4 backend=sqlite:tokencap.db limit=50000 tokens
```

When the session hits 50,000 tokens, `BudgetExceededError` is raised before the
next call is made:

```python
try:
    response = client.messages.create(...)
except tokencap.BudgetExceededError as e:
    for dim in e.check_result.violated:
        state = e.check_result.states[dim]
        print(f"{dim} exceeded: {state.used:,} / {state.limit:,} tokens")
# session exceeded: 50,312 / 50,000 tokens
```

---

## Full policy

For warnings, model degradation, and webhooks before the hard stop, pass a policy:

```python
import tokencap
import anthropic

def on_warn(status):
    print(f"Warning: {status.dimensions['session'].pct_used:.0%} used")

client = tokencap.wrap(
    anthropic.Anthropic(),
    policy=tokencap.Policy(
        dimensions={
            "session": tokencap.DimensionPolicy(
                limit=50_000,
                thresholds=[
                    tokencap.Threshold(
                        at_pct=0.8,
                        actions=[tokencap.Action(kind="WARN", callback=on_warn)],
                    ),
                    tokencap.Threshold(
                        at_pct=0.9,
                        actions=[tokencap.Action(kind="DEGRADE", degrade_to="claude-haiku-4-5")],
                    ),
                    tokencap.Threshold(
                        at_pct=1.0,
                        actions=[tokencap.Action(kind="BLOCK")],
                    ),
                ],
            ),
        }
    ),
)
# [tokencap] session started: session=a3f1c2d4 backend=sqlite:tokencap.db limit=50000 tokens
```

The agent makes many calls. Tokens accumulate. When 80% is crossed, the WARN
callback fires once:

```
Warning: 82% used
```

After 90%, subsequent calls automatically use `claude-haiku-4-5` instead of the
requested model. The calling code never changes.

When the session reaches 100%, the next call raises `BudgetExceededError`:

```python
try:
    response = client.messages.create(...)
except tokencap.BudgetExceededError as e:
    for dim in e.check_result.violated:
        state = e.check_result.states[dim]
        print(f"{dim} exceeded: {state.used:,} / {state.limit:,} tokens")
# session exceeded: 51,200 / 50,000 tokens
```

Check the final state:

```python
status = tokencap.get_status()
for dim, state in status.dimensions.items():
    print(f"{dim}: {state.used:,} / {state.limit:,} tokens ({state.pct_used:.1%})")
# session: 51,200 / 50,000 tokens (102.4%)

tokencap.teardown()
```

`limit` and `policy` are mutually exclusive. Passing both raises `ConfigurationError`.

---

## Policy actions

### WARN: fire a callback and continue

Fires once when the threshold is crossed. The call proceeds normally.

```python
tokencap.Threshold(
    at_pct=0.8,
    actions=[tokencap.Action(kind="WARN", callback=on_warn)],
)
```

### DEGRADE: swap to a cheaper model transparently

From this threshold onward, all calls use the degraded model. The calling code
never changes.

```python
tokencap.Threshold(
    at_pct=0.9,
    actions=[tokencap.Action(kind="DEGRADE", degrade_to="claude-haiku-4-5")],
)
```

### BLOCK: raise an exception before the call

Fires on every call after the threshold is crossed, not just the first.

```python
tokencap.Threshold(
    at_pct=1.0,
    actions=[tokencap.Action(kind="BLOCK")],
)
```

### WEBHOOK: fire an HTTP POST and continue

Fire-and-forget in a background thread. Does not add latency to the call path.

```python
tokencap.Threshold(
    at_pct=0.8,
    actions=[tokencap.Action(kind="WEBHOOK", webhook_url="https://your-app.com/alerts")],
)
```

---

## Checking status

```python
status = tokencap.get_status()

for dim, state in status.dimensions.items():
    print(f"{dim}: {state.used:,} / {state.limit:,} tokens ({state.pct_used:.1%})")

# session: 31,200 / 50,000 tokens (62.4%)
```

---

## Why tokencap is easy to use

Most budget tools track dollars. The problem is that dollar cost changes every
time a provider reprices a model, and different call types (cached tokens, batch
API, streaming) cost different amounts. You end up with thresholds that silently
mean something different after a pricing update.

tokencap uses token counts directly. You set a limit of 50,000 tokens. That limit
means exactly the same thing regardless of which model you use, how the provider
prices it, or whether tokens are cached.

Dollar cost tracking is deliberately absent. Provider pricing changes without
notice and no machine-readable pricing API exists. A dollar figure derived from
a stale table is worse than no figure at all. Token counts are always accurate
— they come directly from the provider response.

If you know your task takes roughly 5,000 tokens per call and you want to cap at
10 calls, you set a limit of 50,000. No conversion needed.

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

---

## The wrapped client

`tokencap.wrap()` returns a proxy client. The common call paths work unchanged.
Here is exactly what is intercepted and what passes through.

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

```python
client = tokencap.wrap(anthropic.Anthropic())

# Tracked and enforced
response = client.messages.create(model="claude-sonnet-4-6", ...)

with client.messages.stream(model="claude-sonnet-4-6", ...) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

# Passes through untracked
models = client.models.list()
api_key = client.api_key

# Async works the same way
async_client = tokencap.wrap(anthropic.AsyncAnthropic())
response = await async_client.messages.create(model="claude-sonnet-4-6", ...)
```

**`isinstance` returns False.**
`isinstance(wrapped_client, anthropic.Anthropic)` is `False`. This is a known
limitation of the proxy pattern. Stub files (`.pyi`) are planned for v0.2.

For OpenAI the same rules apply: `chat.completions.create()` is intercepted,
everything else passes through.

---

## Advanced usage

### Multi-agent shared budgets

Multiple agents on the same machine can share a budget by pointing at the same
SQLite file:

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

client_a = agent_a.wrap_anthropic(anthropic.Anthropic())
client_b = agent_b.wrap_openai(openai.OpenAI())
```

Across machines, switch to Redis. The API is identical:

```python
from tokencap.backends.redis import RedisBackend

shared = RedisBackend("redis://redis-host:6379")
```

```bash
pip install redis
```

### Pre-configuring with init()

If you need to set custom identifiers or a non-default backend before wrapping:

```python
tokencap.init(
    policy=tokencap.Policy(...),
    identifiers={"session": "my-run-id-123"},
    backend=RedisBackend("redis://localhost:6379"),
)

client = tokencap.wrap(anthropic.Anthropic())
```

---

## Development

### Running tests

```bash
pip install -e ".[dev]"
pip install redis opentelemetry-api
make test          # unit + integration, no external services needed
make redis-up      # start local Redis container
make test-live     # live tests (mock providers, real Redis)
make redis-down    # stop Redis container
```

### Lint

```bash
make lint          # ruff + mypy --strict
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## OTEL integration

tokencap emits OpenTelemetry metrics after every call if `opentelemetry-api` is
installed. No configuration required.

```bash
pip install opentelemetry-api
```

| Metric | Type | Labels |
|---|---|---|
| `tokencap.tokens.used` | Counter | provider, model, dimension |
| `tokencap.tokens.remaining` | Gauge | dimension, identifier |
| `tokencap.budget.pct_used` | Gauge | dimension, identifier |
| `tokencap.policy.action_fired` | Counter | action_kind, dimension |

If `opentelemetry-api` is not installed, all telemetry is a no-op.

---

## Supported providers

| Provider | Install | Token estimation |
|---|---|---|
| Anthropic | `pip install tokencap[anthropic]` | Anthropic SDK counter |
| OpenAI | `pip install tokencap[openai]` | tiktoken |

Estimation runs before the call. Actual usage is reconciled after. The delta is
debited automatically. You never pay twice.

tokencap works with any model string passed to the provider SDK. Token estimation
uses the provider SDK counter where available and falls back to character estimation
for unknown models. No configuration is needed to use new or custom model names.

---

## What the defaults are

tokencap never does anything silently. When you call `wrap()`, these defaults apply:

| Setting | Default value |
|---|---|
| Dimension name | `"session"` |
| Session identifier | auto-generated UUID (printed when `wrap()` is called) |
| Backend | SQLite file `tokencap.db` in the current directory |
| Enforcement | none (tracking only) unless `limit=` or `policy=` is passed |

Pass `quiet=True` to `wrap()` to suppress the startup message.

---

## API reference

```python
tokencap.wrap(client, limit=None, policy=None, quiet=False)
```
Wraps an Anthropic or OpenAI client (sync or async). `limit` is a token count
shorthand for BLOCK at 100%. `policy` accepts a full `Policy` object. `limit`
and `policy` are mutually exclusive.

```python
tokencap.get_status()  # returns StatusResponse
tokencap.teardown()    # closes backend connections, resets global Guard
```

```python
tokencap.init(policy, identifiers=None, backend=None, otel_enabled=True, quiet=False)
```
Optional. Pre-configures the global Guard before `wrap()` is called.

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
```

### Exceptions

```python
tokencap.BudgetExceededError    # e.check_result.violated: list[str]
                                # e.check_result.states: dict[str, BudgetState]
tokencap.BackendError           # unrecoverable storage failure
```

---

## Installation

```bash
pip install tokencap
```

Requires Python 3.9+.

For provider-specific installs: `pip install tokencap[anthropic]`, `pip install tokencap[openai]`, or `pip install tokencap[all]`.

For distributed mode: `pip install redis`

For OTEL: `pip install opentelemetry-api`

---

## License

Apache 2.0
