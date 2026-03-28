"""Tests for tokencap.backends (SQLiteBackend and RedisBackend).

Common behavioral tests are parametrized over both backends.
Backend-specific tests (concurrent writes, import errors) are separate.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

import pytest

from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.types import BudgetKey

# ---------------------------------------------------------------------------
# MockRedisClient — simulates Redis GET/SET/INCRBY/EXISTS/DEL with a Lock
# for atomic Lua script simulation
# ---------------------------------------------------------------------------


class MockRedisClient:
    """Thread-safe mock Redis client for unit testing RedisBackend."""

    def __init__(self) -> None:
        """Initialise empty store and lock."""
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()
        self._scripts: dict[str, str] = {}
        self._script_counter = 0

    def get(self, key: str) -> str | None:
        """GET key."""
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        """SET key value."""
        self._store[key] = str(value)

    def exists(self, key: str) -> int:
        """EXISTS key."""
        return 1 if key in self._store else 0

    def delete(self, *keys: str) -> int:
        """DEL keys."""
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    def close(self) -> None:
        """No-op close."""

    def register_script(self, script: str) -> Any:
        """Register a Lua script and return a callable."""
        self._script_counter += 1
        script_id = f"script_{self._script_counter}"
        self._scripts[script_id] = script

        def execute(keys: list[str] | None = None, args: list[Any] | None = None) -> Any:
            return self._eval_script(script, keys or [], args or [])

        return execute

    def _eval_script(self, script: str, keys: list[str], args: list[Any]) -> Any:
        """Simulate Lua script execution atomically under a lock."""
        with self._lock:
            if "INCRBY" in script and "violated" not in script:
                tokens = int(args[0])
                result = []
                for k in keys:
                    cur = int(self._store.get(k, "0"))
                    self._store[k] = str(cur + tokens)
                    result.append(int(self._store[k]))
                return result
            if "violated" in script:
                tokens = int(args[0])
                dim_names = args[1:]
                n = len(keys) // 2
                violated = []
                states = []
                for i in range(n):
                    used_key = keys[i * 2]
                    limit_key = keys[i * 2 + 1]
                    used = int(self._store.get(used_key, "0"))
                    limit = int(self._store.get(limit_key, "0"))
                    if (used + tokens) > limit:
                        violated.append(dim_names[i])
                    states.append(used)
                    states.append(limit)
                if violated:
                    ret: list[Any] = [0, *states, "---", *violated]
                    return ret
                for i in range(n):
                    used_key = keys[i * 2]
                    cur = int(self._store.get(used_key, "0"))
                    self._store[used_key] = str(cur + tokens)
                final: list[Any] = [1]
                for i in range(n):
                    used_key = keys[i * 2]
                    limit_key = keys[i * 2 + 1]
                    final.append(int(self._store.get(used_key, "0")))
                    final.append(int(self._store.get(limit_key, "0")))
                return final
            if "SCAN" in script:
                used_key = keys[0]
                pattern = args[0]
                self._store[used_key] = "0"
                prefix = pattern.replace("*", "")
                to_del = [k for k in self._store if k.startswith(prefix)]
                for k in to_del:
                    del self._store[k]
                return 1
            return None

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = True) -> MockRedisClient:
        """Factory matching redis.Redis.from_url signature."""
        return cls()


# ---------------------------------------------------------------------------
# Backend fixtures
# ---------------------------------------------------------------------------


def _make_mock_redis_backend() -> Any:
    """Build a RedisBackend backed by MockRedisClient."""
    from tokencap.backends.redis import (
        _CHECK_AND_INCREMENT_LUA,
        _FORCE_INCREMENT_LUA,
        _RESET_LUA,
        RedisBackend,
    )

    client = MockRedisClient()
    backend = RedisBackend.__new__(RedisBackend)
    backend._client = client
    backend._check_script = client.register_script(_CHECK_AND_INCREMENT_LUA)
    backend._force_script = client.register_script(_FORCE_INCREMENT_LUA)
    backend._reset_script = client.register_script(_RESET_LUA)
    return backend


@pytest.fixture(params=["sqlite", "redis"])
def backend(request: pytest.FixtureRequest, tmp_path: Any) -> Any:
    """Parametrized backend fixture: SQLiteBackend and RedisBackend (mocked)."""
    if request.param == "sqlite":
        b = SQLiteBackend(path=str(tmp_path / "test.db"))
        yield b
        b.close()
    else:
        yield _make_mock_redis_backend()


# ---------------------------------------------------------------------------
# Parametrized common behavioral tests
# ---------------------------------------------------------------------------


class TestSetLimit:
    """set_limit and get_states — parametrized over both backends."""

    def test_set_limit_and_get_states(self, backend: Any, sample_key: BudgetKey) -> None:
        """set_limit registers a key; get_states returns correct initial state."""
        backend.set_limit(sample_key, 1000)
        states = backend.get_states([sample_key])
        state = states["session"]
        assert state.limit == 1000
        assert state.used == 0
        assert state.remaining == 1000
        assert state.pct_used == 0.0

    def test_get_states_unknown_key(self, backend: Any) -> None:
        """get_states for an unknown key returns a zero BudgetState."""
        unknown = BudgetKey(dimension="unknown", identifier="none")
        states = backend.get_states([unknown])
        assert states["unknown"].limit == 0
        assert states["unknown"].used == 0
        assert states["unknown"].remaining == 0


class TestCheckAndIncrement:
    """check_and_increment — parametrized over both backends."""

    def test_allowed(self, backend: Any, sample_key: BudgetKey) -> None:
        """Increment within limit succeeds."""
        backend.set_limit(sample_key, 1000)
        result = backend.check_and_increment([sample_key], 500)
        assert result.allowed is True
        assert result.violated == []
        assert result.states["session"].used == 500

    def test_rejected_over_limit(self, backend: Any, sample_key: BudgetKey) -> None:
        """Increment exceeding limit is rejected with zero increment."""
        backend.set_limit(sample_key, 100)
        result = backend.check_and_increment([sample_key], 200)
        assert result.allowed is False
        assert "session" in result.violated
        states = backend.get_states([sample_key])
        assert states["session"].used == 0

    def test_multiple_keys_one_violated(self, backend: Any) -> None:
        """When one key would violate, nothing is incremented on any key."""
        key_a = BudgetKey(dimension="dim_a", identifier="id_a")
        key_b = BudgetKey(dimension="dim_b", identifier="id_b")
        backend.set_limit(key_a, 1000)
        backend.set_limit(key_b, 50)
        result = backend.check_and_increment([key_a, key_b], 100)
        assert result.allowed is False
        assert "dim_b" in result.violated
        states = backend.get_states([key_a, key_b])
        assert states["dim_a"].used == 0
        assert states["dim_b"].used == 0

    def test_multiple_keys_all_allowed(self, backend: Any) -> None:
        """When all keys are within limits, all are incremented."""
        key_a = BudgetKey(dimension="dim_a", identifier="id_a")
        key_b = BudgetKey(dimension="dim_b", identifier="id_b")
        backend.set_limit(key_a, 1000)
        backend.set_limit(key_b, 1000)
        result = backend.check_and_increment([key_a, key_b], 100)
        assert result.allowed is True
        assert result.states["dim_a"].used == 100
        assert result.states["dim_b"].used == 100

    def test_one_past_limit(self, backend: Any, sample_key: BudgetKey) -> None:
        """Filling budget exactly then adding 1 more token is rejected."""
        backend.set_limit(sample_key, 100)
        result = backend.check_and_increment([sample_key], 100)
        assert result.allowed is True
        result = backend.check_and_increment([sample_key], 1)
        assert result.allowed is False


class TestForceIncrement:
    """force_increment — parametrized over both backends."""

    def test_force_increment_basic(self, backend: Any, sample_key: BudgetKey) -> None:
        """force_increment within limit succeeds."""
        backend.set_limit(sample_key, 1000)
        states = backend.force_increment([sample_key], 500)
        assert states["session"].used == 500

    def test_force_increment_beyond_limit(self, backend: Any, sample_key: BudgetKey) -> None:
        """force_increment succeeds even when over limit."""
        backend.set_limit(sample_key, 100)
        states = backend.force_increment([sample_key], 200)
        assert states["session"].used == 200
        assert states["session"].remaining == -100

    def test_force_increment_after_budget_full(self, backend: Any, sample_key: BudgetKey) -> None:
        """force_increment works after check_and_increment filled the budget."""
        backend.set_limit(sample_key, 100)
        backend.check_and_increment([sample_key], 100)
        states = backend.force_increment([sample_key], 50)
        assert states["session"].used == 150
        assert states["session"].remaining == -50


class TestThresholdFired:
    """is_threshold_fired and mark_threshold_fired — parametrized over both backends."""

    def test_default_not_fired(self, backend: Any, sample_key: BudgetKey) -> None:
        """A threshold is not fired by default."""
        assert backend.is_threshold_fired(sample_key, 0.8) is False

    def test_mark_and_check(self, backend: Any, sample_key: BudgetKey) -> None:
        """Marking a threshold makes is_threshold_fired return True."""
        backend.mark_threshold_fired(sample_key, 0.8)
        assert backend.is_threshold_fired(sample_key, 0.8) is True


class TestReset:
    """reset — parametrized over both backends."""

    def test_reset_clears_used_and_thresholds(
        self, backend: Any, sample_key: BudgetKey
    ) -> None:
        """reset zeros used and clears fired thresholds."""
        backend.set_limit(sample_key, 1000)
        backend.force_increment([sample_key], 500)
        backend.mark_threshold_fired(sample_key, 0.8)
        backend.reset(sample_key)
        states = backend.get_states([sample_key])
        assert states["session"].used == 0
        assert states["session"].limit == 1000
        assert backend.is_threshold_fired(sample_key, 0.8) is False


# ---------------------------------------------------------------------------
# SQLite-specific tests
# ---------------------------------------------------------------------------


class TestSQLiteSpecific:
    """SQLite-specific tests that do not apply to Redis."""

    def test_set_limit_idempotent(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Calling set_limit twice updates the limit."""
        sqlite_backend.set_limit(sample_key, 500)
        sqlite_backend.set_limit(sample_key, 1000)
        states = sqlite_backend.get_states([sample_key])
        assert states["session"].limit == 1000

    def test_set_limit_preserves_used(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Updating a limit does not reset used_tokens."""
        sqlite_backend.set_limit(sample_key, 1000)
        sqlite_backend.force_increment([sample_key], 200)
        sqlite_backend.set_limit(sample_key, 2000)
        states = sqlite_backend.get_states([sample_key])
        assert states["session"].used == 200
        assert states["session"].limit == 2000

    def test_exact_limit(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Increment exactly to the limit succeeds."""
        sqlite_backend.set_limit(sample_key, 100)
        result = sqlite_backend.check_and_increment([sample_key], 100)
        assert result.allowed is True
        assert result.states["session"].used == 100

    def test_sequential_increments(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Multiple sequential increments accumulate correctly."""
        sqlite_backend.set_limit(sample_key, 1000)
        sqlite_backend.check_and_increment([sample_key], 300)
        sqlite_backend.check_and_increment([sample_key], 300)
        result = sqlite_backend.check_and_increment([sample_key], 300)
        assert result.allowed is True
        assert result.states["session"].used == 900
        result = sqlite_backend.check_and_increment([sample_key], 200)
        assert result.allowed is False

    def test_mark_idempotent(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Marking the same threshold twice does not error."""
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is True

    def test_different_pct_not_affected(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Marking one threshold does not affect others."""
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 1.0) is False


class TestSQLiteConcurrentWrites:
    """Atomicity acceptance test for SQLiteBackend."""

    def test_concurrent_writes(self, tmp_db: str) -> None:
        """10 threads x 100 increments of 1 token, limit=2000, final used=1000."""
        b = SQLiteBackend(path=tmp_db)
        key = BudgetKey(dimension="session", identifier="concurrent-test")
        b.set_limit(key, 2000)

        num_threads = 10
        increments_per_thread = 100
        barrier = threading.Barrier(num_threads)
        errors: list[str] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(increments_per_thread):
                    b.check_and_increment([key], 1)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        states = b.get_states([key])
        assert states["session"].used == num_threads * increments_per_thread
        b.close()


# ---------------------------------------------------------------------------
# Redis-specific tests
# ---------------------------------------------------------------------------


class TestRedisImportError:
    """Test that RedisBackend raises ImportError when redis not installed."""

    def test_raises_import_error(self) -> None:
        """RedisBackend() raises ImportError with install instructions."""
        with patch.dict("sys.modules", {"redis": None}), pytest.raises(
            ImportError, match="pip install redis"
        ):
            from importlib import reload

            import tokencap.backends.redis as redis_mod
            reload(redis_mod)
            redis_mod.RedisBackend()


class TestRedisConcurrentWrites:
    """Atomicity acceptance test for RedisBackend (mocked)."""

    def test_concurrent_writes(self) -> None:
        """10 threads x 100 increments of 1 token, limit=2000, final used=1000."""
        b = _make_mock_redis_backend()
        key = BudgetKey(dimension="session", identifier="concurrent-test")
        b.set_limit(key, 2000)

        num_threads = 10
        increments_per_thread = 100
        barrier = threading.Barrier(num_threads)
        errors: list[str] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(increments_per_thread):
                    b.check_and_increment([key], 1)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        states = b.get_states([key])
        assert states["session"].used == num_threads * increments_per_thread
