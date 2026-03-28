# tokencap: Decisions

> Every significant decision is recorded here, including ones that were reversed.
> This file answers the question every future contributor asks: "why is it done this way?"
> Record new decisions as they are made during the build. Do not clean this file up.

---

## D-001: Token-denominated internally, dollars are a display layer

**Decision:** All enforcement logic operates on token counts. Dollar cost is computed
on-demand for display in StatusResponse and OTEL metrics. It is never stored and
never used for enforcement decisions.

**Why:** Model pricing changes frequently (Anthropic and OpenAI have both changed
pricing mid-year). Cached tokens have different prices than regular tokens. Batch API
has different rates. If enforcement were dollar-based, a pricing change would silently
alter enforcement behavior without any code change. Tokens are the stable unit. They
are what the API actually counts and returns. Dollar display is a convenience, not the
source of truth.

**Rejected alternative:** Dollar-denominated tracking. Ruled out because pricing
volatility makes dollar thresholds semantically unstable.

---

## D-002: Two modes: zero-infra SQLite default, Redis as one-line upgrade

**Decision:** SQLiteBackend is the default. No Redis required out of the box.
Switching to Redis requires changing one constructor argument. The public API is
identical in both modes.

**Why:** Developer adoption is killed by infrastructure requirements at install time.
A developer who runs `pip install tokencap` and then discovers they need Redis running
will bounce. Most use cases (solo developers, small teams, single-machine deployments)
don't need distributed state. The SQLite mode is correct and complete for those cases.
The Redis upgrade path exists for teams who need cross-process enforcement, and the
one-line switch means they never rewrite application code to get there.

**Rejected alternative:** Redis-only (too much friction). SQLite-only (wrong for
multi-process / multi-tenant production). Custom in-memory shared state (doesn't
survive process restarts or cross-process scenarios).

---

## D-003: Anthropic and OpenAI only in v0

**Decision:** v0 ships with AnthropicProvider and OpenAIProvider. All other providers
(Gemini, Mistral, Cohere, etc.) are v0.2+.

**Why:** Provider support requires maintaining a pricing table, testing token estimation
accuracy, and verifying response parsing. Doing this for 5+ providers in v0 delays
shipping without proportionate value. Anthropic and OpenAI cover the vast majority
of production agent deployments. The Provider Protocol is designed for extension,
v0.2 additions are simple to add.

---

## D-004: Client wrapping, not monkey-patching

**Decision:** tokencap wraps the client object. The developer passes their existing
client to `guard.wrap_anthropic(client)` or uses `tokencap.wrap(client)`. We do not
monkey-patch the SDK modules globally.

**Why:** Monkey-patching (modifying `anthropic.Anthropic.messages.create` globally) is
invisible, hard to debug, and breaks when SDK internals change. Client wrapping is
explicit. The developer chooses which clients are guarded. It works correctly in
test environments where patching global state causes test pollution. It also allows
multiple Guard instances with different policies on different clients in the same process.

Some other tools in this space use monkey-patching to achieve a one-line API. We
deliberately chose wrapping instead because the debugging cost of invisible global
state is higher than the minor ergonomic difference.

**Rejected alternative:** Monkey-patching (global SDK modification). Ruled out for
the reasons above.

---

## D-005: DEGRADE model map is caller-supplied, not opinionated

**Decision:** The DEGRADE action takes a `degrade_to` model string. tokencap does not
ship a default model downgrade hierarchy.

**Why:** Model quality opinions are not ours to make. "Degrade from claude-opus to
claude-haiku" is not universally correct. Some applications require a specific model
capability and should BLOCK rather than degrade. Others have contractual or compliance
reasons to use specific models. Providing opinionated defaults would cause silent
behavior changes when model families update. The developer explicitly declares the
downgrade target, making the policy self-documenting.

---

## D-006: No HTTP status endpoint in v0

**Decision:** `get_status()` is a Python function call. No HTTP server, no sidecar,
no port in v0. HTTP status endpoint is v0.2.

