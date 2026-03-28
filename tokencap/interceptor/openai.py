"""GuardedOpenAI, GuardedChat, GuardedCompletions: OpenAI SDK interception.

Intercepts chat.completions.create(). Everything else passes through
via __getattr__. Two levels of proxying match the OpenAI resource hierarchy.
"""

from __future__ import annotations

from typing import Any

from tokencap.core.guard import Guard
from tokencap.interceptor.base import call, call_async, call_stream

try:
    import openai
except ImportError as _err:
    raise ImportError(
        "GuardedOpenAI requires the openai package. "
        "Install it with: pip install tokencap[openai]"
    ) from _err


class GuardedCompletions:
    """Proxy for openai.resources.chat.Completions.

    Intercepts create(). Everything else passes through untracked.
    """

    def __init__(
        self,
        completions: Any,
        guard: Guard,
        *,
        is_async: bool,
    ) -> None:
        """Initialise with the real completions resource, guard, and async flag."""
        self._completions = completions
        self._guard = guard
        self._is_async = is_async

    def create(self, **kwargs: Any) -> Any:
        """Intercept completions.create().

        For streaming calls, injects stream_options to get usage data (D-030).
        This is done in a copy of kwargs, never mutates the caller's dict.
        """
        if kwargs.get("stream"):
            kwargs = dict(kwargs)
            kwargs.setdefault(
                "stream_options", {"include_usage": True}
            )
            return call_stream(self._completions.create, kwargs, self._guard)
        if self._is_async:
            return call_async(self._completions.create, kwargs, self._guard)
        return call(self._completions.create, kwargs, self._guard)

    def __getattr__(self, name: str) -> Any:
        # All other completions attributes pass through untracked
        return getattr(self._completions, name)


class GuardedChat:
    """Proxy for openai.resources.Chat. Intercepts .completions."""

    def __init__(self, chat: Any, guard: Guard, *, is_async: bool) -> None:
        """Initialise with the real chat resource, guard, and async flag."""
        self._chat = chat
        self._guard = guard
        self._is_async = is_async

    @property
    def completions(self) -> GuardedCompletions:
        """Intercept .completions access."""
        return GuardedCompletions(
            self._chat.completions, self._guard, is_async=self._is_async
        )

    def __getattr__(self, name: str) -> Any:
        # All other chat attributes pass through untracked
        return getattr(self._chat, name)


class GuardedOpenAI:
    """Proxy for openai.OpenAI and openai.AsyncOpenAI.

    Same pattern as GuardedAnthropic. Intercepts .chat via @property.
    All client-returning methods are explicit *args/**kwargs methods. See D-027.
    """

    def __init__(self, client: openai.OpenAI, guard: Guard) -> None:
        """Initialise with the real client and guard."""
        self._client = client
        self._guard = guard
        self._is_async = isinstance(client, openai.AsyncOpenAI)

    @property
    def chat(self) -> GuardedChat:
        """Intercept .chat access. Returns GuardedChat, not the real resource."""
        return GuardedChat(
            self._client.chat, self._guard, is_async=self._is_async
        )

    def with_options(self, *args: Any, **kwargs: Any) -> GuardedOpenAI:
        """Return a new GuardedOpenAI wrapping the client with updated options."""
        return GuardedOpenAI(
            self._client.with_options(*args, **kwargs), self._guard
        )

    @property
    def with_raw_response(self) -> GuardedOpenAI:
        """Return a new GuardedOpenAI wrapping the raw-response client."""
        return GuardedOpenAI(
            self._client.with_raw_response, self._guard  # type: ignore[arg-type]
        )

    @property
    def with_streaming_response(self) -> GuardedOpenAI:
        """Return a new GuardedOpenAI wrapping the streaming-response client."""
        return GuardedOpenAI(
            self._client.with_streaming_response, self._guard  # type: ignore[arg-type]
        )

    def __getattr__(self, name: str) -> Any:
        # api_key, base_url, models, files, and everything else passes through untracked
        return getattr(self._client, name)
