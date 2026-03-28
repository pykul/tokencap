"""Integration tests for the tokencap drop-in API.

Full end-to-end: tokencap.wrap(), all tiers, all action kinds.
HTTP layer mocked with pytest-httpx. No credentials required.
"""

from __future__ import annotations

import io
import sys
import time
from unittest.mock import patch

import anthropic
import openai
import pytest

import tokencap
from tests.conftest import (
    anthropic_response,
    make_action,
    make_dimension_policy,
    make_policy,
    make_threshold,
    openai_response,
)
from tokencap.core.exceptions import BudgetExceededError, ConfigurationError


@pytest.fixture(autouse=True)
def _teardown_after_each() -> None:  # type: ignore[misc]
    """Reset global Guard after each test."""
    yield  # type: ignore[misc]
    tokencap.teardown()


class TestTier1:
    """wrap(client) — tracking only."""

    def test_wrap_tracks_tokens(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Tokens tracked, no enforcement."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10, content="Hi"),
        )
        client = tokencap.wrap(anthropic.Anthropic(api_key="sk-fake"), quiet=True)
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.content[0].text == "Hi"
        status = client.get_status()
        assert status.dimensions["session"].used > 0


class TestTier2:
    """wrap(client, limit=N) — hard block."""

    def test_wrap_limit_blocks(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Blocks when limit exceeded."""
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), limit=1, quiet=True
        )
        with pytest.raises(BudgetExceededError):
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )


class TestTier3:
    """wrap(client, policy=...) — full policy control."""

    def test_wrap_policy_tracks(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Policy passed directly to wrap()."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=100000),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        status = client.get_status()
        assert status.dimensions["session"].used > 0

    def test_limit_and_policy_raises(self) -> None:
        """Passing both limit and policy raises ConfigurationError."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(limit=1000),
        })
        with pytest.raises(ConfigurationError, match="limit or policy, not both"):
            tokencap.wrap(
                anthropic.Anthropic(api_key="sk-fake"),
                limit=1000, policy=policy,
            )


class TestActions:
    """All four action kinds via the drop-in API."""

    def test_warn_fires_callback(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """WARN fires callback, call proceeds."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        warned: list[bool] = []

        def on_warn(status: object) -> None:
            warned.append(True)

        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=10,
                thresholds=[make_threshold(at_pct=0.5, actions=[
                    make_action(kind="WARN", callback=on_warn),
                ])],
            ),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert len(warned) == 1

    def test_block_raises(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """BLOCK raises BudgetExceededError."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=1,
                thresholds=[make_threshold(
                    at_pct=1.0, actions=[make_action(kind="BLOCK")],
                )],
            ),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        with pytest.raises(BudgetExceededError):
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )

    def test_degrade_swaps_model(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """DEGRADE swaps model transparently."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=10,
                thresholds=[make_threshold(at_pct=0.5, actions=[
                    make_action(kind="DEGRADE", degrade_to="claude-haiku-4-5"),
                ])],
            ),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )

    def test_webhook_fires(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """WEBHOOK fires in background thread."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=10,
                thresholds=[make_threshold(at_pct=0.5, actions=[
                    make_action(kind="WEBHOOK", webhook_url="http://example.com/hook"),
                ])],
            ),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )


class TestMultiDimension:
    """Multi-dimensional budget tests."""

    def test_block_on_one_dimension(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Two dimensions, one exceeded: BLOCK fires, both states in exception."""
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=1,
                thresholds=[make_threshold(
                    at_pct=1.0, actions=[make_action(kind="BLOCK")],
                )],
            ),
            "tenant": make_dimension_policy(limit=100000),
        })
        client = tokencap.wrap(
            anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
        )
        with pytest.raises(BudgetExceededError) as exc_info:
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )
        assert "session" in exc_info.value.check_result.violated


class TestStartupMessage:
    """Startup message tests."""

    def test_message_printed(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """wrap() prints startup message."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            tokencap.wrap(anthropic.Anthropic(api_key="sk-fake"))
        finally:
            sys.stdout = old_stdout
        assert "[tokencap]" in buf.getvalue()

    def test_quiet_suppresses(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """quiet=True suppresses startup message."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            tokencap.wrap(anthropic.Anthropic(api_key="sk-fake"), quiet=True)
        finally:
            sys.stdout = old_stdout
        assert buf.getvalue() == ""


class TestInitThenWrap:
    """Tests for the init() + wrap() code path."""

    def test_init_then_wrap_anthropic(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """init() + wrap() + messages.create() works end-to-end for Anthropic."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10, content="Hi"),
        )
        tokencap.init(
            policy=make_policy(dimensions={
                "session": make_dimension_policy(limit=100000),
            }),
            quiet=True,
        )
        client = tokencap.wrap(anthropic.Anthropic(api_key="sk-fake"))
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.content[0].text == "Hi"
        status = client.get_status()
        assert status.dimensions["session"].used > 0

    def test_init_then_wrap_openai(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """init() + wrap() + completions.create() works end-to-end for OpenAI."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=20, completion_tokens=5, content="Hi"),
        )
        tokencap.init(
            policy=make_policy(dimensions={
                "session": make_dimension_policy(limit=100000),
            }),
            quiet=True,
        )
        client = tokencap.wrap(openai.OpenAI(api_key="sk-fake"))
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.choices[0].message.content == "Hi"
        status = client.get_status()
        assert status.dimensions["session"].used > 0


class TestWebhookHTTPPost:
    """Tests that verify the WEBHOOK POST actually fires."""

    def test_webhook_fires_http_post_anthropic(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """WEBHOOK fires an HTTP POST for Anthropic calls."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=10,
                thresholds=[make_threshold(at_pct=0.5, actions=[
                    make_action(kind="WEBHOOK", webhook_url="http://test-hook.example.com/alert"),
                ])],
            ),
        })
        with patch("tokencap.interceptor.base.urllib.request.urlopen") as mock_urlopen:
            client = tokencap.wrap(
                anthropic.Anthropic(api_key="sk-fake"), policy=policy, quiet=True
            )
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )
            time.sleep(0.1)
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert "test-hook.example.com" in req.full_url

    def test_webhook_fires_http_post_openai(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """WEBHOOK fires an HTTP POST for OpenAI calls."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=10, completion_tokens=5),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=100,
                thresholds=[make_threshold(at_pct=0.1, actions=[
                    make_action(kind="WEBHOOK", webhook_url="http://test-hook.example.com/alert"),
                ])],
            ),
        })
        with patch("tokencap.interceptor.base.urllib.request.urlopen") as mock_urlopen:
            client = tokencap.wrap(
                openai.OpenAI(api_key="sk-fake"), policy=policy, quiet=True
            )
            client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hi"}],
            )
            time.sleep(0.1)
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            assert "test-hook.example.com" in req.full_url