**Why:** An HTTP endpoint requires either a background thread (complicates shutdown)
or a sidecar process (complicates deployment). The 80% use case is developers checking
status programmatically from inside their agent code, not from an external tool.
The Python API covers that completely. An HTTP endpoint adds complexity without
covering a gap that exists in v0.

---

## D-007: Threshold fire-once per period rule

**Decision:** A threshold fires exactly once per budget period per key. Once fired,
it is recorded in the backend and does not re-fire until the period resets.

**Why:** Without this rule, every LLM call after crossing the 80% threshold fires
a WARN callback and a webhook POST. In a 100-call agent run where the threshold is
crossed at call 60, calls 61-100 would each fire the alert. This creates alert storms
and burns webhook quotas. The threshold is intended to signal a crossing event, not
a persistent state.

---

## D-008: WEBHOOK is fire-and-forget in a background thread

**Decision:** Webhook HTTP posts execute in a background daemon thread. They do not
block the call path. Failures are logged at WARNING level and swallowed.

**Why:** The LLM call path latency is the developer's critical path. A webhook
destination may be slow, unavailable, or timing out. If webhook delivery blocked
the call, a misconfigured or unavailable webhook URL would add seconds of latency
to every LLM call after a threshold crossing. That is unacceptable. Webhooks are
best-effort notification, not guaranteed delivery.

---

## D-009: Zero required dependencies

**Decision:** `pip install tokencap` installs nothing beyond the Python standard
library. Provider SDKs are optional extras. Redis and OTEL are installed directly
by the developer if needed, not as tokencap extras.

**Why:** Mandatory dependencies are adoption friction. A developer who already has
`anthropic` installed should not be forced to install other packages. Optional
extras (`tokencap[anthropic]`, `tokencap[openai]`, `tokencap[all]`) cover provider
SDKs only. Redis and OTEL are handled separately. See D-033 for the full rationale.
The core library must import cleanly with zero extras.

---

## D-010: Reconciliation increment never triggers BLOCK

**Decision:** The post-call delta reconciliation (adding the difference between
estimated and actual tokens) is a forced increment. It does not check limits and
cannot trigger a BLOCK action. This is implemented as `force_increment()` on the
Backend Protocol. See D-013 for why it is a separate method rather than a flag.

**Why:** By the time reconciliation runs, the API call has already completed and the
response is in hand. Raising BudgetExceededError at that point would confuse the caller:
they have a valid response but an exception would prevent them from using it.
Reconciliation is a bookkeeping operation to keep the ledger accurate. Enforcement
happens in pre-call only.

---

## D-011: Package name is tokencap, not tokenguard

**Decision:** The package is named `tokencap`. `tokenguard` was the first choice but
is taken on PyPI (v0.4.1, unrelated Claude Code safety tool). `tokencap` was available
and is the better name anyway. "cap" is a mechanical enforcement metaphor (you cap
usage), whereas "guard" is more passive. `tokenmesh` and `tokenmon` were considered
and rejected: `tokenmesh` is too abstract for search discovery, `tokenmon` implies
monitoring/observation rather than enforcement.

---

## D-012: Python 3.9+ minimum

**Decision:** Minimum supported Python version is 3.9.

**Why:** 3.9 is the oldest Python version with broad ecosystem support for the
libraries tokencap's users are likely to have installed (anthropic, openai).
The type annotation concern that previously made 3.9 problematic is resolved by
`from __future__ import annotations` at the top of every module, which makes
`X | Y`, `list[X]`, and `dict[X, Y]` syntax valid as string annotations on 3.9+.
No runtime feature from 3.10+ is used in the codebase.

---

## D-013: force_increment is a separate Backend method, not a flag on check_and_increment

**Decision:** The Backend Protocol has two distinct increment methods:
`check_and_increment` for enforcement decisions (may reject), and `force_increment`
for post-call reconciliation (always succeeds).

