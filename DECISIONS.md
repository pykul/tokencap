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
library. All provider SDKs, Redis, tiktoken, and OTEL are optional extras.

**Why:** Mandatory dependencies are adoption friction. A developer who already has
`anthropic` installed should not be forced to also install `redis-py` and `tiktoken`
just because other users need them. Optional extras (`tokencap[anthropic]`,
`tokencap[redis]`, `tokencap[all]`) let each developer install exactly what they need.
The core library must import cleanly with zero extras.

---

## D-010: Reconciliation increment never triggers BLOCK

**Decision:** The post-call delta reconciliation (adding the difference between
estimated and actual tokens) is a forced increment. It does not check limits and
cannot trigger a BLOCK action. This is implemented as `force_increment()` on the
Backend Protocol. See D-013 for why it is a separate method rather than a flag.

**Why:** By the time reconciliation runs, the API call has already completed and the
response is in hand. Raising BudgetExceeded at that point would confuse the caller:
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

## D-012: Python 3.10+ minimum

**Decision:** Minimum supported Python version is 3.10.

**Why:** 3.10 introduced `match` statements (not used but available), improved type
union syntax (`X | Y` instead of `Union[X, Y]`), and structural pattern matching.
More practically: 3.10 is the oldest Python version still receiving security updates
as of 2026. Supporting 3.9 or earlier would require avoiding several clean type
annotation patterns.

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

**Implementation note:** `InterceptorBase` has two call paths: `call()` for sync
and `call_async()` for async. The wrapper detects which path to use based on
the client type passed to `Guard.__init__`. This detection happens once at
construction time, not on every call.
