# Contributing to tokencap

## Prerequisites

- Python 3.9 or later
- Docker (for Redis live tests)
- Git

## Setup

Clone the repo and install in editable mode with all dependencies:

```bash
git clone https://github.com/pykul/tokencap
cd tokencap
pip install -e ".[dev]"
```

The `dev` extra includes both provider SDKs (anthropic, openai, tiktoken),
redis, opentelemetry-api, and all dev tools (pytest, pytest-httpx, mypy, ruff).
A single install gives you everything needed to run tests and lint.

## Environment variables

**Provider API keys** are the standard env vars for each SDK. Set them in your
shell or a `.env` file. When absent, the live provider tests run in mock
fallback mode automatically — no credentials are needed for `make test` or
`make test-live`.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

**`REDIS_URL`** controls which Redis instance tokencap connects to. This
applies to both local development and production use of `RedisBackend`.

For local development, `make redis-up` starts a container at the default
address — no configuration needed:

```bash
make redis-up
# REDIS_URL defaults to redis://localhost:6379
```

For production or a remote Redis instance:

```bash
export REDIS_URL=redis://your-redis-host:6379
```

## Running tests

There are three test tiers:

**`make test`** runs unit and integration tests. No credentials, no external
services required. This is what CI runs on every push. Always run before
committing.

```bash
make test
```

**`make test-live`** runs live tests against real services. The Anthropic and
OpenAI tests run in mock fallback mode automatically when `ANTHROPIC_API_KEY`
or `OPENAI_API_KEY` are absent — they never skip. The Redis test requires a
running Redis instance.

```bash
make redis-up      # start local Redis container
make test-live     # run all live tests
make redis-down    # stop Redis container
```

**Redis live tests** read `REDIS_URL` from the environment and default to
`redis://localhost:6379` if not set. When Redis is not reachable, the test
falls back to an in-memory mock and still exercises the full code path.

## Smoke test

`scripts/smoke_test.py` runs every tokencap feature against real Anthropic and
OpenAI APIs. It is the human verification step before a release. It is not part
of CI.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
python scripts/smoke_test.py
```

Uses the cheapest available models with minimal tokens (~$0.001 total). Requires
real API keys — no mock fallback.

## Running lint

```bash
make lint
```

Runs `ruff check` on all source and test files and `mypy --strict` on all
source files under `tokencap/`. Must pass before every commit. CI will reject
a push that fails lint.

## Branch and PR conventions

- Never push directly to main. No exceptions. This includes documentation-only
  changes, single-line fixes, and reverts.
- Branch naming: `phase-N/short-description` (e.g. `phase-1/foundation`).
- Open a pull request when work is complete and all tests pass.
- Never merge without a passing CI run.

## Making design decisions

When you make a significant design choice, record it in `DECISIONS.md`. A
significant choice is one a future contributor would ask "why is it done this
way?" When in doubt, record it. See `DECISIONS.md` for examples of the format.

## Architecture

`ARCHITECTURE.md` is the single source of truth for all architectural
decisions. Read it in full before making any code changes. When implementation
diverges from the spec, update the architecture document to reflect reality.

`CLAUDE.md` contains standing rules that accumulate as the project matures.
Every rule exists because something went wrong or almost went wrong without it.

## License

Apache 2.0. See `LICENSE`.