**Why:** Using `check_and_increment` for reconciliation would require either a
`skip_check=True` flag (leaky abstraction) or accepting that reconciliation can
silently fail when the limit is exceeded. Neither is correct. A completed API call
cannot be undone. The provider already charged for those tokens. The reconciliation
increment must always succeed to keep the ledger accurate. Separating the methods
makes the intent unambiguous and eliminates the flag anti-pattern.

---

## D-014: Action.kind not Action.type

**Decision:** The action type field is named `kind`, not `type`.

**Why:** `type` is a Python builtin. Using it as a dataclass field name shadows the
builtin, creates confusion for anyone who calls `type(action)`, and causes subtle
issues with serialization libraries that introspect field names. `kind` is idiomatic
Python for discriminated union fields (see also: `ast.AST` nodes, `typing` internals).

---

## D-015: Dimension identifiers are supplied at Guard construction, not inferred

**Decision:** `Guard.__init__` accepts an `identifiers: dict[str, str] | None`
parameter. Dimensions not listed receive an auto-generated UUID.

**Why:** The `BudgetKey` has both a `dimension` and an `identifier`. The dimension
names the type of limit ("session", "tenant_daily"). The identifier names the specific
counter to increment ("session_abc123", "acme:2026-03-27"). Without explicit identifiers,
two `Guard` instances targeting the same Redis backend would share counters only if
they generated the same identifier, which is impossible with random UUIDs. Explicit identifiers
also make multi-process enforcement predictable: two processes with the same identifiers
share the same counters, which is the entire point of distributed mode.

---

## D-016: Threshold.at_pct validated in __post_init__, not at call time

**Decision:** `Threshold.__post_init__` raises `ValueError` if `at_pct` is not in
`(0.0, 1.0]`.

**Why:** Invalid threshold values should fail at construction time, not silently
produce wrong behaviour at runtime when a threshold is never or always triggered.
`at_pct=0.0` would fire on every call. `at_pct=1.5` would never fire. Both indicate
a misconfiguration that should be caught immediately.

---

## D-017: DimensionPolicy.reset_every is Literal["day", "hour"] | None

**Decision:** `reset_every` uses `Literal` instead of bare `str`.

**Why:** A bare `str` allows any value including typos like `"daily"` or `"days"`.
`Literal["day", "hour"]` is checked by mypy at the call site. The valid values are
intentionally minimal in v0. Additional periods (week, month) can be added to the
`Literal` in a backward-compatible way.

---

## D-018: py.typed marker included from day one

**Decision:** `tokencap/py.typed` is an empty file committed to the repository
and included in the package distribution.

**Why:** Without this PEP 561 marker, mypy treats the package as untyped and ignores
all type annotations. Downstream users who run mypy on their own code would not
benefit from tokencap's type information. Since we require `mypy --strict` to pass
on the entire codebase, not shipping the marker would be inconsistent.

---

## D-019: No thresholds configured means usage is tracked, not enforced

**Decision:** A `DimensionPolicy` with no thresholds tracks token usage silently.
It does not block, warn, or take any action when the limit is reached. Enforcement
requires at least one explicitly configured threshold.

**Why:** Implicit behaviour is always the wrong default for a policy system.
If tokencap silently blocked calls when no thresholds were defined, a developer
who adds a dimension for visibility purposes only would suddenly find calls being
rejected. The policy is self-documenting: what you see in the thresholds list is
exactly what fires. Nothing else happens. The README quickstart example includes
an explicit BLOCK threshold to make this concrete and prevent the "I configured
a limit but nothing happened" confusion.

**Implication for code:** Any example that promises enforcement must show how
enforcement is configured, either via the `limit=` shorthand or an explicit threshold.
The startup message ("no limit set") handles the visibility concern for the zero-config
case, but examples showing BLOCK behavior must make the BLOCK configuration visible.

---

## D-020: No helper utilities for identifier construction

**Decision:** tokencap does not ship helpers like `DailyIdentifier(tenant_id="acme")`
or any other utility that generates identifier strings. Developers construct
identifier strings themselves and pass them to Guard.

