"""Shared test fixtures and factory functions for tokencap."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tokencap.backends.sqlite import SQLiteBackend
from tokencap.core.enums import ActionKind, ResetPeriod
from tokencap.core.guard import Guard
from tokencap.core.policy import Action, DimensionPolicy, Policy, Threshold
from tokencap.core.types import BudgetKey, BudgetState, CheckResult, TokenUsage

# ---------------------------------------------------------------------------
# Policy factory functions
# ---------------------------------------------------------------------------


def make_action(
    kind: ActionKind = ActionKind.WARN,
    webhook_url: str | None = None,
    degrade_to: str | None = None,
    callback: Any = None,
) -> Action:
    """Create an Action with overridable defaults."""
    return Action(kind=kind, webhook_url=webhook_url, degrade_to=degrade_to, callback=callback)


def make_threshold(
    at_pct: float = 0.8,
    actions: list[Action] | None = None,
) -> Threshold:
    """Create a Threshold with overridable defaults."""
    return Threshold(at_pct=at_pct, actions=actions or [make_action()])


def make_dimension_policy(
    limit: int = 10000,
    thresholds: list[Threshold] | None = None,
    reset_every: ResetPeriod | None = None,
) -> DimensionPolicy:
    """Create a DimensionPolicy with overridable defaults."""
    return DimensionPolicy(limit=limit, thresholds=thresholds or [], reset_every=reset_every)


def make_policy(
    dimensions: dict[str, DimensionPolicy] | None = None,
    name: str = "test",
) -> Policy:
    """Create a Policy with overridable defaults."""
    if dimensions is None:
        dimensions = {"session": make_dimension_policy()}
    return Policy(dimensions=dimensions, name=name)


# ---------------------------------------------------------------------------
# Response helpers for pytest-httpx
# ---------------------------------------------------------------------------


def anthropic_response(
    input_tokens: int = 50,
    output_tokens: int = 50,
    content: str = "test",
    model: str = "claude-sonnet-4-6",
    stop_reason: str = "end_turn",
) -> dict[str, object]:
    """Build a fake Anthropic Messages API JSON response."""
    return {
        "id": "msg_test123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def openai_response(
    prompt_tokens: int = 50,
    completion_tokens: int = 50,
    content: str = "test",
    model: str = "gpt-4o",
    finish_reason: str = "stop",
) -> dict[str, object]:
    """Build a fake OpenAI Chat Completions API JSON response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Phase 1 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test_tokencap.db")


@pytest.fixture
def sqlite_backend(tmp_db: str) -> SQLiteBackend:
    """Create a SQLiteBackend using a temporary database."""
    backend = SQLiteBackend(path=tmp_db)
    yield backend  # type: ignore[misc]  # mypy cannot infer return type of pytest yield fixtures
    backend.close()


@pytest.fixture
def sample_key() -> BudgetKey:
    """Return a sample BudgetKey. Override via parametrize if needed."""
    return BudgetKey(dimension="session", identifier="test-123")


# ---------------------------------------------------------------------------
# Phase 2 fixtures — all accept overrides
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_policy() -> Policy:
    """Policy with one session dimension: WARN at 80%, BLOCK at 100%."""
    return make_policy(dimensions={
        "session": make_dimension_policy(
            limit=10000,
            thresholds=[
                make_threshold(at_pct=0.8, actions=[make_action(kind="WARN")]),
                make_threshold(at_pct=1.0, actions=[make_action(kind="BLOCK")]),
            ],
        ),
    })


@pytest.fixture
def mock_provider() -> MagicMock:
    """Mock provider with overridable defaults."""
    provider = MagicMock()
    provider.estimate_tokens.return_value = 100
    provider.extract_usage.return_value = TokenUsage(input_tokens=50, output_tokens=50)
    provider.get_model.return_value = "claude-sonnet-4-6"
    return provider


@pytest.fixture
def mock_backend() -> MagicMock:
    """Mock backend with overridable defaults."""
    backend = MagicMock()
    key = BudgetKey(dimension="session", identifier="test-id")
    state = BudgetState(
        key=key, limit=10000, used=100, remaining=9900, pct_used=0.01
    )
    backend.check_and_increment.return_value = CheckResult(
        allowed=True, states={"session": state}, violated=[]
    )
    backend.force_increment.return_value = {"session": state}
    backend.get_states.return_value = {"session": state}
    backend.is_threshold_fired.return_value = False
    return backend


@pytest.fixture
def stub_guard(
    sample_policy: Policy,
    mock_backend: MagicMock,
) -> Guard:
    """Guard wired to mock backend.

    Guard is a stateless config holder — it does not store provider.
    Tests that need a provider should set it on the wrapped client directly
    or pass it to call()/call_async()/call_stream() explicitly.
    """
    return Guard(
        policy=sample_policy,
        identifiers={"session": "test-id"},
        backend=mock_backend,
        quiet=True,
    )
