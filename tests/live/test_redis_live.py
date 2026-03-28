"""Live tests for RedisBackend.

When Redis is reachable: runs the full concurrent write test against real Redis.
When Redis is not reachable: falls back to MockRedisClient and runs the same test.
Never skips.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from tokencap.core.types import BudgetKey


def _get_redis_url() -> str:
    """Read REDIS_URL from environment, default to localhost."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


def _try_connect_redis(url: str) -> Any | None:
    """Attempt to connect to Redis. Returns client or None."""
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def test_redis_concurrent_writes() -> None:
    """Concurrent write atomicity test. Never skips.

    10 threads x 100 increments of 1 token, limit=2000, final used=1000.
    """
    url = _get_redis_url()
    real_client = _try_connect_redis(url)

    if real_client is not None:
        # Real Redis path
        from tokencap.backends.redis import RedisBackend

        backend = RedisBackend(url=url)
        # Clean up any leftover keys from previous test runs
        key = BudgetKey(dimension="session", identifier="live-concurrent-test")
        backend.reset(key)
        backend.set_limit(key, 2000)
    else:
        # Mock fallback path
        from tests.unit.test_backends import MockRedisClient
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

        key = BudgetKey(dimension="session", identifier="live-concurrent-test")
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

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"

    states = backend.get_states([key])
    assert states["session"].used == num_threads * increments_per_thread

    # Cleanup
    if real_client is not None:
        backend.reset(key)
    backend.close()