**Why:** The developer knows their tenant model, their date/timezone handling,
and their naming conventions better than tokencap does. A helper would make
opinionated choices (UTC vs local time, date format, separator character) that
would be wrong for some users. The pattern is simple enough that a helper is
scope creep. One line of Python constructs any identifier you need:

    f"acme:{datetime.now(UTC).strftime('%Y-%m-%d')}"

Adding a helper would create a maintenance surface with no proportionate value.

---

## D-021: Three-tier API with progressive defaults

**Decision:** tokencap has three usage tiers:
1. `wrap(client)`: zero config, tracking only
2. `wrap(client, limit=N)`: one argument, hard block at limit
3. `init(policy=...) + wrap(client)`: full control

Each tier is a superset of the previous. Defaults are always printed to stdout
on first call so the developer knows exactly what tokencap configured on their
behalf. Defaults can be suppressed with `quiet=True`.

**Why:** A single-argument API gets adoption. The full policy API is necessary for
production use cases, but it should not be the entry point. Most developers want to
see something working in two lines before they invest in learning the full
configuration surface. The startup message makes the defaults transparent rather than
magical, which matches the library's philosophy of never doing anything silently.

**Rejected alternative:** Requiring `init()` before `wrap()`. This was the
original design. It fails the "two lines" test and forces every developer through
the full policy API even when they just want basic tracking.

---

## D-022: Default dimension name is "session"

**Decision:** When `wrap()` is called without `init()`, the default dimension
name is `"session"`. Not `"run"`, not `"default"`, not `"global"`.

**Why:** "session" maps to how developers think about a single execution of their
agent. It is visible in `get_status()` output and in OTEL labels, so the name
needs to be self-explanatory to someone who did not write the configuration.
"run" was the original name but is too generic. "session" communicates scope
(one agent execution) without implying persistence across calls.

---

## D-023: Startup message prints defaults to stdout

**Decision:** When a Guard is created (either explicitly or implicitly via `wrap()`),
tokencap prints one line to stdout describing what was configured. Format:

    [tokencap] session started: session=<id> backend=<backend> (no limit set)
    [tokencap] session started: session=<id> backend=<backend> limit=<N> tokens

This can be suppressed with `quiet=True` on `wrap()` or `init()`.

**Why:** Defaults that are invisible are worse than no defaults. A developer
who calls `wrap(client, limit=50_000)` needs to know that tokencap saw their
limit and is enforcing it. A developer who calls `wrap(client)` needs to know
that tokencap is tracking but not enforcing. Printing one line on startup costs
nothing and prevents the two most common support questions: "why is it not
blocking?" and "what session ID am I using?"

The message goes to stdout, not a logger, so it works with zero logging
configuration. It prints once per Guard instance, not per call.

---

## D-024: wrap() supports both sync and async clients transparently

**Decision:** `tokencap.wrap()` accepts both `anthropic.Anthropic` (sync) and
`anthropic.AsyncAnthropic` (async), and both `openai.OpenAI` (sync) and
`openai.AsyncOpenAI` (async). It detects the client type at runtime and returns
the appropriate wrapper. The developer always calls the same `wrap()` function.

**Why:** Requiring separate `wrap_sync()` and `wrap_async()` functions would
force the developer to know which client type they are passing and call the right
function. This is friction with no benefit. The client type is detectable via
`isinstance` at runtime. A single `wrap()` function that handles both keeps the
API simple and consistent with the "two lines" philosophy.

**Implementation note:** `interceptor/base.py` has two call paths: `call()` for sync
and `call_async()` for async. The wrapper detects which path to use based on
the client type passed to `Guard.__init__`. This detection happens once at
construction time, not on every call.

---

## D-025: with_options() uses *args/**kwargs passthrough, returns GuardedAnthropic

**Decision:** `with_options()` is implemented as an explicit method on `GuardedAnthropic`
using `*args, **kwargs` passthrough. It calls the underlying client's `with_options()`
and wraps the result in a new `GuardedAnthropic` bound to the same `Guard`.

