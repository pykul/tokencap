"""Exceptions raised by tokencap."""

from __future__ import annotations

from tokencap.core.types import CheckResult


class BudgetExceededError(Exception):
    """Raised by the BLOCK action before an LLM call is made.

    The call is never sent to the provider.

    Attributes:
        check_result: Full state of every dimension at the time of the block.
            check_result.violated lists the dimension names that caused the block.
            check_result.states maps every dimension name to its BudgetState.
    """

    def __init__(self, check_result: CheckResult) -> None:
        self.check_result = check_result
        violated = ", ".join(check_result.violated)
        super().__init__(f"Budget exceeded on dimensions: {violated}")


class BackendError(Exception):
    """Raised when the storage backend encounters an unrecoverable error.

    For example, a lost Redis connection during check_and_increment.
    The LLM call is not made when this is raised.
    """


class ConfigurationError(Exception):
    """Raised during Guard initialisation when the policy or backend configuration is invalid.

    For example, a DimensionPolicy with a limit of zero, or an unrecognised
    backend type.
    """
