"""Enums for tokencap public API parameters.

All enums inherit from str so that existing string values continue to work:
    ActionKind.WARN == "WARN"  # True
    Provider.ANTHROPIC == "anthropic"  # True

This file has no imports from any other tokencap module.
"""

from __future__ import annotations

from enum import Enum


class ActionKind(str, Enum):
    """The kind of policy action to execute when a threshold is crossed."""

    WARN = "WARN"
    BLOCK = "BLOCK"
    DEGRADE = "DEGRADE"
    WEBHOOK = "WEBHOOK"


class Provider(str, Enum):
    """Supported LLM provider SDKs."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class ResetPeriod(str, Enum):
    """Budget reset period. Planned for v0.2 — field exists but is not yet active."""

    HOUR = "hour"
    DAY = "day"