```python
def with_options(self, *args: Any, **kwargs: Any) -> "GuardedAnthropic":
    new_client = self._client.with_options(*args, **kwargs)
    return GuardedAnthropic(new_client, self._guard)
```

**Why `*args, **kwargs` and not a typed signature:** Copying the SDK's parameter
list would create a maintenance burden. Every time the Anthropic SDK changes
`with_options()`, our copy would drift silently. `*args, **kwargs` passes everything
through without owning the signature. If the SDK renames a parameter, the developer
gets the SDK's own error, not a tokencap-specific one.

**Why not `__getattr__` delegation:** `__getattr__` on `with_options` would return
the SDK's real method, which returns a plain `anthropic.Anthropic`. Any calls made
through that object would bypass tokencap silently. This is worse than a visible
error. The explicit method ensures the result is always wrapped.

---

## D-026: tokencap intercepts messages.create() and messages.stream() only in v0

**Decision:** In v0, tokencap intercepts `messages.create()` and `messages.stream()`
on Anthropic, and `chat.completions.create()` on OpenAI. All other endpoints
(batch, beta, models, files, etc.) pass through untracked.

**Why:** The vast majority of agent token usage flows through these endpoints.
Intercepting every possible endpoint would require maintaining wrappers for every
SDK method and every SDK update. That is disproportionate maintenance for v0.
The intercepted surface is documented honestly in the README so developers know
what is and is not tracked. Expanding to batch and beta endpoints is v0.2.

---

## D-027: All client-returning SDK methods must be explicitly wrapped

**Decision:** Any method on the Anthropic or OpenAI SDK that returns a new client
instance must be implemented as an explicit method on the guarded wrapper using
`*args, **kwargs` passthrough. It must return a new guarded wrapper bound to the
same `Guard`. It must never be left to `__getattr__` delegation.

Known methods in v0:

**Anthropic:**
- `with_options(*args, **kwargs)` -> `GuardedAnthropic`
- `with_raw_response(*args, **kwargs)` -> `GuardedAnthropic`
- `with_streaming_response(*args, **kwargs)` -> `GuardedAnthropic`

**OpenAI:**
- `with_options(*args, **kwargs)` -> `GuardedOpenAI`
- `with_raw_response(*args, **kwargs)` -> `GuardedOpenAI`
- `with_streaming_response(*args, **kwargs)` -> `GuardedOpenAI`

**Why:** Any client-returning method left to `__getattr__` returns a plain SDK
client. Every call made through that object bypasses tokencap silently. This is
the same problem as the original `with_options()` gap. The fix is the same in
all cases: an explicit method with `*args, **kwargs` passthrough.

**Standing instruction for Phase 2:** Before closing Phase 2, Claude Code must
scan the installed Anthropic and OpenAI SDK source for any method that returns
a client instance and is not yet covered. Any new method found must either be
wrapped or documented as a known gap with a justification.

---

## D-028: InterceptorBase is module-level functions, not a class

**Decision:** `interceptor/base.py` contains `call()`, `call_async()`,
`call_stream()`, and `_evaluate_thresholds()` as module-level functions.
There is no `InterceptorBase` class to instantiate.

**Why:** There is no instance state in the interceptor. All state lives in
`Guard`. Forcing a class instantiation would either require passing `Guard` to
`__init__` (coupling) or passing it to every method call (verbose). Functions
with explicit `guard` arguments are cleaner, easier to test, and make the data
flow obvious.

---

## D-029: Early stream exit falls back to pre-call estimate

**Decision:** If a developer exits a stream before the final chunk arrives
(via `break`, exception, or early return), tokencap uses the pre-call token
estimate as the reconciled count. A WARNING is logged. The ledger is not
corrected to the actual usage because the actual usage is unknown.

**Why:** The pre-call estimate was already debited. Without the final chunk,
actual usage cannot be determined. The options are: leave the ledger understated
(accept the estimate as final), raise an error on exit (breaks developer code),
or force a status call to the provider (not available). Accepting the estimate
as final is the least surprising behavior. The warning tells the developer what
happened.

