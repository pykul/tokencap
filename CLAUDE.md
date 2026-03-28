# tokencap: Standing Rules for Claude Code

> Read this file at the start of every session, after ARCHITECTURE.md.
> These rules accumulate as the project matures. Every rule exists because
> something went wrong or almost went wrong without it.

---

## Before Writing Anything

1. Read ARCHITECTURE.md in full before producing any plan.
2. Read DECISIONS.md before making any design choice. The decision may already
   be made and recorded.
3. Produce a full plan listing every file you will create or modify and why.
   Show the plan. Wait for approval. Do not write code before approval.
4. After implementation, report every file touched and every decision made.

---

## Hard Rules: Never Violate These

**Never add a required dependency.** tokencap has zero required dependencies.
Every external import that is not in the Python standard library must be guarded
with a try/except ImportError. If it fails, emit a clear message telling the user
exactly what to install. Never let a ModuleNotFoundError propagate to user code.

**Never store prompt or response content.** tokencap tracks token counts and
metadata only. If you find yourself writing code that stores, logs, or inspects
the content of messages or completions, stop. That is not what this library does.

**Never make enforcement decisions based on dollars.** All enforcement logic
uses token counts. Dollar cost is computed for display only (StatusResponse,
OTEL metrics). Never check a dollar threshold. See D-001.

**Never monkey-patch SDK modules globally.** tokencap wraps client objects.
It does not modify `anthropic.Anthropic`, `openai.OpenAI`, or any other class
at the module level. See D-004.

**Never block the call path in WEBHOOK.** Webhook HTTP posts run in a background
daemon thread. They must not add latency to LLM calls. See D-008.

**Never mutate the caller's kwargs dict.** The DEGRADE action swaps the model
in a copy of kwargs. The original dict passed by the developer must be unchanged
after any interceptor operation.

**Never raise in telemetry code.** OTEL calls are wrapped in try/except. A
telemetry failure must never cause a LLM call to fail. Log at WARNING and continue.

**Never let the reconciliation increment trigger policy actions.** The post-call
delta increment is forced. It does not check limits or fire thresholds. See D-010.

---

## Code Quality Rules

**All source files under `tokencap/` must pass `mypy --strict` with no errors.**
Test files under `tests/` are excluded from `mypy --strict`. The `make lint`
target runs `mypy --strict tokencap/` only. If a type annotation requires an
ignore comment, fix the underlying issue. Do not add `# type: ignore` unless
you have exhausted all alternatives and document why in a comment.

**All files must pass ruff with the project config.** Run `make lint` before
reporting completion of any task. Fix all warnings.

**Always run `make lint` and `make test` immediately before every `git commit`.**
Never commit without a passing lint and test run in the same session. If either
fails, fix the issue before committing. This is not optional — CI will reject
the push and the commit must be amended or followed by a fix commit.

**Every public function and class must have a docstring.** Single-line is fine
for obvious cases. No docstring-free public API.

See Testing Rules section for the full test policy.

**The concurrent write test is the atomicity acceptance test.** For both
SQLiteBackend and RedisBackend: 10 threads, 100 increments of 1 token each,
against a single key with a limit of 2000. Final used_tokens must equal exactly
1000. If it does not, atomicity is broken. Do not mark Phase 1 or Phase 4 complete
until this test passes consistently.

---

## Architecture Rules

**The Backend Protocol is the only interface between Guard and storage.**
Guard never calls SQLiteBackend or RedisBackend methods directly, only the
protocol interface. If you need a new storage operation, add it to the protocol
first and update ARCHITECTURE.md before implementing it.

**The Provider Protocol is the only interface between interceptors and providers.**
The functions in `interceptor/base.py` never import `anthropic` or `openai` directly.
All provider-specific logic lives in the provider implementations.

**wrap() must handle both sync and async clients.** Client type detection happens
once at Guard construction via isinstance. Do not detect on every call. Use
`call()` for sync clients and `call_async()` for async clients. Never require
the developer to call different wrap functions. See D-024.

