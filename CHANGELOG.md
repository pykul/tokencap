# Changelog

All notable changes to tokencap are documented here.

## v0.2.0 — Documentation and packaging improvements

### Changed
- Simplified README with clearer problem framing and "What it is" section
- Added CONTRIBUTING.md with full development guide
- Added RELEASING.md with release process documentation
- Added tag protection documentation
- Automated PyPI publishing via GitHub Actions on tag push

## v0.1.0 — Initial release

### Added
- `wrap()` for explicit client wrapping with tracking, limits, and full policy control
- `patch()` for framework integration via opt-in monkey-patching with per-provider control via `providers=` parameter
- `unpatch()` to fully reverse `patch()` effects
- `SQLiteBackend`: zero-config local storage with atomic writes
- `RedisBackend`: distributed storage with Lua script atomicity
- Policy engine: WARN, BLOCK, DEGRADE, WEBHOOK actions with configurable thresholds
- Multi-dimensional budgets: track session, tenant, daily, or any custom dimension simultaneously
- OpenTelemetry emission: metrics and spans, no-op if not installed
- `client.get_status()` on wrapped clients
- `ActionKind`, `Provider`, `ResetPeriod` enums with `str` backwards compatibility
- LangChain, CrewAI, LlamaIndex, AutoGen support via `patch()`
- `scripts/smoke_test.py`: 67-test live verification script
- Full async support for both Anthropic and OpenAI
