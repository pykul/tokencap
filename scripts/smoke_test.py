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
use the cheapest models with max_tokens=20).
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
_start_time: float = 0.0

MSG = "What is 2+2?"
MAX_TOKENS = 20


def _run(name: str, fn: Any) -> None:
    """Run a single test function and record the result."""
    print(f"\n  {'─' * 50}")
    print(f"  Running {name}...")
    try:
        ok, msg = fn()
    except Exception as exc:
        ok, msg = False, f"unhandled exception: {exc}"
    _results.append((name, ok, msg))
    if ok:
        print(f"  PASS")
    else:
        print(f"  FAIL: {msg}")


def _log(msg: str) -> None:
    """Print a verbose log line inside a test."""
    print(f"    -> {msg}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = "claude-haiku-4-5"
OPENAI_MODEL = "gpt-4o-mini"
SMALL_MESSAGES_ANTHROPIC: list[dict[str, str]] = [
    {"role": "user", "content": MSG},
]
SMALL_MESSAGES_OPENAI: list[dict[str, str]] = [
    {"role": "user", "content": MSG},
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


def _log_anthropic_response(response: Any) -> None:
    """Log details of an Anthropic response."""
    _log(f"Response: model={response.model}, "
         f"{response.usage.input_tokens} in / {response.usage.output_tokens} out tokens")


def _log_openai_response(response: Any) -> None:
    """Log details of an OpenAI response."""
    _log(f"Response: model={response.model}, "
         f"{response.usage.prompt_tokens} in / {response.usage.completion_tokens} out tokens")


def _log_status(status: Any, dim_name: str = "session") -> None:
    """Log tokencap status for a dimension."""
    dim = status.dimensions[dim_name]
    _log(f"Status: {dim.used:,} / {dim.limit:,} tokens ({dim.pct_used:.2%})")
    _log(f"Policy: {status.active_policy}, next_threshold: {status.next_threshold}")


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
    _log("Both ANTHROPIC_API_KEY and OPENAI_API_KEY are set")
    return True, ""


def test_threshold_rejects_zero() -> tuple[bool, str]:
    """Threshold(at_pct=0.0) raises ValueError."""
    import tokencap
    try:
        tokencap.Threshold(at_pct=0.0, actions=[])
        return False, "no ValueError raised"
    except ValueError as e:
        _log(f"ValueError raised: {e}")
        return True, ""


def test_threshold_rejects_above_one() -> tuple[bool, str]:
    """Threshold(at_pct=1.5) raises ValueError."""
    import tokencap
    try:
        tokencap.Threshold(at_pct=1.5, actions=[])
        return False, "no ValueError raised"
    except ValueError as e:
        _log(f"ValueError raised: {e}")
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
    except ConfigurationError as e:
        _log(f"ConfigurationError raised: {e}")
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
    except ConfigurationError as e:
        _log(f"ConfigurationError raised: {e}")
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
    except ConfigurationError as e:
        _log(f"ConfigurationError raised: {e}")
        return True, ""


def test_unpatch_when_not_patched_is_noop() -> tuple[bool, str]:
    """unpatch() when not patched does not raise."""
    import tokencap
    _ensure_clean()
    try:
        tokencap.unpatch()
        _log("unpatch() returned without error")
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
        _log(f"Calling {ANTHROPIC_MODEL} with: '{MSG}' (tracking only, no limit)")
        client = tokencap.wrap(anthropic.Anthropic(), quiet=True)
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response"
        _log_anthropic_response(response)
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}, expected > 0"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_limit_blocks() -> tuple[bool, str]:
    """wrap(client, limit=1) — BudgetExceededError raised with correct check_result."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL} with limit=1 (should block on estimate)")
        client = tokencap.wrap(anthropic.Anthropic(), limit=1, quiet=True)
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError as exc:
            _log(f"BLOCK raised as expected")
            cr = exc.check_result
            if "session" not in cr.violated:
                return False, f"violated={cr.violated}, expected ['session']"
            if "session" not in cr.states:
                return False, "no 'session' in check_result.states"
            _log(f"violated={cr.violated}, states.session.limit={cr.states['session'].limit}")
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_full_policy_warn() -> tuple[bool, str]:
    """wrap(client, policy=) with WARN at 1% — callback fires, call proceeds."""
    import anthropic
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        _log(f"Calling {ANTHROPIC_MODEL} with WARN at 1% of limit=100")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=100,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not warned:
            return False, "WARN callback not called"
        _log(f"WARN fired: callback called {len(warned)} time(s)")
        if not response.content[0].text:
            return False, "response empty after WARN — call should have proceeded"
        _log_anthropic_response(response)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_block_action() -> tuple[bool, str]:
    """BLOCK at 100% with limit=1 — raises BudgetExceededError."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL} with BLOCK at 100% of limit=1")
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
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            _log("BLOCK raised as expected")
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_degrade() -> tuple[bool, str]:
    """DEGRADE at 1% — call succeeds with degraded model."""
    import anthropic
    import tokencap
    try:
        # Note: we cannot verify from outside that the model was swapped.
        # We verify the call succeeded (was not blocked) and tokens were tracked.
        _log(f"Calling claude-sonnet-4-6 with DEGRADE to {ANTHROPIC_MODEL} at 1% of limit=1M")
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
            model="claude-sonnet-4-6", max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response after DEGRADE"
        _log_anthropic_response(response)
        _log(f"DEGRADE active: call succeeded (model swap is transparent)")
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after DEGRADE"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_webhook() -> tuple[bool, str]:
    """WEBHOOK at 1% — fires HTTP POST to httpbin, does not block."""
    import anthropic
    import tokencap
    try:
        url = "https://httpbin.org/post"
        _log(f"Calling {ANTHROPIC_MODEL} with WEBHOOK to {url} at 1% of limit=1M")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.WEBHOOK,
                        webhook_url=url,
                    )],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        _log(f"WEBHOOK fired to {url} (background thread)")
        time.sleep(2)
        if not response.content[0].text:
            return False, "empty response"
        _log_anthropic_response(response)
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after WEBHOOK"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_client_get_status() -> tuple[bool, str]:
    """client.get_status() returns correct StatusResponse with all fields."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL} with limit=50,000, then checking status fields")
        client = tokencap.wrap(anthropic.Anthropic(), limit=50_000, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
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
        if not status.timestamp:
            return False, "timestamp is empty"
        if not isinstance(status.active_policy, str) or not status.active_policy:
            return False, f"active_policy={status.active_policy!r}"
        # next_threshold can be None (BLOCK-only policy) or ThresholdInfo
        _log_status(status)
        _log(f"timestamp={status.timestamp}, active_policy={status.active_policy!r}")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_module_get_status() -> tuple[bool, str]:
    """tokencap.get_status() returns same data as client.get_status()."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL}, comparing module vs client get_status()")
        client = tokencap.wrap(anthropic.Anthropic(), limit=50_000, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        module_status = tokencap.get_status()
        client_status = client.get_status()
        mod_used = module_status.dimensions["session"].used
        cli_used = client_status.dimensions["session"].used
        if mod_used != cli_used:
            return False, f"module={mod_used} != client={cli_used}"
        _log(f"module.used={mod_used}, client.used={cli_used} (match)")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_multi_dimension() -> tuple[bool, str]:
    """Two dimensions in one policy, both tracked independently."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL} with 2 dimensions: session + tenant")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(limit=1_000_000),
            "tenant": tokencap.DimensionPolicy(limit=5_000_000),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
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
        _log_status(status, "session")
        _log_status(status, "tenant")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_async() -> tuple[bool, str]:
    """wrap(AsyncAnthropic()) — async client tracked."""
    import anthropic
    import tokencap

    async def _inner() -> tuple[bool, str]:
        try:
            _log(f"Calling {ANTHROPIC_MODEL} via AsyncAnthropic")
            client = tokencap.wrap(anthropic.AsyncAnthropic(), quiet=True)
            response = await client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            if not response.content[0].text:
                return False, "empty response"
            _log_anthropic_response(response)
            status = client.get_status()
            if status.dimensions["session"].used <= 0:
                return False, f"used={status.dimensions['session'].used}"
            _log_status(status)
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
        _log(f"with_options() returned {type(opts_client).__name__}")
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
        _log(f"with_raw_response returned {type(raw_client).__name__}")
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
        _log(f"with_streaming_response returned {type(stream_client).__name__}")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_anthropic_streaming() -> tuple[bool, str]:
    """messages.stream() returns GuardedStream, tokens reconciled."""
    import anthropic
    import tokencap
    try:
        _log(f"Streaming {ANTHROPIC_MODEL} with: '{MSG}'")
        client = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        chunks: list[str] = []
        with client.messages.stream(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
        if not chunks:
            return False, "no chunks received"
        _log(f"Received {len(chunks)} chunks: {''.join(chunks)!r}")
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after stream"
        _log_status(status)
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
    _log("quiet=True: no stdout captured")
    return True, ""


def test_wrap_anthropic_teardown_rewrap() -> tuple[bool, str]:
    """teardown() then re-wrap starts a fresh session."""
    import anthropic
    import tokencap
    try:
        _log(f"Session 1: calling {ANTHROPIC_MODEL}")
        client1 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        _log(f"Session 1 used: {used1}")
        tokencap.teardown()
        _log("teardown() called")
        client2 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        status2 = tokencap.get_status()
        if status2.dimensions["session"].used != 0:
            return False, f"second session used={status2.dimensions['session'].used}, expected 0"
        _log(f"Session 2 used: {status2.dimensions['session'].used} (fresh)")
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
        _log(f"Calling {OPENAI_MODEL} with: '{MSG}' (tracking only, no limit)")
        client = tokencap.wrap(openai.OpenAI(), quiet=True)
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response"
        _log_openai_response(response)
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_limit_blocks() -> tuple[bool, str]:
    """wrap(client, limit=1) — BudgetExceededError raised with correct check_result."""
    import openai
    import tokencap
    try:
        _log(f"Calling {OPENAI_MODEL} with limit=1 (should block on estimate)")
        client = tokencap.wrap(openai.OpenAI(), limit=1, quiet=True)
        try:
            client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError as exc:
            _log("BLOCK raised as expected")
            cr = exc.check_result
            if "session" not in cr.violated:
                return False, f"violated={cr.violated}, expected ['session']"
            if "session" not in cr.states:
                return False, "no 'session' in check_result.states"
            _log(f"violated={cr.violated}, states.session.limit={cr.states['session'].limit}")
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_full_policy_warn() -> tuple[bool, str]:
    """wrap(client, policy=) with WARN at 1% — callback fires, call proceeds."""
    import openai
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        _log(f"Calling {OPENAI_MODEL} with WARN at 1% of limit=100")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=100,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not warned:
            return False, "WARN callback not called"
        _log(f"WARN fired: callback called {len(warned)} time(s)")
        if not response.choices[0].message.content:
            return False, "response empty after WARN — call should have proceeded"
        _log_openai_response(response)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_block_action() -> tuple[bool, str]:
    """BLOCK at 100% with limit=1 — raises BudgetExceededError."""
    import openai
    import tokencap
    try:
        _log(f"Calling {OPENAI_MODEL} with BLOCK at 100% of limit=1")
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
                model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            _log("BLOCK raised as expected")
            return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_degrade() -> tuple[bool, str]:
    """DEGRADE at 1% — call succeeds with degraded model."""
    import openai
    import tokencap
    try:
        # Note: we cannot verify from outside that the model was swapped.
        # We verify the call succeeded (was not blocked) and tokens were tracked.
        _log(f"Calling gpt-4o with DEGRADE to {OPENAI_MODEL} at 1% of limit=1M")
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
            model="gpt-4o", max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response after DEGRADE"
        _log_openai_response(response)
        _log(f"DEGRADE active: call succeeded (model swap is transparent)")
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after DEGRADE"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_webhook() -> tuple[bool, str]:
    """WEBHOOK at 1% — fires HTTP POST to httpbin, does not block."""
    import openai
    import tokencap
    try:
        url = "https://httpbin.org/post"
        _log(f"Calling {OPENAI_MODEL} with WEBHOOK to {url} at 1% of limit=1M")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=1_000_000,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(
                        kind=tokencap.ActionKind.WEBHOOK,
                        webhook_url=url,
                    )],
                )],
            ),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        _log(f"WEBHOOK fired to {url} (background thread)")
        time.sleep(2)
        if not response.choices[0].message.content:
            return False, "empty response"
        _log_openai_response(response)
        status = client.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "tokens not tracked after WEBHOOK"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_client_get_status() -> tuple[bool, str]:
    """client.get_status() returns correct StatusResponse with all fields."""
    import openai
    import tokencap
    try:
        _log(f"Calling {OPENAI_MODEL} with limit=50,000, then checking status fields")
        client = tokencap.wrap(openai.OpenAI(), limit=50_000, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
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
        if not status.timestamp:
            return False, "timestamp is empty"
        if not isinstance(status.active_policy, str) or not status.active_policy:
            return False, f"active_policy={status.active_policy!r}"
        _log_status(status)
        _log(f"timestamp={status.timestamp}, active_policy={status.active_policy!r}")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_module_get_status() -> tuple[bool, str]:
    """tokencap.get_status() returns same data as client.get_status()."""
    import openai
    import tokencap
    try:
        _log(f"Calling {OPENAI_MODEL}, comparing module vs client get_status()")
        client = tokencap.wrap(openai.OpenAI(), limit=50_000, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        mod = tokencap.get_status()
        cli = client.get_status()
        mod_used = mod.dimensions["session"].used
        cli_used = cli.dimensions["session"].used
        if mod_used != cli_used:
            return False, f"module={mod_used} != client={cli_used}"
        _log(f"module.used={mod_used}, client.used={cli_used} (match)")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_multi_dimension() -> tuple[bool, str]:
    """Two dimensions in one policy, both tracked independently."""
    import openai
    import tokencap
    try:
        _log(f"Calling {OPENAI_MODEL} with 2 dimensions: session + tenant")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(limit=1_000_000),
            "tenant": tokencap.DimensionPolicy(limit=5_000_000),
        })
        client = tokencap.wrap(openai.OpenAI(), policy=policy, quiet=True)
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
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
        _log_status(status, "session")
        _log_status(status, "tenant")
        return True, ""
    finally:
        _ensure_clean()


def test_wrap_openai_async() -> tuple[bool, str]:
    """wrap(AsyncOpenAI()) — async client tracked."""
    import openai
    import tokencap

    async def _inner() -> tuple[bool, str]:
        try:
            _log(f"Calling {OPENAI_MODEL} via AsyncOpenAI")
            client = tokencap.wrap(openai.AsyncOpenAI(), quiet=True)
            response = await client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_OPENAI,
            )
            if not response.choices[0].message.content:
                return False, "empty response"
            _log_openai_response(response)
            status = client.get_status()
            if status.dimensions["session"].used <= 0:
                return False, f"used={status.dimensions['session'].used}"
            _log_status(status)
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
        _log(f"with_options() returned {type(opts_client).__name__}")
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
        _log(f"with_raw_response returned {type(raw_client).__name__}")
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
        _log(f"with_streaming_response returned {type(stream_client).__name__}")
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
    _log("quiet=True: no stdout captured")
    return True, ""


def test_wrap_openai_teardown_rewrap() -> tuple[bool, str]:
    """teardown() then re-wrap starts a fresh session."""
    import openai
    import tokencap
    try:
        _log(f"Session 1: calling {OPENAI_MODEL}")
        client1 = tokencap.wrap(openai.OpenAI(), limit=1_000_000, quiet=True)
        client1.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        _log(f"Session 1 used: {used1}")
        tokencap.teardown()
        _log("teardown() called")
        client2 = tokencap.wrap(openai.OpenAI(), limit=1_000_000, quiet=True)
        status2 = tokencap.get_status()
        if status2.dimensions["session"].used != 0:
            return False, f"second session used={status2.dimensions['session'].used}, expected 0"
        _log(f"Session 2 used: {status2.dimensions['session'].used} (fresh)")
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
    """patch(providers=[Provider.ANTHROPIC]) wraps Anthropic constructors."""
    import anthropic
    import tokencap
    from tokencap.interceptor.anthropic import GuardedAnthropic
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        if not isinstance(client, GuardedAnthropic):
            return False, f"got {type(client).__name__}, expected GuardedAnthropic"
        _log(f"anthropic.Anthropic() returned {type(client).__name__}")
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_tracking() -> tuple[bool, str]:
    """patch(providers=[Provider.ANTHROPIC]) + make call + verify get_status()."""
    import anthropic
    import tokencap
    try:
        _log(f"patch(limit=50000, providers=[ANTHROPIC]), calling {ANTHROPIC_MODEL}")
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        if status.dimensions["session"].limit != 50_000:
            return False, f"limit={status.dimensions['session'].limit}"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_limit_blocks() -> tuple[bool, str]:
    """patch(limit=1, providers=[Provider.ANTHROPIC]) — BudgetExceededError raised."""
    import anthropic
    import tokencap
    try:
        _log(f"patch(limit=1, providers=[ANTHROPIC]), calling {ANTHROPIC_MODEL}")
        tokencap.patch(limit=1, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            _log("BLOCK raised as expected")
            return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_warn() -> tuple[bool, str]:
    """patch(policy=) with WARN at 1% — callback fires, call proceeds."""
    import anthropic
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        _log(f"patch(WARN at 1% of limit=100, providers=[ANTHROPIC])")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=100,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not warned:
            return False, "WARN callback not called"
        _log(f"WARN fired: callback called {len(warned)} time(s)")
        if not response.content[0].text:
            return False, "response empty after WARN"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_degrade() -> tuple[bool, str]:
    """patch(policy=) with DEGRADE at 1% — call succeeds."""
    import anthropic
    import tokencap
    try:
        _log(f"patch(DEGRADE to {ANTHROPIC_MODEL} at 1% of limit=1M, providers=[ANTHROPIC])")
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
            model="claude-sonnet-4-6", max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not response.content[0].text:
            return False, "empty response"
        _log_anthropic_response(response)
        _log("DEGRADE active: call succeeded")
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
        _log(f"After unpatch: anthropic.Anthropic() returned {type(client).__name__}")
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
        except ConfigurationError as e:
            _log(f"ConfigurationError raised: {e}")
            return True, ""
    finally:
        _ensure_clean()


def test_patch_anthropic_get_status_module_level() -> tuple[bool, str]:
    """tokencap.get_status() works in patch mode."""
    import anthropic
    import tokencap
    try:
        _log(f"patch(limit=50000, providers=[ANTHROPIC]), calling {ANTHROPIC_MODEL}")
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client = anthropic.Anthropic()
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        # In patch mode, status is module-level — no client.get_status()
        status = tokencap.get_status()
        if "session" not in status.dimensions:
            return False, "no session dimension"
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        _log_status(status)
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
            _log("get_status() raised ConfigurationError after unpatch (correct)")
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
    """patch(providers=[Provider.OPENAI]) wraps OpenAI constructors."""
    import openai
    import tokencap
    from tokencap.interceptor.openai import GuardedOpenAI
    try:
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        if not isinstance(client, GuardedOpenAI):
            return False, f"got {type(client).__name__}, expected GuardedOpenAI"
        _log(f"openai.OpenAI() returned {type(client).__name__}")
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_tracking() -> tuple[bool, str]:
    """patch(providers=[Provider.OPENAI]) + make call + verify get_status()."""
    import openai
    import tokencap
    try:
        _log(f"patch(limit=50000, providers=[OPENAI]), calling {OPENAI_MODEL}")
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, f"used={status.dimensions['session'].used}"
        if status.dimensions["session"].limit != 50_000:
            return False, f"limit={status.dimensions['session'].limit}"
        _log_status(status)
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_limit_blocks() -> tuple[bool, str]:
    """patch(limit=1, providers=[Provider.OPENAI]) — BudgetExceededError raised."""
    import openai
    import tokencap
    try:
        _log(f"patch(limit=1, providers=[OPENAI]), calling {OPENAI_MODEL}")
        tokencap.patch(limit=1, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        try:
            client.chat.completions.create(
                model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_OPENAI,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError:
            _log("BLOCK raised as expected")
            return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_warn() -> tuple[bool, str]:
    """patch(policy=) with WARN at 1% — callback fires, call proceeds."""
    import openai
    import tokencap
    warned: list[bool] = []

    def on_warn(status: Any) -> None:
        warned.append(True)

    try:
        _log(f"patch(WARN at 1% of limit=100, providers=[OPENAI])")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=100,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        tokencap.patch(policy=policy, quiet=True, providers=[tokencap.Provider.OPENAI])
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not warned:
            return False, "WARN callback not called"
        _log(f"WARN fired: callback called {len(warned)} time(s)")
        if not response.choices[0].message.content:
            return False, "response empty after WARN"
        return True, ""
    finally:
        _ensure_clean()


def test_patch_openai_degrade() -> tuple[bool, str]:
    """patch(policy=) with DEGRADE at 1% — call succeeds."""
    import openai
    import tokencap
    try:
        _log(f"patch(DEGRADE to {OPENAI_MODEL} at 1% of limit=1M, providers=[OPENAI])")
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
            model="gpt-4o", max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        if not response.choices[0].message.content:
            return False, "empty response"
        _log_openai_response(response)
        _log("DEGRADE active: call succeeded")
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
        _log(f"After unpatch: openai.OpenAI() returned {type(client).__name__}")
        return True, ""
    finally:
        _ensure_clean()


def test_patch_both_providers_share_guard() -> tuple[bool, str]:
    """patch() — both Anthropic and OpenAI share one session budget."""
    import anthropic
    import openai
    import tokencap
    try:
        _log("patch(limit=1M), calling both providers")
        tokencap.patch(limit=1_000_000, quiet=True)
        anth = anthropic.Anthropic()
        anth.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used_after_anth = tokencap.get_status().dimensions["session"].used
        _log(f"After Anthropic call: used={used_after_anth}")

        oai = openai.OpenAI()
        oai.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used_after_both = tokencap.get_status().dimensions["session"].used
        _log(f"After OpenAI call: used={used_after_both}")

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
        _log("init(identifiers={'session': 'smoke-test-custom-id'}), then patch()")
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
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = tokencap.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        _log_status(status)
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
        _log(f"Guard(limit=1M) + wrap_anthropic(), calling {ANTHROPIC_MODEL}")
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        client = guard.wrap_anthropic(anthropic.Anthropic())
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        _log_status(status)
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_single_openai() -> tuple[bool, str]:
    """Guard + wrap_openai() — single provider."""
    import openai
    import tokencap
    try:
        _log(f"Guard(limit=1M) + wrap_openai(), calling {OPENAI_MODEL}")
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        client = guard.wrap_openai(openai.OpenAI())
        client.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        _log_status(status)
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
        _log("Guard(limit=1M) with both providers sharing budget")
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            quiet=True,
        )
        anth = guard.wrap_anthropic(anthropic.Anthropic())
        oai = guard.wrap_openai(openai.OpenAI())

        anth.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = guard.get_status().dimensions["session"].used
        _log(f"After Anthropic call: used={used1}")

        oai.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_OPENAI,
        )
        used2 = guard.get_status().dimensions["session"].used
        _log(f"After OpenAI call: used={used2} (combined)")

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
        _log(f"Guard with SQLiteBackend(path={db_path})")
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
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        if not os.path.exists(db_path):
            return False, "database file not created"
        _log(f"Database file created: {db_path}")
        status = guard.get_status()
        if status.dimensions["session"].used <= 0:
            return False, "used=0"
        _log_status(status)
        return True, ""
    finally:
        guard.teardown()
        _ensure_clean()


def test_guard_custom_identifiers() -> tuple[bool, str]:
    """Guard with custom identifiers — verified in get_status()."""
    import anthropic
    import tokencap
    try:
        _log("Guard(identifiers={'session': 'custom-smoke-id'})")
        guard = tokencap.Guard(
            policy=tokencap.Policy(dimensions={
                "session": tokencap.DimensionPolicy(limit=1_000_000),
            }),
            identifiers={"session": "custom-smoke-id"},
            quiet=True,
        )
        client = guard.wrap_anthropic(anthropic.Anthropic())
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status = guard.get_status()
        key = status.dimensions["session"].key
        if key.identifier != "custom-smoke-id":
            return False, f"identifier={key.identifier}"
        _log(f"Identifier in status: {key.identifier!r} (matches)")
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
        _log(f"Auto-generated UUID: {ident}")
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
        _log(f"Two calls to {ANTHROPIC_MODEL}, WARN at 1% of limit=100")
        policy = tokencap.Policy(dimensions={
            "session": tokencap.DimensionPolicy(
                limit=100,
                thresholds=[tokencap.Threshold(
                    at_pct=0.01,
                    actions=[tokencap.Action(kind=tokencap.ActionKind.WARN, callback=on_warn)],
                )],
            ),
        })
        client = tokencap.wrap(anthropic.Anthropic(), policy=policy, quiet=True)
        # Two calls — both cross the 0.01 threshold
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        _log(f"After call 1: warned {len(warned)} time(s)")
        client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        _log(f"After call 2: warned {len(warned)} time(s)")
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
        _log(f"Two calls to {ANTHROPIC_MODEL}, BLOCK at 100% of limit=1")
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
        for i in range(2):
            try:
                client.messages.create(
                    model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                    messages=SMALL_MESSAGES_ANTHROPIC,
                )
            except tokencap.BudgetExceededError:
                blocked_count += 1
                _log(f"Call {i+1}: BLOCK raised")
        if blocked_count != 2:
            return False, f"blocked {blocked_count} times, expected 2"
        return True, ""
    finally:
        _ensure_clean()


def test_budget_exceeded_carries_check_result() -> tuple[bool, str]:
    """BudgetExceededError has check_result with violated list, states dict, correct limit."""
    import anthropic
    import tokencap
    try:
        _log(f"Calling {ANTHROPIC_MODEL} with limit=1, inspecting check_result")
        client = tokencap.wrap(anthropic.Anthropic(), limit=1, quiet=True)
        try:
            client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
                messages=SMALL_MESSAGES_ANTHROPIC,
            )
            return False, "no BudgetExceededError raised"
        except tokencap.BudgetExceededError as exc:
            cr = exc.check_result
            if not isinstance(cr.violated, list):
                return False, f"violated is {type(cr.violated).__name__}, expected list"
            if "session" not in cr.violated:
                return False, f"violated={cr.violated}"
            if not isinstance(cr.states, dict):
                return False, f"states is {type(cr.states).__name__}, expected dict"
            if "session" not in cr.states:
                return False, "no 'session' in states"
            state = cr.states["session"]
            if state.limit != 1:
                return False, f"state.limit={state.limit}"
            _log(f"violated={cr.violated}")
            _log(f"states.session: used={state.used}, limit={state.limit}")
            return True, ""
    finally:
        _ensure_clean()


def test_teardown_rewrap_fresh() -> tuple[bool, str]:
    """teardown() + re-wrap gives fresh state (used=0)."""
    import anthropic
    import tokencap
    try:
        _log(f"Session 1: calling {ANTHROPIC_MODEL}")
        client1 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        used1 = tokencap.get_status().dimensions["session"].used
        if used1 <= 0:
            return False, "first session used=0"
        _log(f"Session 1 used: {used1}")
        tokencap.teardown()
        _log("teardown() called")

        # Second session — fresh Guard, fresh backend
        client2 = tokencap.wrap(anthropic.Anthropic(), limit=1_000_000, quiet=True)
        used2 = tokencap.get_status().dimensions["session"].used
        if used2 != 0:
            return False, f"second session used={used2}, expected 0"
        _log(f"Session 2 used: {used2} (fresh)")
        return True, ""
    finally:
        _ensure_clean()


def test_patch_unpatch_repatch_cycle() -> tuple[bool, str]:
    """patch -> call -> unpatch -> patch(new limit) -> call -> fresh state."""
    import anthropic
    import tokencap
    try:
        _log("Cycle: patch(50K) -> call -> unpatch -> patch(100K) -> call")
        tokencap.patch(limit=50_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client1 = anthropic.Anthropic()
        client1.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status1 = tokencap.get_status()
        if status1.dimensions["session"].limit != 50_000:
            return False, f"first limit={status1.dimensions['session'].limit}"
        if status1.dimensions["session"].used <= 0:
            return False, "first used=0"
        _log(f"Phase 1: limit={status1.dimensions['session'].limit}, used={status1.dimensions['session'].used}")

        tokencap.unpatch()
        _log("unpatch() called")

        tokencap.patch(limit=100_000, quiet=True, providers=[tokencap.Provider.ANTHROPIC])
        client2 = anthropic.Anthropic()
        client2.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=MAX_TOKENS,
            messages=SMALL_MESSAGES_ANTHROPIC,
        )
        status2 = tokencap.get_status()
        if status2.dimensions["session"].limit != 100_000:
            return False, f"second limit={status2.dimensions['session'].limit}"
        if status2.dimensions["session"].used <= 0:
            return False, "second used=0"
        _log(f"Phase 2: limit={status2.dimensions['session'].limit}, used={status2.dimensions['session'].used}")
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
    except ConfigurationError as e:
        _log(f"ConfigurationError raised: {e}")
        return True, ""
    finally:
        _ensure_clean()


# ===================================================================
# Section runners
# ===================================================================

def _section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print(f"{'=' * 55}")


def run_section_0() -> None:
    """Preamble — no API calls."""
    _section("SECTION 0: PREAMBLE (no API calls)")
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
    _section("SECTION 1: WRAP MODE -- ANTHROPIC")
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
    _section("SECTION 2: WRAP MODE -- OPENAI")
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
    _section("SECTION 3: PATCH MODE -- ANTHROPIC")
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
    _section("SECTION 4: PATCH MODE -- OPENAI")
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
    _section("SECTION 5: EXPLICIT GUARD MODE")
    _run("test_guard_single_anthropic", test_guard_single_anthropic)
    _run("test_guard_single_openai", test_guard_single_openai)
    _run("test_guard_both_providers_shared", test_guard_both_providers_shared)
    _run("test_guard_custom_backend", test_guard_custom_backend)
    _run("test_guard_custom_identifiers", test_guard_custom_identifiers)
    _run("test_guard_auto_uuid", test_guard_auto_uuid)


def run_section_6() -> None:
    """EDGE CASES."""
    _section("SECTION 6: EDGE CASES")
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
    global _start_time
    _start_time = time.time()

    print("=" * 55)
    print("  tokencap smoke test")
    print("=" * 55)

    run_section_0()
    run_section_1()
    run_section_2()
    run_section_3()
    run_section_4()
    run_section_5()
    run_section_6()

    elapsed = time.time() - _start_time

    # Summary
    print(f"\n{'=' * 55}")
    print("  tokencap smoke test results")
    print(f"{'=' * 55}")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)

    if failed:
        print(f"\n  FAILURES ({failed}):")
        for name, ok, msg in _results:
            if not ok:
                print(f"    {name}: {msg}")

    print(f"\n  Passed:  {passed} / {total}")
    print(f"  Failed:  {failed}")
    print(f"  Time:    {elapsed:.1f}s")
    print(f"{'=' * 55}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