**core/types.py has no imports from other tokencap modules.**
It is the foundation. Everything depends on it. If types.py imports from anywhere
else in tokencap, you have a circular dependency waiting to happen.

**__init__.py contains the public API surface and the module-level API functions
(wrap, init, get_status, teardown).** The global Guard singleton lives here.
No business logic beyond these functions. All policy evaluation, interception,
and storage logic lives in the appropriate modules.

---

## Documentation Rules

**Update ARCHITECTURE.md when implementation diverges from the spec.**
If you discover during implementation that a design decision in ARCHITECTURE.md
is wrong or incomplete, do not silently implement something different. Note the
discrepancy in your report. The architecture doc is updated to reflect reality,
not the other way around, unless the Supervisor reviews and decides otherwise.

**Update DECISIONS.md when you make a significant design choice.**
A significant choice is one that a future contributor would reasonably ask
"why is it done this way?" If you are in doubt, record it.

**README examples must be copy-pasteable and correct.**
Do not write README examples that you have not verified work against the actual
implementation. Fictional examples in the README are worse than no examples.

---

## Phase Completion Checklist

Before reporting a phase complete, verify all of the following:

- [ ] All acceptance criteria in ARCHITECTURE.md for this phase pass
- [ ] `make lint` passes clean (ruff + `mypy --strict`, zero warnings)
- [ ] `make test` passes with zero failures and zero unexpected skips
- [ ] Every new file has a docstring on every public class and function
- [ ] ARCHITECTURE.md accurately describes what was built
- [ ] DECISIONS.md records any new decisions made during implementation
- [ ] No TODO, FIXME, or HACK comments left in any file (use GitHub issues instead)

---

## Rules Added After Expert Architecture Review

**Never use `type` as a dataclass field name.** Use `kind` instead. `type` is a
Python builtin. Using it as a field name shadows the builtin and breaks serialization
libraries. See D-014.

**Post-call reconciliation always uses force_increment, never check_and_increment.**
The reconciliation step must always succeed regardless of budget state. If you find
yourself calling `check_and_increment` in the post-call path, stop. You are using
the wrong method. See D-013.

**Never use bare str for enum-like fields.** Use `Literal[...]` so mypy catches
typos at the call site. `reset_every`, `action.kind`, and any other field with a
fixed set of valid string values must use `Literal`. See D-017.

**Validate invariants in __post_init__, not at call time.** If a dataclass field
has constraints (range, non-empty, etc.), check them in `__post_init__` and raise
`ValueError` with a clear message. Never let an invalid value propagate silently
to runtime. See D-016.

**Never use bare dict in Protocol signatures.** All `dict` types in Protocol
method signatures must be parameterised: `dict[str, Any]`, `dict[str, BudgetState]`,
etc. Bare `dict` tells mypy nothing and will fail `mypy --strict`.

**Callable types must include signatures.** `Callable | None` is not acceptable.
Use `Callable[[StatusResponse], None] | None`. Every `Callable` annotation must
specify argument types and return type.

**`py.typed` must exist and be included in the distribution.** Check that
`pyproject.toml` includes `tokencap/py.typed` in the package data. Without it,
downstream mypy users get no type information.

---

## Rules Added After Defaults Redesign

**Never apply a default silently.** Every default tokencap applies on behalf of
the developer must be printed to stdout on Guard creation. The startup message
is not optional code. It is part of the contract. See D-023.

**The default dimension name is "session", not "run".** Any code, comment, or
test that uses "run" as the default dimension name is wrong. See D-022.

**`wrap(client, limit=N)` is syntactic sugar, not a separate code path.** It
must produce exactly the same internal state as a Policy containing a "session"
DimensionPolicy with a BLOCK threshold at 100% and an auto UUID identifier.
`wrap(client, policy=...)` accepts a full Policy directly. `limit` and `policy`
are mutually exclusive — passing both raises `ConfigurationError`. See D-043.

**`quiet=True` suppresses stdout only.** It never suppresses OTEL emission,
logging, or any other output channel. The startup message and only the startup
message is affected by `quiet`.

---

## Rules Added After Interception Scope Clarification

