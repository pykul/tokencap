#!/usr/bin/env python3
"""tokencap smoke test — exercises every user-facing behavior with real API calls.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export OPENAI_API_KEY=sk-...
    python scripts/smoke_test.py

Both API keys are required. The script exits immediately if either is missing.

To skip a provider, comment out its section runner in main(). Each section
is independent and clearly marked with a banner comment.

This is NOT part of any test suite or CI pipeline. It is a manual verification
script that makes real API calls and costs real money (very little — all calls
use the cheapest models with max_tokens=10).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def _run(name: str, fn: Any) -> None:
    """Run a single test function and record the result."""
    sys.stdout.write(f"  Running {name}... ")
    sys.stdout.flush()
    try:
        ok, msg = fn()
    except Exception as exc:
        ok, msg = False, f"unhandled exception: {exc}"
    _results.append((name, ok, msg))
    if ok:
        print("PASS")
    else:
        print(f"FAIL: {msg}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = "claude-haiku-4-5"
OPENAI_MODEL = "gpt-4o-mini"
SMALL_MESSAGES_ANTHROPIC: list[dict[str, str]] = [
    {"role": "user", "content": "hi"},
]
SMALL_MESSAGES_OPENAI: list[dict[str, str]] = [
    {"role": "user", "content": "hi"},
]


def _ensure_clean() -> None:
    """Force-clean global tokencap state. Safe to call multiple times."""
    import tokencap
    try:
        tokencap.unpatch()
    except Exception:
        pass
    try:
        tokencap.teardown()
    except Exception:
        pass


# ===================================================================
# SECTION 0: Preamble — no API calls needed
# ===================================================================

def test_api_keys_present() -> tuple[bool, str]:
    """Fail fast if API keys are missing."""
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if missing:
        return False, f"missing env vars: {', '.join(missing)}"
    return True, ""


def test_threshold_rejects_zero() -> tuple[bool, str]:
    """Threshold(at_pct=0.0) raises ValueError."""
    import tokencap
    try:
        tokencap.Threshold(at_pct=0.0, actions=[])
        return False, "no ValueError raised"
    except ValueError:
        return True, ""


def test_threshold_rejects_above_one() -> tuple[bool, str]:
    """Threshold(at_pct=1.5) raises ValueError."""
    import tokencap
    try:
        tokencap.Threshold(at_pct=1.5, actions=[])
        return False, "no ValueError raised"
    except ValueError:
        return True, ""


def test_wrap_limit_and_policy_raises() -> tuple[bool, str]:
    """wrap(limit=, policy=) raises ConfigurationError."""
    import anthropic
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    try:
        tokencap.wrap(
            anthropic.Anthropic(),
            limit=1000,
            policy=tokencap.Policy(
                dimensions={"session": tokencap.DimensionPolicy(limit=1000)}
            ),
        )
        return False, "no ConfigurationError raised"
    except ConfigurationError:
        return True, ""
    finally:
        _ensure_clean()


def test_patch_limit_and_policy_raises() -> tuple[bool, str]:
    """patch(limit=, policy=) raises ConfigurationError."""
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    try:
        tokencap.patch(
            limit=1000,
            policy=tokencap.Policy(
                dimensions={"session": tokencap.DimensionPolicy(limit=1000)}
            ),
        )
        return False, "no ConfigurationError raised"
    except ConfigurationError:
        return True, ""
    finally:
        _ensure_clean()


def test_get_status_before_guard_raises() -> tuple[bool, str]:
    """get_status() without any Guard raises ConfigurationError."""
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    _ensure_clean()
    try:
        tokencap.get_status()
        return False, "no ConfigurationError raised"
    except ConfigurationError:
        return True, ""


def test_unpatch_when_not_patched_is_noop() -> tuple[bool, str]:
    """unpatch() when not patched does not raise."""
    import tokencap
    _ensure_clean()
    try:
        tokencap.unpatch()
        return True, ""
    except Exception as exc:
        return False, f"raised {exc}"


# ===================================================================
# SECTION 1: WRAP MODE — Anthropic
#
# Tests wrap() with anthropic.Anthropic(). Comment out the
# run_section_1() call in main() to skip Anthropic wrap tests.
# ===================================================================

def test_wrap_anthropic_tracking_only() -> tuple[bool, str]:
    """wrap(client) — tracking only, no enforcement."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), quiet=True)
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response"
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}, expected > 0"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_limit_blocks() -> tuple[bool, str]:
    """wrap(client, limit=1) — BudgetExceededError raised."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), limit=1, quiet=True)
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_full_policy_warn() -> tuple[bool, str]:
    """wrap(client, policy=) with WARN at 1% — callback fires."""
    import anthropic
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not warned:
            return False, "WARN callback not called"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_block_action() -> tuple[bool, str]:
    """BLOCK at 100% with limit=1 — raises BudgetExceededError."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1,
                thresholds=[tokencap.Threshold(
                    at_pct=1.0, actions=[tokencap.Action(kind=tokencap.ActionKind.BLOCK)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_degrade() -> tuple[bool, str]:
    """DEGRADE at 1% — call succeeds with degraded model."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.DEGRADE, degrade_to=ANTHROPIC_MODEL,
                    )],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response after DEGRADE"
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after DEGRADE"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_webhook() -> tuple[bool, str]:
    """WEBHOOK at 1% — fires HTTP POST to httpbin, does not block."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.WEBHOOK,
                        webhook_url="https://httpbin.org/post",
                    )],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        time.sleep(2)
        if not response.content[0].text:
            return False, "empty response"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_client_get_status() -> tuple[bool, str]:
    """client.get_status() returns correct StatusResponse."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), limit=50_000, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = client.get_status()
        dim = status.dimensions.get("session")
        if dim is None:
            return False, "no 'session' dimension in status"
        if dim.used <= 0:
            return False, f"used={dim.used}"
        if dim.limit != 50_000:
            return False, f"limit={dim.limit}, expected 50000"
        if dim.pct_used <= 0.0:
            return False, f"pct_used={dim.pct_used}"
        if dim.remaining >= 50_000:
            return False, f"remaining={dim.remaining}, expected < 50000"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_module_get_status() -> tuple[bool, str]:
    """tokencap.get_status() returns same data as client.get_status()."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), limit=50_000, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        module_status = tokencap.get_status()
        client_status = client.get_status()
        if module_status.dimensions["session"].used != client_status.dimensions["session"].used:
            return False, "module and client get_status() disagree"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_multi_dimension() -> tuple[bool, str]:
    """Two dimensions in one policy, both tracked independently."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(limit=1_000_000),
            "tenant": tokencap.DimensionPolicy(limit=5_000_000),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = client.get_status()
        if "session" not in status.dimensions:
            return False, "missing 'session' dimension"
        if "tenant" not in status.dimensions:
            return False, "missing 'tenant' dimension"
        if status.dimensions["session"].used <= 0:
            return False, "session used=0"
        if status.dimensions["tenant"].used <= 0:
            return False, "tenant used=0"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_async() -> tuple[bool, str]:
    """wrap(AsyncAnthropic()) — async client tracked."""
    import anthropic
    import tokencap

    async def _inner() -> tuple[bool, str]:
        try:
            client = tokencap.wrap(anthropic.AsyncAnthropic(), quiet=True)
            response = await client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            if not response.content[0].text:
                return False, "empty response"
            status = client.get_status()
            if status.dimensions["session"].used <= 0:
                return False, f"used={status.dimensions['session'].used}"
            return True, ""
        finally:
            _ensure_clean()

    return asyncio.run(_inner())


def test_wrap_anthropic_with_options() -> tuple[bool, str]:
    """with_options() returns a wrapped client."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        client = tokencap.wrap(anthropic.Anthropic(), quiet=True)
        opts_client = client.with_options(timeout=30.0)
        if not isinstance(opts_client, GuardedAnthropic):
            return False, f"got {type(opts_client).__name__}, expected GuardedAnthropic"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_with_raw_response() -> tuple[bool, str]:
    """with_raw_response returns a wrapped client."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        client = tokencap.wrap(anthropic.Anthropic(), quiet=True)
        raw_client = client.with_raw_response
        if not isinstance(raw_client, GuardedAnthropic):
            return False, f"got {type(raw_client).__name__}, expected GuardedAnthropic"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_with_streaming_response() -> tuple[bool, str]:
    """with_streaming_response returns a wrapped client."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        client = tokencap.wrap(anthropic.Anthropic(), quiet=True)
        stream_client = client.with_streaming_response
        if not isinstance(stream_client, GuardedAnthropic):
            return False, f"got {type(stream_client).__name__}, expected GuardedAnthropic"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_streaming() -> tuple[bool, str]:
    """messages.stream() returns GuardedStream, tokens reconciled."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        chunks: list[str] = []
        with client.messages.stream(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
        if not chunks:
            return False, "no chunks received"
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after stream"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_quiet() -> tuple[bool, str]:
    """quiet=True suppresses stdout."""
    import io
    import anthropic
    import tokencap
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        tokencap.wrap(anthropic.Anthropic(), quiet=True)
    finally:
        sys.stdout = old
        _ensure_clean()
    if buf.getvalue():
        return False, f"stdout not empty: {buf.getvalue()!r}"
    return True, ""


def test_wrap_anthropic_teardown_rewrap() -> tuple[bool, str]:
    """teardown() then re-wrap starts a fresh session."""
    import anthropic
    import tokencap
    try:
        client1 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        tokencap.teardown()
        client2 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        status2 = tokencap.get_status()
        if status2.dimensions["session"].used != 0:
            return False, f"second session used={status2.dimensions['session'].used}, expected 0"
        return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# SECTION 2: WRAP MODE — OpenAI
#
# Tests wrap() with openai.OpenAI(). Comment out the
# run_section_2() call in main() to skip OpenAI wrap tests.
# ===================================================================

def test_wrap_openai_tracking_only() -> tuple[bool, str]:
    """wrap(client) — tracking only, no enforcement."""
    import openai
    import tokencap
    try:
        client = tokencap.wrap(openai.OpenAI(), quiet=True)
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response"
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_limit_blocks() -> tuple[bool, str]:
    """wrap(client, limit=1) — BudgetExceededError raised."""
    import openai
    import tokencap
    try:
        client = tokencap.wrap(openai.OpenAI(), limit=1, quiet=True)
        try:
            client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_full_policy_warn() -> tuple[bool, str]:
    """wrap(client, policy=) with WARN at 1% — callback fires."""
    import openai
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not warned:
            return False, "WARN callback not called"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_block_action() -> tuple[bool, str]:
    """BLOCK at 100% with limit=1 — raises BudgetExceededError."""
    import openai
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1,
                thresholds=[tokencap.Threshold(
                    at_pct=1.0, actions=[tokencap.Action(kind=tokencap.ActionKind.BLOCK)],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        try:
            client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_degrade() -> tuple[bool, str]:
    """DEGRADE at 1% — call succeeds with degraded model."""
    import openai
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.DEGRADE, degrade_to=OPENAI_MODEL,
                    )],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        response = client.chat.completions.create(
            model="gpt-4o", max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response after DEGRADE"
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after DEGRADE"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_webhook() -> tuple[bool, str]:
    """WEBHOOK at 1% — fires HTTP POST to httpbin, does not block."""
    import openai
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.WEBHOOK,
                        webhook_url="https://httpbin.org/post",
                    )],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        time.sleep(2)
        if not response.choices[0].message.content:
            return False, "empty response"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_client_get_status() -> tuple[bool, str]:
    """client.get_status() returns correct StatusResponse."""
    import openai
    import tokencap
    try:
        client = tokencap.wrap(openai.OpenAI(), limit=50_000, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = client.get_status()
        dim = status.dimensions.get("session")
        if dim is None:
            return False, "no 'session' dimension"
        if dim.used <= 0:
            return False, f"used={dim.used}"
        if dim.limit != 50_000:
            return False, f"limit={dim.limit}"
        if dim.pct_used <= 0.0:
            return False, f"pct_used={dim.pct_used}"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_module_get_status() -> tuple[bool, str]:
    """tokencap.get_status() returns same data as client.get_status()."""
    import openai
    import tokencap
    try:
        client = tokencap.wrap(openai.OpenAI(), limit=50_000, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        mod = tokencap.get_status()
        cli = client.get_status()
        if mod.dimensions["session"].used != cli.dimensions["session"].used:
            return False, "module and client get_status() disagree"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_multi_dimension() -> tuple[bool, str]:
    """Two dimensions in one policy, both tracked independently."""
    import openai
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(limit=1_000_000),
            "tenant": tokencap.DimensionPolicy(limit=5_000_000),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = client.get_status()
        if "session" not in status.dimensions:
            return False, "missing 'session' dimension"
        if "tenant" not in status.dimensions:
            return False, "missing 'tenant' dimension"
        if status.dimensions["session"].used <= 0:
            return False, "session used=0"
        if status.dimensions["tenant"].used <= 0:
            return False, "tenant used=0"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_async() -> tuple[bool, str]:
    """wrap(AsyncOpenAI()) — async client tracked."""
    import openai
    import tokencap

    async def _inner() -> tuple[bool, str]:
        try:
            client = tokencap.wrap(openai.AsyncOpenAI(), quiet=True)
            response = await client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_OPENAI,
            )
            if not response.choices[0].message.content:
                return False, "empty response"
            status = client.get_status()
            if status.dimensions["session"].used <= 0:
                return False, f"used={status.dimensions['session'].used}"
            return True, ""
        finally:
            _ensure_clean()

    return asyncio.run(_inner())


def test_wrap_openai_with_options() -> tuple[bool, str]:
    """with_options() returns a wrapped client."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        client = tokencap.wrap(openai.OpenAI(), quiet=True)
        opts_client = client.with_options(timeout=30.0)
        if not isinstance(opts_client, GuardedOpenAI):
            return False, f"got {type(opts_client).__name__}, expected GuardedOpenAI"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_with_raw_response() -> tuple[bool, str]:
    """with_raw_response returns a wrapped client."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        client = tokencap.wrap(openai.OpenAI(), quiet=True)
        raw_client = client.with_raw_response
        if not isinstance(raw_client, GuardedOpenAI):
            return False, f"got {type(raw_client).__name__}, expected GuardedOpenAI"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_with_streaming_response() -> tuple[bool, str]:
    """with_streaming_response returns a wrapped client."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        client = tokencap.wrap(openai.OpenAI(), quiet=True)
        stream_client = client.with_streaming_response
        if not isinstance(stream_client, GuardedOpenAI):
            return False, f"got {type(stream_client).__name__}, expected GuardedOpenAI"
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_quiet() -> tuple[bool, str]:
    """quiet=True suppresses stdout."""
    import io
    import openai
    import tokencap
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        tokencap.wrap(openai.OpenAI(), quiet=True)
    finally:
        sys.stdout = old
        _ensure_clean()
    if buf.getvalue():
        return False, f"stdout not empty: {buf.getvalue()!r}"
    return True, ""


def test_wrap_openai_teardown_rewrap() -> tuple[bool, str]:
    """teardown() then re-wrap starts a fresh session."""
    import openai
    import tokencap
    try:
        client1 = tokencap.wrap(openai.OpenAI(), limit=1_000_000, quiet=True)
        client1.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        tokencap.teardown()
        client2 = tokencap.wrap(openai.OpenAI(), limit=1_000_000, quiet=True)
        status2 = tokencap.get_status()
        if status2.dimensions["session"].used != 0:
            return False, f"second session used={status2.dimensions['session'].used}, expected 0"
        return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# SECTION 3: PATCH MODE — Anthropic
#
# Tests patch() with anthropic.Anthropic(). Comment out the
# run_section_3() call in main() to skip Anthropic patch tests.
# ===================================================================

def test_patch_anthropic_wraps() -> tuple[bool, str]:
    """patch(providers=[tokencap.Provider.ANTHROPIC]) wraps Anthropic constructors."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        if not isinstance(client, GuardedAnthropic):
            return False, f"got {type(client).__name__}, expected GuardedAnthropic"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_tracking() -> tuple[bool, str]:
    """patch(providers=[tokencap.Provider.ANTHROPIC]) + make call + verify get_status()."""
    import anthropic
    import tokencap
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        if status.dimensions["session"].limit != 50_000:
            return False, f"limit={status.dimensions['session'].limit}"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_limit_blocks() -> tuple[bool, str]:
    """patch(limit=1, providers=[tokencap.Provider.ANTHROPIC]) — BudgetExceededError raised."""
    import anthropic
    import tokencap
    try:
        tokencap.patch(limit=1, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_warn() -> tuple[bool, str]:
    """patch(policy=) with WARN at 1% — callback fires."""
    import anthropic
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not warned:
            return False, "WARN callback not called"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_degrade() -> tuple[bool, str]:
    """patch(policy=, providers=[tokencap.Provider.ANTHROPIC]) with DEGRADE at 1% — call succeeds."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.DEGRADE, degrade_to=ANTHROPIC_MODEL,
                    )],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_unpatch_restores() -> tuple[bool, str]:
    """unpatch() restores original Anthropic constructor."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        tokencap.unpatch()
        client = anthropic.Anthropic()
        if isinstance(client, GuardedAnthropic):
            return False, "still GuardedAnthropic after unpatch"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_double_raises() -> tuple[bool, str]:
    """patch() twice without unpatch() raises ConfigurationError."""
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        try:
            tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
            return False, "no ConfigurationError raised"
        except ConfigurationError:
            return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_get_status_module_level() -> tuple[bool, str]:
    """tokencap.get_status() works in patch mode."""
    import anthropic
    import tokencap
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        # In patch mode, status is module-level — no client.get_status()
        status = tokencap.get_status()
        if "session" not in status.dimensions:
            return False, "no session dimension"
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_unpatch_clears_guard() -> tuple[bool, str]:
    """unpatch() calls teardown() — get_status() raises after."""
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        tokencap.unpatch()
        try:
            tokencap.get_status()
            return False, "no ConfigurationError after unpatch"
        except ConfigurationError:
            return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# SECTION 4: PATCH MODE — OpenAI
#
# Tests patch() with openai.OpenAI(). Comment out the
# run_section_4() call in main() to skip OpenAI patch tests.
# ===================================================================

def test_patch_openai_wraps() -> tuple[bool, str]:
    """patch(providers=[tokencap.Provider.OPENAI]) wraps OpenAI constructors."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        if not isinstance(client, GuardedOpenAI):
            return False, f"got {type(client).__name__}, expected GuardedOpenAI"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_tracking() -> tuple[bool, str]:
    """patch(providers=[tokencap.Provider.OPENAI]) + make call + verify get_status()."""
    import openai
    import tokencap
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        if status.dimensions["session"].limit != 50_000:
            return False, f"limit={status.dimensions['session'].limit}"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_limit_blocks() -> tuple[bool, str]:
    """patch(limit=1, providers=[tokencap.Provider.OPENAI]) — BudgetExceededError raised."""
    import openai
    import tokencap
    try:
        tokencap.patch(limit=1, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        try:
            client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_warn() -> tuple[bool, str]:
    """patch(policy=) with WARN at 1% — callback fires."""
    import openai
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not warned:
            return False, "WARN callback not called"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_degrade() -> tuple[bool, str]:
    """patch(policy=, providers=[tokencap.Provider.OPENAI]) with DEGRADE at 1% — call succeeds."""
    import openai
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.DEGRADE, degrade_to=OPENAI_MODEL,
                    )],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o", max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_unpatch_restores() -> tuple[bool, str]:
    """unpatch() restores original OpenAI constructor."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.OPENAI])
        tokencap.unpatch()
        client = openai.OpenAI()
        if isinstance(client, GuardedOpenAI):
            return False, "still GuardedOpenAI after unpatch"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_both_providers_share_guard() -> tuple[bool, str]:
    """patch() — both Anthropic and OpenAI share one session budget."""
    import anthropic
    import openai
    import tokencap
    try:
        tokencap.patch(limit=1_000_000, quiet=True)
        anth = anthropic.Anthropic()
        anth.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used_after_anth = tokencap.get_status().dimensions["session"].used

        oai = openai.OpenAI()
        oai.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used_after_both = tokencap.get_status().dimensions["session"].used

        if used_after_anth <= 0:
            return False, "no Anthropic usage"
        if used_after_both <= used_after_anth:
            return False, f"OpenAI did not add: {used_after_both} <= {used_after_anth}"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_init_then_patch() -> tuple[bool, str]:
    """init() + patch() — pre-configured identifiers used."""
    import anthropic
    import tokencap
    try:
        tokencap.init(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            identifiers={"session": "smoke-test-custom-id"},
            quiet=True,
        )
        tokencap.patch(quiet=True)
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# SECTION 5: EXPLICIT GUARD MODE
#
# Tests Guard() directly. Comment out run_section_5() to skip.
# ===================================================================

def test_guard_single_anthropic() -> tuple[bool, str]:
    """Guard + wrap_anthropic() — single provider."""
    import anthropic
    import tokencap
    try:
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        client = guard.wrap_anthropic(anthropic.Anthropic())
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_single_openai() -> tuple[bool, str]:
    """Guard + wrap_openai() — single provider."""
    import openai
    import tokencap
    try:
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        client = guard.wrap_openai(openai.OpenAI())
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_both_providers_shared() -> tuple[bool, str]:
    """Both providers wrapped against the same Guard share budget."""
    import anthropic
    import openai
    import tokencap
    try:
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        anth = guard.wrap_anthropic(anthropic.Anthropic())
        oai = guard.wrap_openai(openai.OpenAI())

        anth.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = guard.get_status().dimensions["session"].used

        oai.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used2 = guard.get_status().dimensions["session"].used

        if used1 <= 0:
            return False, "Anthropic added 0"
        if used2 <= used1:
            return False, f"OpenAI did not add: {used2} <= {used1}"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_custom_backend() -> tuple[bool, str]:
    """Guard with custom SQLiteBackend path."""
    import tempfile
    import anthropic
    import tokencap
    from tokencap.backends.sqlite import SQLiteBackend
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "smoke.db")
    try:
        backend = SQLiteBackend(path=db_path)
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            backend=backend,
            quiet=True,
        )
        client = guard.wrap_anthropic(anthropic.Anthropic())
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not os.path.exists(db_path):
            return False, "database file not created"
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_custom_identifiers() -> tuple[bool, str]:
    """Guard with custom identifiers."""
    import anthropic
    import tokencap
    try:
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            identifiers={"session": "custom-smoke-id"},
            quiet=True,
        )
        client = guard.wrap_anthropic(anthropic.Anthropic())
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = guard.get_status()
        key = status.dimensions["session"].key
        if key.identifier != "custom-smoke-id":
            return False, f"identifier={key.identifier}"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_auto_uuid() -> tuple[bool, str]:
    """Guard without explicit identifiers generates UUID."""
    import tokencap
    import uuid as uuid_mod
    try:
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        ident = guard.identifiers["session"]
        try:
            uuid_mod.UUID(ident)
        except ValueError:
            return False, f"identifier {ident!r} is not a valid UUID"
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


# ===================================================================
# SECTION 6: EDGE CASES
#
# Behavioral edge cases. Comment out run_section_6() to skip.
# ===================================================================

def test_warn_fires_once() -> tuple[bool, str]:
    """WARN fires exactly once even when threshold crossed multiple times."""
    import anthropic
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        # Two calls — both cross the 0.01 threshold
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if len(warned) != 1:
            return False, f"WARN fired {len(warned)} times, expected 1"
        return True, ""
    finally:
        _ensure_clean()


def test_block_fires_every_call() -> tuple[bool, str]:
    """BLOCK fires on every call after threshold, not just the first."""
    import anthropic
    import tokencap
    try:
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1,
                thresholds=[tokencap.Threshold(
                    at_pct=1.0, actions=[tokencap.Action(kind=tokencap.ActionKind.BLOCK)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        blocked_count = 0
        for _ in range(2):
            try:
                client.messages.create(
                    model=ANTHROPIC_MODEL, max_tokens=10,
                    messages=SMALL_MESSAGES_ANTHROPIC,
                )
            except tokencap.BudgetExceededError:
                blocked_count += 1
        if blocked_count != 2:
            return False, f"blocked {blocked_count} times, expected 2"
        return True, ""
    finally:
        _ensure_clean()


def test_budget_exceeded_carries_check_result() -> tuple[bool, str]:
    """BudgetExceededError has check_result with violated dims and states."""
    import anthropic
    import tokencap
    try:
        client = tokencap.wrap(anthropic.Anthropic(), limit=1, quiet=True)
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=10,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError as exc:
            cr = exc.check_result
            if "session" not in cr.violated:
                return False, f"violated={cr.violated}"
            if "session" not in cr.states:
                return False, "no 'session' in states"
            state = cr.states["session"]
            if state.limit != 1:
                return False, f"state.limit={state.limit}"
            return True, ""
    finally:
        _ensure_clean()


def test_teardown_rewrap_fresh() -> tuple[bool, str]:
    """teardown() + re-wrap gives fresh state (used=0)."""
    import anthropic
    import tokencap
    try:
        client1 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        tokencap.teardown()

        # Second session — fresh Guard, fresh backend
        client2 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        used2 = tokencap.get_status().dimensions["session"].used
        if used2 != 0:
            return False, f"second session used={used2}, expected 0"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_unpatch_repatch_cycle() -> tuple[bool, str]:
    """patch → call → unpatch → patch(new limit) → call → fresh state."""
    import anthropic
    import tokencap
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client1 = anthropic.Anthropic()
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status1 = tokencap.get_status()
        if status1.dimensions["session"].limit != 50_000:
            return False, f"first limit={status1.dimensions['session'].limit}"
        if status1.dimensions["session"].used <= 0:
            return False, "first used=0"

        tokencap.unpatch()

        tokencap.patch(limit=100_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client2 = anthropic.Anthropic()
        client2.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=10,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status2 = tokencap.get_status()
        if status2.dimensions["session"].limit != 100_000:
            return False, f"second limit={status2.dimensions['session'].limit}"
        if status2.dimensions["session"].used <= 0:
            return False, "second used=0"
        return True, ""
    finally:
        _ensure_clean()


def test_unsupported_client_raises() -> tuple[bool, str]:
    """wrap('not a client') raises ConfigurationError."""
    import tokencap
    from tokencap.core.exceptions import ConfigurationError
    try:
        tokencap.wrap("not a client", quiet=True)
        return False, "no ConfigurationError raised"
    except ConfigurationError:
        return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# Section runners
# ===================================================================

def run_section_0() -> None:
    """Preamble — no API calls."""
    print("\n=== SECTION 0: Preamble (no API calls) ===")
    _run("test_api_keys_present", test_api_keys_present)
    # If keys missing, abort early
    if not _results[-1][1]:
        print("\nAborting: API keys required for remaining tests.")
        sys.exit(1)
    _run("test_threshold_rejects_zero", test_threshold_rejects_zero)
    _run("test_threshold_rejects_above_one", test_threshold_rejects_above_one)
    _run("test_wrap_limit_and_policy_raises", test_wrap_limit_and_policy_raises)
    _run("test_patch_limit_and_policy_raises", test_patch_limit_and_policy_raises)
    _run("test_get_status_before_guard_raises", test_get_status_before_guard_raises)
    _run("test_unpatch_when_not_patched_is_noop", test_unpatch_when_not_patched_is_noop)


def run_section_1() -> None:
    """WRAP MODE — Anthropic."""
    print("\n=== SECTION 1: Wrap Mode — Anthropic ===")
    _run("test_wrap_anthropic_tracking_only", test_wrap_anthropic_tracking_only)
    _run("test_wrap_anthropic_limit_blocks", test_wrap_anthropic_limit_blocks)
    _run("test_wrap_anthropic_full_policy_warn", test_wrap_anthropic_full_policy_warn)
    _run("test_wrap_anthropic_block_action", test_wrap_anthropic_block_action)
    _run("test_wrap_anthropic_degrade", test_wrap_anthropic_degrade)
    _run("test_wrap_anthropic_webhook", test_wrap_anthropic_webhook)
    _run("test_wrap_anthropic_client_get_status", test_wrap_anthropic_client_get_status)
    _run("test_wrap_anthropic_module_get_status", test_wrap_anthropic_module_get_status)
    _run("test_wrap_anthropic_multi_dimension", test_wrap_anthropic_multi_dimension)
    _run("test_wrap_anthropic_async", test_wrap_anthropic_async)
    _run("test_wrap_anthropic_with_options", test_wrap_anthropic_with_options)
    _run("test_wrap_anthropic_with_raw_response", test_wrap_anthropic_with_raw_response)
    _run("test_wrap_anthropic_with_streaming_response", test_wrap_anthropic_with_streaming_response)
    _run("test_wrap_anthropic_streaming", test_wrap_anthropic_streaming)
    _run("test_wrap_anthropic_quiet", test_wrap_anthropic_quiet)
    _run("test_wrap_anthropic_teardown_rewrap", test_wrap_anthropic_teardown_rewrap)


def run_section_2() -> None:
    """WRAP MODE — OpenAI."""
    print("\n=== SECTION 2: Wrap Mode — OpenAI ===")
    _run("test_wrap_openai_tracking_only", test_wrap_openai_tracking_only)
    _run("test_wrap_openai_limit_blocks", test_wrap_openai_limit_blocks)
    _run("test_wrap_openai_full_policy_warn", test_wrap_openai_full_policy_warn)
    _run("test_wrap_openai_block_action", test_wrap_openai_block_action)
    _run("test_wrap_openai_degrade", test_wrap_openai_degrade)
    _run("test_wrap_openai_webhook", test_wrap_openai_webhook)
    _run("test_wrap_openai_client_get_status", test_wrap_openai_client_get_status)
    _run("test_wrap_openai_module_get_status", test_wrap_openai_module_get_status)
    _run("test_wrap_openai_multi_dimension", test_wrap_openai_multi_dimension)
    _run("test_wrap_openai_async", test_wrap_openai_async)
    _run("test_wrap_openai_with_options", test_wrap_openai_with_options)
    _run("test_wrap_openai_with_raw_response", test_wrap_openai_with_raw_response)
    _run("test_wrap_openai_with_streaming_response", test_wrap_openai_with_streaming_response)
    _run("test_wrap_openai_quiet", test_wrap_openai_quiet)
    _run("test_wrap_openai_teardown_rewrap", test_wrap_openai_teardown_rewrap)


def run_section_3() -> None:
    """PATCH MODE — Anthropic."""
    print("\n=== SECTION 3: Patch Mode — Anthropic ===")
    _run("test_patch_anthropic_wraps", test_patch_anthropic_wraps)
    _run("test_patch_anthropic_tracking", test_patch_anthropic_tracking)
    _run("test_patch_anthropic_limit_blocks", test_patch_anthropic_limit_blocks)
    _run("test_patch_anthropic_warn", test_patch_anthropic_warn)
    _run("test_patch_anthropic_degrade", test_patch_anthropic_degrade)
    _run("test_patch_anthropic_unpatch_restores", test_patch_anthropic_unpatch_restores)
    _run("test_patch_anthropic_double_raises", test_patch_anthropic_double_raises)
    _run("test_patch_anthropic_get_status_module_level", test_patch_anthropic_get_status_module_level)
    _run("test_patch_anthropic_unpatch_clears_guard", test_patch_anthropic_unpatch_clears_guard)


def run_section_4() -> None:
    """PATCH MODE — OpenAI."""
    print("\n=== SECTION 4: Patch Mode — OpenAI ===")
    _run("test_patch_openai_wraps", test_patch_openai_wraps)
    _run("test_patch_openai_tracking", test_patch_openai_tracking)
    _run("test_patch_openai_limit_blocks", test_patch_openai_limit_blocks)
    _run("test_patch_openai_warn", test_patch_openai_warn)
    _run("test_patch_openai_degrade", test_patch_openai_degrade)
    _run("test_patch_openai_unpatch_restores", test_patch_openai_unpatch_restores)
    _run("test_patch_both_providers_share_guard", test_patch_both_providers_share_guard)
    _run("test_patch_init_then_patch", test_patch_init_then_patch)


def run_section_5() -> None:
    """EXPLICIT GUARD MODE."""
    print("\n=== SECTION 5: Explicit Guard Mode ===")
    _run("test_guard_single_anthropic", test_guard_single_anthropic)
    _run("test_guard_single_openai", test_guard_single_openai)
    _run("test_guard_both_providers_shared", test_guard_both_providers_shared)
    _run("test_guard_custom_backend", test_guard_custom_backend)
    _run("test_guard_custom_identifiers", test_guard_custom_identifiers)
    _run("test_guard_auto_uuid", test_guard_auto_uuid)


def run_section_6() -> None:
    """EDGE CASES."""
    print("\n=== SECTION 6: Edge Cases ===")
    _run("test_warn_fires_once", test_warn_fires_once)
    _run("test_block_fires_every_call", test_block_fires_every_call)
    _run("test_budget_exceeded_carries_check_result", test_budget_exceeded_carries_check_result)
    _run("test_teardown_rewrap_fresh", test_teardown_rewrap_fresh)
    _run("test_patch_unpatch_repatch_cycle", test_patch_unpatch_repatch_cycle)
    _run("test_unsupported_client_raises", test_unsupported_client_raises)


# ===================================================================
# Main
# ===================================================================

def main() -> None:
    """Run all smoke test sections and print summary."""
    print("tokencap smoke test")
    print("=" * 60)

    run_section_0()
    run_section_1()
    run_section_2()
    run_section_3()
    run_section_4()
    run_section_5()
    run_section_6()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)

    failures = [(name, msg) for name, ok, msg in _results if not ok]
    if failures:
        print(f"\nFAILURES ({failed}):")
        for name, msg in failures:
            print(f"  {name}: {msg}")

    print(f"\n{passed}/{total} passed, {failed} failed")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
