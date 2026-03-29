"""GuardedAnthropic and GuardedMessages: Anthropic SDK interception.

Intercepts messages.create() and messages.stream(). Everything else
passes through via __getattr__.
"""

from __future__ import annotations

from typing import Any

from tokencap.core.guard import Guard
from tokencap.interceptor.base import GuardedStream, call, call_async, call_stream

try:
    import anthropic

    # Capture original class refs before patch() can replace them
    _AsyncAnthropic = anthropic.AsyncAnthropic
except ImportError as _err:
    raise ImportError(
        "GuardedAnthropic requires the anthropic package. "
        "Install it with: pip install tokencap[anthropic]"
    ) from _err


class GuardedMessages:
    """Proxy for anthropic.resources.Messages.

    Intercepts create() and stream(). Everything else passes through
    via __getattr__ (batch, count_tokens, etc. — untracked).
    """

    def __init__(
        self,
        messages: Any,
        guard: Guard,
        provider: Any,
        *,
        is_async: bool,
    ) -> None:
        """Initialise with the real messages resource, guard, provider, and async flag."""
        self._messages = messages
        self._guard = guard
        self._provider = provider
        self._is_async = is_async

    def create(self, **kwargs: Any) -> Any:
        """Intercept messages.create(). Routes to async if client is async."""
        if self._is_async:
            return call_async(
                self._messages.create, kwargs, self._guard, self._provider
            )
        return call(self._messages.create, kwargs, self._guard, self._provider)

    def stream(self, **kwargs: Any) -> GuardedStream:
        """Intercept messages.stream(). Returns a GuardedStream context manager."""
        return call_stream(
            self._messages.stream, kwargs, self._guard, self._provider
        )

    def __getattr__(self, name: str) -> Any:
        # batch, count_tokens, and any other messages attributes pass through untracked
        return getattr(self._messages, name)


class GuardedAnthropic:
    """Proxy for anthropic.Anthropic and anthropic.AsyncAnthropic.

    @property intercepts .messages before __getattr__ is considered.
    All client-returning methods are explicit *args/**kwargs methods
    that return new GuardedAnthropic instances. See D-027.
    Everything else delegates via __getattr__.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        guard: Guard,
        provider: Any,
    ) -> None:
        """Initialise with the real client, guard, and provider."""
        self._client = client
        self._guard = guard
        self._provider = provider
        self._is_async = isinstance(client, _AsyncAnthropic)

    @property
    def messages(self) -> GuardedMessages:
        """Intercept .messages access. Returns GuardedMessages, not the real resource."""
        return GuardedMessages(
            self._client.messages,
            self._guard,
            self._provider,
            is_async=self._is_async,
        )

    def with_options(self, *args: Any, **kwargs: Any) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the client with updated options."""
        return GuardedAnthropic(
            self._client.with_options(*args, **kwargs),
            self._guard,
            self._provider,
        )

    @property
    def with_raw_response(self) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the raw-response client."""
        return GuardedAnthropic(
            self._client.with_raw_response,  # type: ignore[arg-type]
            self._guard,
            self._provider,
        )

    @property
    def with_streaming_response(self) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the streaming-response client."""
        return GuardedAnthropic(
            self._client.with_streaming_response,  # type: ignore[arg-type]
            self._guard,
            self._provider,
        )

    def get_status(self) -> Any:
        """Return the current budget status for this client's Guard."""
        return self._guard.get_status()

    def __getattr__(self, name: str) -> Any:
        # api_key, base_url, models, beta, and everything else passes through untracked
        return getattr(self._client, name)
