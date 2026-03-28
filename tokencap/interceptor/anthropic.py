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
        *,
        is_async: bool,
    ) -> None:
        """Initialise with the real messages resource, guard, and async flag."""
        self._messages = messages
        self._guard = guard
        self._is_async = is_async

    def create(self, **kwargs: Any) -> Any:
        """Intercept messages.create(). Routes to async if client is async."""
        if self._is_async:
            return call_async(self._messages.create, kwargs, self._guard)
        return call(self._messages.create, kwargs, self._guard)

    def stream(self, **kwargs: Any) -> GuardedStream:
        """Intercept messages.stream(). Returns a GuardedStream context manager."""
        return call_stream(self._messages.stream, kwargs, self._guard)

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
        client: Any,
        guard: Guard,
    ) -> None:
        """Initialise with the real client and guard."""
        self._client = client
        self._guard = guard
        self._is_async = isinstance(client, anthropic.AsyncAnthropic)

    @property
    def messages(self) -> GuardedMessages:
        """Intercept .messages access. Returns GuardedMessages, not the real resource."""
        return GuardedMessages(
            self._client.messages,
            self._guard,
            is_async=self._is_async,
        )

    def with_options(self, *args: Any, **kwargs: Any) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the client with updated options."""
        return GuardedAnthropic(
            self._client.with_options(*args, **kwargs), self._guard
        )

    @property
    def with_raw_response(self) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the raw-response client."""
        return GuardedAnthropic(
            self._client.with_raw_response, self._guard
        )

    @property
    def with_streaming_response(self) -> GuardedAnthropic:
        """Return a new GuardedAnthropic wrapping the streaming-response client."""
        return GuardedAnthropic(
            self._client.with_streaming_response, self._guard
        )

    def __getattr__(self, name: str) -> Any:
        # api_key, base_url, models, beta, and everything else passes through untracked
        return getattr(self._client, name)
