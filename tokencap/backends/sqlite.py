"""SQLite-backed storage backend for tokencap.

Zero-infra default. Multiple agents and processes on the same machine share
state automatically as long as they point to the same file.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from tokencap.core.types import BudgetKey, BudgetState, CheckResult


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


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


class SQLiteBackend:
    """SQLite storage backend implementing the Backend protocol.

    Default file path: ``tokencap.db`` in the current working directory.
    Uses ``BEGIN IMMEDIATE`` for write serialisation so concurrent increments
    are safe across threads and processes.
    """

    def __init__(self, path: str = "tokencap.db") -> None:
        """Initialise with a path to the SQLite database file."""
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self) -> None:
        """Create the schema if it does not exist."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS budgets (
                key_dimension  TEXT    NOT NULL,
                key_identifier TEXT    NOT NULL,
                limit_tokens   INTEGER NOT NULL,
                used_tokens    INTEGER NOT NULL DEFAULT 0,
                updated_at     TEXT    NOT NULL,
                PRIMARY KEY (key_dimension, key_identifier)
            );

            CREATE TABLE IF NOT EXISTS fired_thresholds (
                key_dimension  TEXT    NOT NULL,
                key_identifier TEXT    NOT NULL,
                at_pct         REAL    NOT NULL,
                fired_at       TEXT    NOT NULL,
                PRIMARY KEY (key_dimension, key_identifier, at_pct)
            );
            """
        )

    def check_and_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> CheckResult:
        """Atomic check-then-increment across all keys.

        Uses BEGIN IMMEDIATE to serialise concurrent writes.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                states: dict[str, BudgetState] = {}
                violated: list[str] = []

                for key in keys:
                    row = cursor.execute(
                        "SELECT limit_tokens, used_tokens FROM budgets "
                        "WHERE key_dimension = ? AND key_identifier = ?",
                        (key.dimension, key.identifier),
                    ).fetchone()

                    if row is None:
                        states[key.dimension] = _build_state(key, 0, 0)
                        continue

                    limit, used = row[0], row[1]
                    if used + tokens > limit:
                        violated.append(key.dimension)
                    states[key.dimension] = _build_state(key, limit, used)

                if violated:
                    cursor.execute("ROLLBACK")
                    return CheckResult(allowed=False, states=states, violated=violated)

                now = _now_iso()
                for key in keys:
                    cursor.execute(
                        "UPDATE budgets SET used_tokens = used_tokens + ?, updated_at = ? "
                        "WHERE key_dimension = ? AND key_identifier = ?",
                        (tokens, now, key.dimension, key.identifier),
                    )

                cursor.execute("COMMIT")

                # Rebuild states with updated values
                for key in keys:
                    st = states[key.dimension]
                    states[key.dimension] = _build_state(
                        key, st.limit, st.used + tokens
                    )

                return CheckResult(allowed=True, states=states, violated=[])

            except Exception:
                cursor.execute("ROLLBACK")
                raise

    def force_increment(
        self,
        keys: list[BudgetKey],
        tokens: int,
    ) -> dict[str, BudgetState]:
        """Unconditional increment. Never rejects."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN")
            try:
                now = _now_iso()
                for key in keys:
                    cursor.execute(
                        "UPDATE budgets SET used_tokens = used_tokens + ?, updated_at = ? "
                        "WHERE key_dimension = ? AND key_identifier = ?",
                        (tokens, now, key.dimension, key.identifier),
                    )
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise

        return self.get_states(keys)

    def get_states(self, keys: list[BudgetKey]) -> dict[str, BudgetState]:
        """Non-atomic read of current state for a list of keys."""
        states: dict[str, BudgetState] = {}
        for key in keys:
            row = self._conn.execute(
                "SELECT limit_tokens, used_tokens FROM budgets "
                "WHERE key_dimension = ? AND key_identifier = ?",
                (key.dimension, key.identifier),
            ).fetchone()

            if row is None:
                states[key.dimension] = _build_state(key, 0, 0)
            else:
                states[key.dimension] = _build_state(key, row[0], row[1])

        return states

    def set_limit(self, key: BudgetKey, limit: int) -> None:
        """Register or update a budget limit for a key. Idempotent."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO budgets (key_dimension, key_identifier, limit_tokens, "
                "used_tokens, updated_at) VALUES (?, ?, ?, 0, ?) "
                "ON CONFLICT (key_dimension, key_identifier) "
                "DO UPDATE SET limit_tokens = excluded.limit_tokens, "
                "updated_at = excluded.updated_at",
                (key.dimension, key.identifier, limit, _now_iso()),
            )
            self._conn.commit()

    def reset(self, key: BudgetKey) -> None:
        """Reset used_tokens to zero and clear fired thresholds for this key."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN")
            try:
                now = _now_iso()
                cursor.execute(
                    "UPDATE budgets SET used_tokens = 0, updated_at = ? "
                    "WHERE key_dimension = ? AND key_identifier = ?",
                    (now, key.dimension, key.identifier),
                )
                cursor.execute(
                    "DELETE FROM fired_thresholds "
                    "WHERE key_dimension = ? AND key_identifier = ?",
                    (key.dimension, key.identifier),
                )
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    def is_threshold_fired(self, key: BudgetKey, at_pct: float) -> bool:
        """Return True if the threshold has already fired for this key."""
        row = self._conn.execute(
            "SELECT 1 FROM fired_thresholds "
            "WHERE key_dimension = ? AND key_identifier = ? AND at_pct = ?",
            (key.dimension, key.identifier, at_pct),
        ).fetchone()
        return row is not None

    def mark_threshold_fired(self, key: BudgetKey, at_pct: float) -> None:
        """Record that a threshold has fired for this key. Idempotent."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO fired_thresholds "
                "(key_dimension, key_identifier, at_pct, fired_at) "
                "VALUES (?, ?, ?, ?)",
                (key.dimension, key.identifier, at_pct, _now_iso()),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