**Never claim the wrapped client is a drop-in replacement.** It is not.
It proxies the common call paths. Client-returning SDK methods are
wrapped explicitly and return new guarded clients. See D-025 and D-026.

**All client-returning SDK methods must be explicit `*args, **kwargs` methods, never `__getattr__` delegation.** This applies to `with_options()`, `with_raw_response()`, and `with_streaming_response()` on both `GuardedAnthropic` and `GuardedOpenAI`. Each must return a new guarded wrapper bound to the same `Guard`. Before closing Phase 2, scan the installed SDK source for any additional client-returning methods and either wrap them or document them as known gaps. See D-027.

**Document interception scope in code comments.** Every method on GuardedMessages
and GuardedOpenAI that passes through untracked must have a comment saying so.
Do not leave undocumented pass-throughs.

---

## Rules Added After Interceptor Depth Review

**interceptor/base.py contains functions, not a class.** There is no InterceptorBase
to instantiate. The functions are `call()`, `call_async()`, `call_stream()`. All
take `guard` as an explicit argument. See D-028.

**GuardedMessages and GuardedCompletions must have their own `__getattr__`.**
Pass-through on the resource proxy is not automatic. Without it, `client.messages.batch`
and any other sub-resource attribute raises AttributeError instead of delegating.

**OpenAI streaming must inject `stream_options={"include_usage": True}` before
calling `call_stream()`.** Do this with `setdefault` on a copy of kwargs. Never
mutate the caller's dict. Never skip this. Without it, reconciliation never fires
for OpenAI streaming calls. See D-030.

**`GuardedStream.__exit__` must handle early exit with a WARNING, not an error.**
If `extract_usage()` returns zero or raises, log a WARNING and treat the pre-call
estimate as the final count. Never raise from `__exit__`. See D-029.

**Backend must implement `is_threshold_fired()` and `mark_threshold_fired()`.**
These are part of the Backend Protocol, not optional. The SQLite schema must
include the `fired_thresholds` table. The Redis backend must implement the
corresponding key operations. `reset()` must clear fired records. See D-031.

**The `@property` pattern is mandatory for resource interception.** `messages` on
GuardedAnthropic and `chat` on GuardedOpenAI must be `@property`, not handled by
`__getattr__`. If they were handled by `__getattr__`, the real SDK object would be
returned and no interception would occur.

---

## Version Control Rules

**Never push directly to main. No exceptions.** All changes reach main
through a pull request. This includes documentation-only changes, single-line
fixes, and reverts. If it touches main, it goes through a PR. Branch naming:
phase-N/short-description (e.g. phase-1/foundation). Open a pull request
when the phase is complete and all acceptance criteria pass. Never merge
without a passing test run.

---

## Testing Rules

**Always run the full test suite after any logic change.** A change is
not done until tests pass. Run make test before reporting a task complete.

**Add tests for every new behavior.** If you add a function, add a test.
If you fix a bug, add a test that would have caught it. Tests live in
tests/unit/ for unit tests and tests/integration/ for integration tests.

**Unit tests mock at the function and class level.** Use unittest.mock.
No real API calls, no real Redis connections, no real file I/O beyond
tmp_path.

**Integration tests mock at the HTTP layer.** Use pytest-httpx to
intercept HTTP calls made by the Anthropic and OpenAI SDKs. Real SDK
code runs, real tokencap code runs, fake HTTP responses are returned.
No credentials required. Always run as part of make test in CI.

**Live tests handle missing credentials by mocking response objects.**
Tests in tests/live/ attempt real API calls when ANTHROPIC_API_KEY or
OPENAI_API_KEY is present. When a key is absent, the test constructs a
mock response object that matches the real SDK response shape and runs
the full tokencap code path against it. The test never skips. It always
exercises the full code path. Only the network call itself is replaced
when credentials are missing.

**Live tests run via make test-live.** They never block CI. They are not
part of make test.

**Test file naming mirrors the source tree 1:1.** tests/unit/test_backends.py
tests tokencap/backends/. tests/unit/test_interceptor.py tests
tokencap/interceptor/.
