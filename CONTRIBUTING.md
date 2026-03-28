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
pip install redis opentelemetry-api
```

The `dev` extra includes both provider SDKs (anthropic, openai, tiktoken)
and all dev tools (pytest, pytest-httpx, mypy, ruff). Redis and
opentelemetry-api are installed separately because they are optional
runtime dependencies, not dev-only tools.

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
