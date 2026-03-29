"""Integration tests for tokencap.patch() and tokencap.unpatch().

Full end-to-end: patch SDK constructors, make mocked HTTP calls, verify tracking.
HTTP layer mocked with pytest-httpx. No credentials required.
"""

from __future__ import annotations

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
from tokencap.core.exceptions import BudgetExceededError
from tokencap.interceptor.anthropic import GuardedAnthropic
from tokencap.interceptor.openai import GuardedOpenAI


@pytest.fixture(autouse=True)
def _cleanup() -> None:  # type: ignore[misc]
    """Ensure unpatch and teardown after each test."""
    yield  # type: ignore[misc]
    tokencap.unpatch()
    tokencap.teardown()


class TestPatchAnthropicEndToEnd:
    """patch() + Anthropic end-to-end."""

    def test_patch_anthropic_end_to_end(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """patch(limit=50_000), construct Anthropic, make call, verify tracking."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=30, output_tokens=15),
        )
        tokencap.patch(limit=50_000, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        assert isinstance(client, GuardedAnthropic)
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        status = tokencap.get_status()
        assert status.dimensions["session"].used > 0
        assert status.dimensions["session"].limit == 50_000

        # After unpatch, new clients are real Anthropic instances
        tokencap.unpatch()
        real_client = anthropic.Anthropic(api_key="sk-fake")
        assert not isinstance(real_client, GuardedAnthropic)


class TestPatchOpenAIEndToEnd:
    """patch() + OpenAI end-to-end."""

    def test_patch_openai_end_to_end(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """patch(limit=50_000), construct OpenAI, make call, verify tracking."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=20, completion_tokens=10),
        )
        tokencap.patch(limit=50_000, quiet=True)
        client = openai.OpenAI(api_key="sk-fake")
        assert isinstance(client, GuardedOpenAI)
        client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
        )
        status = tokencap.get_status()
        assert status.dimensions["session"].used > 0
        assert status.dimensions["session"].limit == 50_000

        # After unpatch, new clients are real OpenAI instances
        tokencap.unpatch()
        real_client = openai.OpenAI(api_key="sk-fake")
        assert not isinstance(real_client, GuardedOpenAI)


class TestPatchLimitEnforced:
    """patch(limit=1) blocks on budget exceeded."""

    def test_patch_limit_enforced(self) -> None:
        """With limit=1, the first call raises BudgetExceededError (estimate exceeds limit)."""
        tokencap.patch(limit=1, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        with pytest.raises(BudgetExceededError):
            # The estimate alone exceeds limit=1, so the first call should block
            client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=100,
                messages=[{"role": "user", "content": "Hi"}],
            )