---

## D-030: OpenAI streaming injects stream_options automatically

**Decision:** When `stream=True` is detected in kwargs for an OpenAI call,
`GuardedCompletions.create()` injects `stream_options={"include_usage": True}`
into a copy of kwargs using `setdefault`. This happens before the call.

**Why:** OpenAI does not return token usage in streaming by default. Without
this injection, `extract_usage()` returns zero tokens for every streaming call,
reconciliation never fires, and the ledger permanently understates usage. The
developer should not have to know about this OpenAI quirk. `setdefault` ensures
we do not override a developer-supplied `stream_options`.

---

## D-031: Backend stores threshold fired state

**Decision:** The Backend Protocol has two new methods: `is_threshold_fired()`
and `mark_threshold_fired()`. These store and query whether a given threshold
has fired for a given key. `reset()` also clears fired threshold records.

**Why:** The fire-once rule requires persistent state. If fired state were stored
in memory on the Guard instance, it would not survive across Guard instances in
distributed mode. Two agents targeting the same budget key would each fire the
80% WARN alert separately. Storing in the backend means the fire-once rule is
enforced across all agents sharing the same key.

SQLite stores these in a `fired_thresholds` table. Redis stores them as string
keys with a naming convention. Both are cleared on `reset()`.

---

## D-032: wrapt is not used

**Decision:** tokencap does not use the `wrapt` library for its proxy classes.
The proxy pattern is implemented with `@property` for resource interception and
`__getattr__` for pass-through delegation.

**Why:** `wrapt.ObjectProxy` solves problems with dunder methods that Python looks
up on the type rather than the instance (`__repr__`, `__len__`, `__iter__`, etc.).
These never trigger `__getattr__` on a hand-rolled proxy.

For the Anthropic and OpenAI SDK clients, none of these dunders are relevant.
The clients are not containers, not iterable, not comparable. The only one that
matters is `__repr__` for debugging, which is a minor inconvenience, not a
correctness problem. Adding `wrapt` as a dependency for the marginal benefit of
a better `repr()` is not justified. If a real edge case surfaces post-launch,
adding it in v0.2 is a one-line change.

---

## D-033: redis and opentelemetry-api are not tokencap extras

**Decision:** `tokencap[redis]` and `tokencap[otel]` extras are not published
or documented as the primary install path. The README directs users to install
`redis` and `opentelemetry-api` directly. The extras table in pyproject.toml
covers only `anthropic`, `openai`, and `all`.

**Why:** Redis and OTEL are independent libraries with their own release cycles.
Wrapping them as tokencap extras implies tokencap controls their versions, which
it does not. Both are already handled gracefully by tokencap without being listed
as extras: Redis raises a clear `ImportError` with the install command if
`RedisBackend` is used without it. OTEL no-ops silently if not installed. The
developer who wants either already knows how to install a Python package. Listing
them as extras adds friction without providing any additional value.

---

## D-034: Exception class named BudgetExceededError not BudgetExceeded

**Decision:** The public exception for a blocked call is named
`BudgetExceededError`, following Python stdlib naming convention
(`ValueError`, `KeyError`, `TimeoutError`). An earlier draft used
`BudgetExceeded` but that reads as a status flag rather than an
exception. The N818 ruff rule enforces this convention and is
left fully enabled.

---

## D-035: SQLiteBackend uses threading.Lock in addition to BEGIN IMMEDIATE

**Decision:** All mutating methods on `SQLiteBackend` acquire a
`threading.Lock` before issuing any SQL.

**Why:** `BEGIN IMMEDIATE` serialises writes at the SQLite database-file level,
which handles cross-process concurrency. However, Python's `sqlite3.Connection`
is not thread-safe: two threads issuing statements on the same connection
concurrently can corrupt the connection's internal state. The lock serialises
access to the connection object within a single process. Both mechanisms are
required: the lock for in-process thread safety, `BEGIN IMMEDIATE` for
cross-process write serialisation.

