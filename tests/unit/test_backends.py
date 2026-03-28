"""Tests for tokencap.backends.sqlite.SQLiteBackend."""

from __future__ import annotations

import threading

from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.types import BudgetKey


class TestSetLimitAndGetStates:
    """Tests for set_limit and get_states."""

    def test_set_limit_and_get_states(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """set_limit registers a key; get_states returns correct initial state."""
        sqlite_backend.set_limit(sample_key, 1000)
        states = sqlite_backend.get_states([sample_key])

        state = states["session"]
        assert state.key == sample_key
        assert state.limit == 1000
        assert state.used == 0
        assert state.remaining == 1000
        assert state.pct_used == 0.0

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

    def test_get_states_unknown_key(self, sqlite_backend: SQLiteBackend) -> None:
        """get_states for an unknown key returns a zero BudgetState."""
        unknown = BudgetKey(dimension="unknown", identifier="none")
        states = sqlite_backend.get_states([unknown])
        state = states["unknown"]
        assert state.limit == 0
        assert state.used == 0
        assert state.remaining == 0
        assert state.pct_used == 0.0


class TestCheckAndIncrement:
    """Tests for check_and_increment."""

    def test_allowed(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Increment within limit succeeds."""
        sqlite_backend.set_limit(sample_key, 1000)
        result = sqlite_backend.check_and_increment([sample_key], 500)
        assert result.allowed is True
        assert result.violated == []
        assert result.states["session"].used == 500
        assert result.states["session"].remaining == 500

    def test_rejected_over_limit(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Increment exceeding limit is rejected with zero increment."""
        sqlite_backend.set_limit(sample_key, 100)
        result = sqlite_backend.check_and_increment([sample_key], 200)
        assert result.allowed is False
        assert "session" in result.violated
        # Verify nothing was incremented
        states = sqlite_backend.get_states([sample_key])
        assert states["session"].used == 0

    def test_exact_limit(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Increment exactly to the limit succeeds."""
        sqlite_backend.set_limit(sample_key, 100)
        result = sqlite_backend.check_and_increment([sample_key], 100)
        assert result.allowed is True
        assert result.states["session"].used == 100
        assert result.states["session"].remaining == 0

    def test_one_past_limit(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Increment one token past the limit is rejected."""
        sqlite_backend.set_limit(sample_key, 100)
        result = sqlite_backend.check_and_increment([sample_key], 101)
        assert result.allowed is False

    def test_multiple_keys_one_violated(
        self, sqlite_backend: SQLiteBackend
    ) -> None:
        """When one key would violate, nothing is incremented on any key."""
        key_a = BudgetKey(dimension="dim_a", identifier="id_a")
        key_b = BudgetKey(dimension="dim_b", identifier="id_b")
        sqlite_backend.set_limit(key_a, 1000)
        sqlite_backend.set_limit(key_b, 50)

        result = sqlite_backend.check_and_increment([key_a, key_b], 100)
        assert result.allowed is False
        assert "dim_b" in result.violated

        # Neither key was incremented
        states = sqlite_backend.get_states([key_a, key_b])
        assert states["dim_a"].used == 0
        assert states["dim_b"].used == 0

    def test_multiple_keys_all_allowed(
        self, sqlite_backend: SQLiteBackend
    ) -> None:
        """When all keys are within limits, all are incremented."""
        key_a = BudgetKey(dimension="dim_a", identifier="id_a")
        key_b = BudgetKey(dimension="dim_b", identifier="id_b")
        sqlite_backend.set_limit(key_a, 1000)
        sqlite_backend.set_limit(key_b, 1000)

        result = sqlite_backend.check_and_increment([key_a, key_b], 100)
        assert result.allowed is True
        assert result.states["dim_a"].used == 100
        assert result.states["dim_b"].used == 100

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

        # Next increment would exceed
        result = sqlite_backend.check_and_increment([sample_key], 200)
        assert result.allowed is False


class TestForceIncrement:
    """Tests for force_increment."""

    def test_force_increment_succeeds(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """force_increment always succeeds."""
        sqlite_backend.set_limit(sample_key, 1000)
        states = sqlite_backend.force_increment([sample_key], 500)
        assert states["session"].used == 500

    def test_force_increment_beyond_limit(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """force_increment succeeds even when over limit."""
        sqlite_backend.set_limit(sample_key, 100)
        states = sqlite_backend.force_increment([sample_key], 200)
        assert states["session"].used == 200
        assert states["session"].remaining == -100
        assert states["session"].pct_used == 2.0

    def test_force_increment_after_check(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """force_increment works after check_and_increment filled the budget."""
        sqlite_backend.set_limit(sample_key, 100)
        sqlite_backend.check_and_increment([sample_key], 100)
        states = sqlite_backend.force_increment([sample_key], 50)
        assert states["session"].used == 150
        assert states["session"].remaining == -50


class TestReset:
    """Tests for reset."""

    def test_reset_clears_used(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """reset zeros used_tokens without changing the limit."""
        sqlite_backend.set_limit(sample_key, 1000)
        sqlite_backend.force_increment([sample_key], 500)
        sqlite_backend.reset(sample_key)
        states = sqlite_backend.get_states([sample_key])
        assert states["session"].used == 0
        assert states["session"].limit == 1000

    def test_reset_clears_fired_thresholds(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """reset clears fired threshold records."""
        sqlite_backend.set_limit(sample_key, 1000)
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is True
        sqlite_backend.reset(sample_key)
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is False


class TestThresholdFired:
    """Tests for is_threshold_fired and mark_threshold_fired."""

    def test_default_not_fired(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """A threshold is not fired by default."""
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is False

    def test_mark_and_check(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Marking a threshold makes is_threshold_fired return True."""
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is True

    def test_different_pct_not_affected(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Marking one threshold does not affect others."""
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 1.0) is False

    def test_mark_idempotent(
        self, sqlite_backend: SQLiteBackend, sample_key: BudgetKey
    ) -> None:
        """Marking the same threshold twice does not error."""
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        sqlite_backend.mark_threshold_fired(sample_key, 0.8)
        assert sqlite_backend.is_threshold_fired(sample_key, 0.8) is True


class TestConcurrentWrites:
    """Atomicity acceptance test for SQLiteBackend."""

    def test_concurrent_writes(self, tmp_db: str) -> None:
        """10 threads x 100 increments of 1 token, limit=2000, final used=1000."""
        backend = SQLiteBackend(path=tmp_db)
        key = BudgetKey(dimension="session", identifier="concurrent-test")
        backend.set_limit(key, 2000)

        num_threads = 10
        increments_per_thread = 100
        barrier = threading.Barrier(num_threads)
        errors: list[str] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(increments_per_thread):
                    backend.check_and_increment([key], 1)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=worker) for _ in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        states = backend.get_states([key])
        assert states["session"].used == num_threads * increments_per_thread
        backend.close()
