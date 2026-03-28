"""Tests for tokencap.patch() and tokencap.unpatch()."""

from __future__ import annotations

import io
import sys

import anthropic
import openai
import pytest

import tokencap
from tokencap.core.exceptions import ConfigurationError
from tokencap.interceptor.anthropic import GuardedAnthropic
from tokencap.interceptor.openai import GuardedOpenAI


@pytest.fixture(autouse=True)
def _cleanup() -> None:  # type: ignore[misc]
    """Ensure unpatch and teardown after each test."""
    yield  # type: ignore[misc]
    tokencap.unpatch()
    tokencap.teardown()


class TestPatch:
    """Tests for tokencap.patch()."""

    def test_patch_wraps_anthropic_clients(self) -> None:
        """After patch(), constructing Anthropic returns a GuardedAnthropic."""
        tokencap.patch(quiet=True)
        client = anthropic.Anthropic(api_key="sk-fake")
        assert isinstance(client, GuardedAnthropic)

    def test_patch_wraps_openai_clients(self) -> None:
        """After patch(), constructing OpenAI returns a GuardedOpenAI."""
        tokencap.patch(quiet=True)
        client = openai.OpenAI(api_key="sk-fake")
        assert isinstance(client, GuardedOpenAI)

    def test_patch_limit_and_policy_raises(self) -> None:
        """Passing both limit and policy raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="limit or policy, not both"):
            tokencap.patch(
                limit=1000,
                policy=tokencap.Policy(
                    dimensions={"session": tokencap.DimensionPolicy(limit=1000)}
                ),
            )

    def test_patch_already_patched_raises(self) -> None:
        """Calling patch() twice without unpatch() raises ConfigurationError."""
        tokencap.patch(quiet=True)
        with pytest.raises(ConfigurationError, match="already patched"):
            tokencap.patch(quiet=True)

    def test_patch_quiet_suppresses_message(self) -> None:
        """quiet=True suppresses the startup message."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            tokencap.patch(quiet=True)
        finally:
            sys.stdout = old_stdout
        assert buf.getvalue() == ""

    def test_patch_prints_message(self) -> None:
        """Default patch prints startup message."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            tokencap.patch()
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue()
        assert "[tokencap] patched:" in output
        assert "anthropic" in output


class TestUnpatch:
    """Tests for tokencap.unpatch()."""

    def test_unpatch_restores_original(self) -> None:
        """After unpatch(), constructing Anthropic returns a real Anthropic."""
        tokencap.patch(quiet=True)
        tokencap.unpatch()
        client = anthropic.Anthropic(api_key="sk-fake")
        assert not isinstance(client, GuardedAnthropic)

    def test_unpatch_when_not_patched_is_noop(self) -> None:
        """Calling unpatch() when not patched does not error."""
        tokencap.unpatch()  # Should not raise