---

## D-036: CI uses a single job, not a matrix of jobs

**Decision:** The GitHub Actions CI workflow installs all five Python
versions (3.9–3.13) in one job and loops through each to run
`make test`. Lint runs once. The PR shows one green check, not five.

**Why:** A matrix strategy creates a separate job per Python version,
each producing its own check on the PR. Five checks add visual noise
without proportionate value — if any version fails the single job fails,
which is the same signal. A single job also starts faster because it
avoids the overhead of provisioning five runners. The Makefile accepts
`PYTHON=pythonX.Y` so each loop iteration runs tests under the correct
interpreter.

---

## D-037: BLOCK is exempt from the fire-once rule

**Decision:** The fire-once rule (D-007) applies only to WARN and WEBHOOK
actions. BLOCK actions never call `is_threshold_fired` or
`mark_threshold_fired`. Every call that crosses a BLOCK threshold is
blocked, not just the first.

**Why:** WARN and WEBHOOK are notifications. Firing them on every call
after crossing the threshold creates alert storms and burns webhook
quotas. The fire-once rule prevents this by recording that the threshold
has been crossed and skipping subsequent notifications.

BLOCK is enforcement, not notification. Its purpose is to prevent the
LLM call from being made. If BLOCK only fired once, the first call
after crossing the threshold would be blocked but all subsequent calls
would proceed unchecked. That defeats the purpose of a budget limit.

Therefore `_evaluate_thresholds` checks `has_block` before consulting
the fired-threshold state. When a threshold contains a BLOCK action,
the function skips the fire-once check entirely, executes any WARN and
WEBHOOK actions on the same threshold (so the caller has observable
context before the exception), then raises `BudgetExceededError`.
DEGRADE is skipped when BLOCK is present on the same threshold because
there is no call to degrade — the call is not made.

---

## D-038: policy.py and guard.py stub created in Phase 2

**Decision:** `tokencap/core/policy.py` (Policy, DimensionPolicy, Threshold,
Action) and a minimal `tokencap/core/guard.py` stub were created in Phase 2
rather than waiting for Phase 3.

**Why:** The interceptor module (`interceptor/base.py`) imports `Guard` and
calls `guard.policy.dimensions`, `guard.backend.*`, and `guard.provider.*`.
Without at least the policy dataclasses and a Guard stub, the interceptor
code cannot be imported or tested. The policy dataclasses are pure data with
no evaluation logic — pulling them forward adds no Phase 3 risk. The Guard
stub implements only the attributes the interceptor needs (policy, backend,
provider, identifiers, telemetry, get_status). Full Guard logic (wrap(),
init(), startup message) remains Phase 3.

`tokencap/status/api.py` was also created as a minimal stub containing only
the `StatusResponse` dataclass. This satisfies the `TYPE_CHECKING` import in
`policy.py` for `Callable[[StatusResponse], None]`. The full StatusResponse
implementation with `ThresholdInfo` and `get_status()` logic is Phase 3.

---

## D-039: is_async passed from GuardedAnthropic to GuardedMessages as constructor parameter

**Decision:** `GuardedMessages.__init__` takes `is_async: bool` as a keyword
argument. `GuardedAnthropic` passes `self._is_async` when constructing
`GuardedMessages` in the `messages` property.

**Why:** `GuardedMessages` needs to know whether to route `create()` to
`call()` (sync) or `call_async()` (async). The async flag is determined at
`GuardedAnthropic` construction time via `isinstance(client, AsyncAnthropic)`.
`GuardedMessages` has no reference to `GuardedAnthropic`, so the flag must
be passed explicitly. The same pattern applies to `GuardedCompletions` and
`GuardedChat` on the OpenAI side.

---

## D-040: with_raw_response and with_streaming_response are properties, not methods

**Decision:** On both `GuardedAnthropic` and `GuardedOpenAI`,
`with_raw_response` and `with_streaming_response` are implemented as
`@property` returning new guarded wrappers, not as callable methods with
`*args, **kwargs`. `with_options` remains a callable method.