class TestPatchWarnFires:
    """patch(policy=...) with WARN at 1%."""

    def test_patch_warn_fires(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """WARN callback fires when threshold is crossed."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        warned: list[bool] = []

        def on_warn(status: object) -> None:
            warned.append(True)

        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=100,
                thresholds=[make_threshold(at_pct=0.01, actions=[
                    make_action(kind="WARN", callback=on_warn),
                ])],
            ),
        })
        tokencap.patch(policy=policy, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert len(warned) == 1


class TestPatchDegradeTransparent:
    """patch(policy=...) with DEGRADE at 1%."""

    def test_patch_degrade_transparent(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """DEGRADE swaps model transparently, call succeeds, tokens tracked."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=10, output_tokens=5),
        )
        policy = make_policy(dimensions={
            "session": make_dimension_policy(
                limit=100,
                thresholds=[make_threshold(at_pct=0.01, actions=[
                    make_action(kind="DEGRADE", degrade_to="claude-haiku-4-5"),
                ])],
            ),
        })
        tokencap.patch(policy=policy, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert response.content[0].text == "test"
        status = tokencap.get_status()
        assert status.dimensions["session"].used > 0


class TestPatchGetStatusModuleLevel:
    """tokencap.get_status() works at module level in patch mode."""

    def test_patch_get_status_module_level(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """get_status() is module-level in patch mode — no wrapped client object needed."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=25, output_tokens=10),
        )
        tokencap.patch(limit=50_000, quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        # In patch mode, get_status() is called on the tokencap module,
        # not on the wrapped client object. There is no wrapped client to
        # call it on — the developer just uses anthropic.Anthropic() normally.
        status = tokencap.get_status()
        assert "session" in status.dimensions
        assert status.dimensions["session"].used > 0
        assert status.dimensions["session"].limit == 50_000
        assert status.dimensions["session"].pct_used > 0.0


class TestPatchUnpatchFullCycle:
    """patch → call → unpatch → patch again with different limit."""

    def test_patch_unpatch_full_cycle(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Second patch() session has fresh state with the new limit."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=20, output_tokens=10),
        )
        # First session
        tokencap.patch(limit=50_000, quiet=True)
        client1 = anthropic.Anthropic(api_key="sk-fake")
        client1.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        status1 = tokencap.get_status()
        assert status1.dimensions["session"].limit == 50_000
        assert status1.dimensions["session"].used > 0

        tokencap.unpatch()

        # Second session with different limit
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=15, output_tokens=8),
        )
        tokencap.patch(limit=100_000, quiet=True)
        client2 = anthropic.Anthropic(api_key="sk-fake")
        client2.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        status2 = tokencap.get_status()
        assert status2.dimensions["session"].limit == 100_000
        # Fresh state — usage comes only from the second call
        assert status2.dimensions["session"].used > 0


class TestPatchBothProvidersShareGuard:
    """Both providers share a single Guard when using patch()."""

    def test_patch_both_providers_share_guard(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Anthropic + OpenAI calls share one session dimension with combined usage."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=20, output_tokens=10),
        )
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=15, completion_tokens=8),
        )
        tokencap.patch(limit=100_000, quiet=True)

        anth_client = anthropic.Anthropic(api_key="sk-fake")
        anth_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        usage_after_anthropic = tokencap.get_status().dimensions["session"].used

        oai_client = openai.OpenAI(api_key="sk-fake")
        oai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
        )
        usage_after_both = tokencap.get_status().dimensions["session"].used

        # Both calls contribute to the same session dimension
        assert usage_after_both > usage_after_anthropic
        assert usage_after_anthropic > 0


class TestPatchProvidersAnthropicOnly:
    """patch(providers=["anthropic"]) only patches Anthropic."""

    def test_patch_providers_anthropic_only(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Only Anthropic is wrapped; OpenAI is left untouched."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=anthropic_response(input_tokens=20, output_tokens=10),
        )
        tokencap.patch(limit=50_000, quiet=True, providers=["anthropic"])
        anth = anthropic.Anthropic(api_key="sk-fake")
        assert isinstance(anth, GuardedAnthropic)
        anth.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": "Hi"}],
        )
        status = tokencap.get_status()
        assert status.dimensions["session"].used > 0

        oai = openai.OpenAI(api_key="sk-fake")
        assert not isinstance(oai, GuardedOpenAI)


class TestPatchProvidersOpenAIOnly:
    """patch(providers=["openai"]) only patches OpenAI."""

    def test_patch_providers_openai_only(
        self, httpx_mock: object  # type: ignore[type-arg]
    ) -> None:
        """Only OpenAI is wrapped; Anthropic is left untouched."""
        httpx_mock.add_response(  # type: ignore[union-attr]
            json=openai_response(prompt_tokens=20, completion_tokens=10),
        )
        tokencap.patch(limit=50_000, quiet=True, providers=["openai"])
        oai = openai.OpenAI(api_key="sk-fake")
        assert isinstance(oai, GuardedOpenAI)
        oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hi"}],
        )
        status = tokencap.get_status()
        assert status.dimensions["session"].used > 0

        anth = anthropic.Anthropic(api_key="sk-fake")
        assert not isinstance(anth, GuardedAnthropic)
