"""Redis-backed storage backend for tokencap.

Distributed mode. All writes use Lua scripts for atomicity.
Requires the redis package: pip install redis.
"""

from __future__ import annotations

from typing import Any

from tokencap.core.types import BudgetKey, BudgetState, CheckResult

# Lua script: check all keys, reject if any over limit, otherwise INCRBY all.
# KEYS: alternating [used_key_1, limit_key_1, used_key_2, limit_key_2, ...]
# ARGV: [tokens, dim_name_1, dim_name_2, ...]
_CHECK_AND_INCREMENT_LUA = """
local tokens = tonumber(ARGV[1])
local n = (#KEYS) / 2
local violated = {}
local results = {}

-- Phase 1: read all
for i = 1, n do
    local used_key = KEYS[i * 2 - 1]
    local limit_key = KEYS[i * 2]
    local used = tonumber(redis.call('GET', used_key) or '0')
    local limit = tonumber(redis.call('GET', limit_key) or '0')
    if (used + tokens) > limit then
        violated[#violated + 1] = ARGV[i + 1]
    end
    results[#results + 1] = used
    results[#results + 1] = limit
end

-- Phase 2: reject or increment
if #violated > 0 then
    -- Return: 0 (rejected), then per-key [used, limit], then violated names
    local ret = {0}
    for _, v in ipairs(results) do ret[#ret + 1] = v end
    ret[#ret + 1] = '---'
    for _, v in ipairs(violated) do ret[#ret + 1] = v end
    return ret
end

-- Increment all
for i = 1, n do
    local used_key = KEYS[i * 2 - 1]
    redis.call('INCRBY', used_key, tokens)
end

-- Re-read after increment
local ret = {1}
for i = 1, n do
    local used_key = KEYS[i * 2 - 1]
    local limit_key = KEYS[i * 2]
    local used = tonumber(redis.call('GET', used_key) or '0')
    local limit = tonumber(redis.call('GET', limit_key) or '0')
    ret[#ret + 1] = used
    ret[#ret + 1] = limit
end
return ret
"""

# Lua script: INCRBY all keys unconditionally.
# KEYS: [used_key_1, used_key_2, ...]
# ARGV: [tokens]
_FORCE_INCREMENT_LUA = """
local tokens = tonumber(ARGV[1])
local ret = {}
for i = 1, #KEYS do
    redis.call('INCRBY', KEYS[i], tokens)
    ret[#ret + 1] = tonumber(redis.call('GET', KEYS[i]) or '0')
end
return ret
"""

# Lua script: zero used key and delete all fired threshold keys.
# KEYS: [used_key]
# ARGV: [fired_pattern]
_RESET_LUA = """
redis.call('SET', KEYS[1], 0)
local cursor = '0'
repeat
    local result = redis.call('SCAN', cursor, 'MATCH', ARGV[1], 'COUNT', 100)
    cursor = result[1]
    local keys = result[2]
    for _, k in ipairs(keys) do
        redis.call('DEL', k)
    end
until cursor == '0'
return 1
"""


def _used_key(key: BudgetKey) -> str:
    """Build the Redis key for used tokens."""
    return f"tokencap:used:{key.dimension}:{key.identifier}"


def _limit_key(key: BudgetKey) -> str:
    """Build the Redis key for the token limit."""
    return f"tokencap:limit:{key.dimension}:{key.identifier}"


def _fired_key(key: BudgetKey, at_pct: float) -> str:
    """Build the Redis key for a fired threshold."""
    return f"tokencap:fired:{key.dimension}:{key.identifier}:{at_pct}"


def _build_state(key: BudgetKey, limit: int, used: int) -> BudgetState:
    """Construct a BudgetState from raw values."""
    remaining = limit - used
    pct_used = used / limit if limit > 0 else 0.0
    return BudgetState(
        key=key,
        limit=limit,
        used=used,
        remaining=remaining,
        pct_used=pct_used,
    )


class RedisBackend:
    """Redis storage backend implementing the Backend protocol.

    All writes use Lua scripts for atomicity. Requires the redis package.
    """

    def __init__(self, url: str = "redis://localhost:6379") -> None:
        """Initialise with a Redis connection URL."""
        try:
            import redis as redis_lib
        except ImportError as err:
            raise ImportError(
                "RedisBackend requires the redis package. "
                "Install it with: pip install redis"
            ) from err
        self._client: Any = redis_lib.Redis.from_url(url, decode_responses=True)
        self._check_script: Any = self._client.register_script(_CHECK_AND_INCREMENT_LUA)
        self._force_script: Any = self._client.register_script(_FORCE_INCREMENT_LUA)
        self._reset_script: Any = self._client.register_script(_RESET_LUA)

    def check_and_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> CheckResult:
        """Atomic check-then-increment across all keys via Lua script."""
        redis_keys: list[str] = []
        dim_names: list[str] = []
        for key in keys:
            redis_keys.append(_used_key(key))
            redis_keys.append(_limit_key(key))
            dim_names.append(key.dimension)

        result = self._check_script(keys=redis_keys, args=[tokens, *dim_names])

        allowed = int(result[0]) == 1
        states: dict[str, BudgetState] = {}

        for i, key in enumerate(keys):
            used = int(result[1 + i * 2])
            limit = int(result[2 + i * 2])
            states[key.dimension] = _build_state(key, limit, used)

        violated: list[str] = []
        if not allowed:
            sep_idx = result.index("---")
            violated = [str(v) for v in result[sep_idx + 1 :]]

        return CheckResult(allowed=allowed, states=states, violated=violated)

    def force_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> dict[str, BudgetState]:
        """Unconditional increment via Lua script. Never rejects."""
        redis_keys = [_used_key(key) for key in keys]
        result = self._force_script(keys=redis_keys, args=[tokens])

        states: dict[str, BudgetState] = {}
        for i, key in enumerate(keys):
            used = int(result[i])
            limit_val = self._client.get(_limit_key(key))
            limit = int(limit_val) if limit_val is not None else 0
            states[key.dimension] = _build_state(key, limit, used)
        return states

    def get_states(self, keys: list[BudgetKey]) -> dict[str, BudgetState]:
        """Non-atomic read of current state for a list of keys."""
        states: dict[str, BudgetState] = {}
        for key in keys:
            used_val = self._client.get(_used_key(key))
            limit_val = self._client.get(_limit_key(key))
            used = int(used_val) if used_val is not None else 0
            limit = int(limit_val) if limit_val is not None else 0
            states[key.dimension] = _build_state(key, limit, used)
        return states

    def set_limit(self, key: BudgetKey, limit: int) -> None:
        """Register or update a budget limit for a key. Idempotent."""
        self._client.set(_limit_key(key), limit)
        # Ensure the used key exists
        if self._client.get(_used_key(key)) is None:
            self._client.set(_used_key(key), 0)

    def reset(self, key: BudgetKey) -> None:
        """Reset used_tokens to zero and clear fired thresholds for this key."""
        fired_pattern = f"tokencap:fired:{key.dimension}:{key.identifier}:*"
        self._reset_script(keys=[_used_key(key)], args=[fired_pattern])

    def is_threshold_fired(self, key: BudgetKey, at_pct: float) -> bool:
        """Return True if the threshold has already fired for this key."""
        return bool(self._client.exists(_fired_key(key, at_pct)))

    def mark_threshold_fired(self, key: BudgetKey, at_pct: float) -> None:
        """Record that a threshold has fired for this key. Idempotent."""
        self._client.set(_fired_key(key, at_pct), "1")

    def close(self) -> None:
        """Close the Redis connection."""
        self._client.close()