**Why:** The Anthropic and OpenAI SDKs (as of anthropic 0.86 and openai 2.30)
implement `with_raw_response` and `with_streaming_response` as
`cached_property` on the client, not as callable methods. Calling them as
functions raises `TypeError`. Only `with_options()` is a regular method.
The implementation matches the actual SDK behaviour. ARCHITECTURE.md has been
updated to reflect this.

Post-call token reconciliation works for both raw and streaming response
paths because both providers detect a callable `.parse()` method on the
response object and call it before extracting usage fields. The raw response
wrappers returned by both SDKs expose `.parse()` which returns the fully
parsed response with usage data.

**SDK scan results (Phase 2 closing requirement per D-027):**
- Anthropic: `with_options` (function), `with_raw_response` (cached_property),
  `with_streaming_response` (cached_property). All three wrapped.
- OpenAI: same three. All three wrapped.
- No additional `with_*` client-returning methods found on either SDK.

---

## D-041: Ruff SIM105 and SIM108 suppressed to match architecture spec

**Decision:** `SIM105` and `SIM108` are added to `[tool.ruff.lint] ignore`
in `pyproject.toml`.

**Why:** SIM105 wants `contextlib.suppress(Exception)` instead of
`try/except/pass`. SIM108 wants ternary operators instead of `if/else`
blocks. Both patterns appear in `interceptor/base.py` where the code
is a direct implementation of the code blocks in ARCHITECTURE.md.

The `try/except/pass` in WARN callback handling is intentional per the
architecture spec: "WARN callback failure never propagates." Rewriting
it as `contextlib.suppress` changes the visual pattern without changing
behaviour, but diverges from the documented spec code that reviewers
compare against.

The `if delta > 0` / `else` blocks in `call()` and `call_async()` are
similarly written to match the spec line-for-line. Ternary operators
would compress the logic into a single line, making it harder to
diff against the architecture document.

Both rules are suppressed globally rather than with per-line `noqa`
comments because the patterns recur in every call path and the
justification is the same in every case: keep implementation aligned
with the documented spec.

---

## D-042: Guard is a stateless config holder; provider lives on the wrapped client

**Decision:** Guard holds policy, identifiers, backend, and telemetry. It does
not hold `provider` or `current_model`. Provider is created per `wrap_*` call
and passed to the wrapped client (GuardedAnthropic / GuardedOpenAI), which
passes it through to GuardedMessages / GuardedCompletions and ultimately to
`call()`, `call_async()`, and `call_stream()` as an explicit argument.

**Why:** Provider and current_model are mutable call-time state. Storing them
on Guard means a single Guard wrapping both an Anthropic and an OpenAI client
would overwrite the provider on each `wrap_*` call, causing cross-contamination.
By storing provider on the wrapped client, each wrapped client owns its own
provider instance. `call()` receives provider explicitly from the caller,
making the data flow visible and preventing any shared mutable state on Guard.

---

## D-043: wrap() accepts policy directly; limit and policy are mutually exclusive

**Decision:** `wrap()` signature is `wrap(client, limit=None, policy=None,
quiet=False)`. `policy` accepts a full `Policy` object. `limit` and `policy`
are mutually exclusive — passing both raises `ConfigurationError`.

**Why:** The primary product story is two lines: import and wrap. The previous
design required `init()` + `wrap()` for full policy control, which is a
two-step ceremony that obscures the simplicity of the product. By accepting
`policy` directly on `wrap()`, the common case — a single client with a
custom policy — stays at two lines.

`init()` remains for advanced scenarios: pre-configuring identifiers, choosing
a non-default backend, or sharing state across multiple `wrap()` calls with
different SDK clients. It is not needed for the typical single-client case.

`limit` and `policy` are mutually exclusive because `limit` is syntactic sugar
for a specific policy (session dimension, BLOCK at 100%). Allowing both would
create ambiguity about which takes precedence.
