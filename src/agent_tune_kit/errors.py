"""Errors shared by the CLI and project adapters."""


class UserActionRequired(RuntimeError):
    """Input, configuration or confirmation must be repaired before proceeding."""


class AgentExecutionError(RuntimeError):
    """A known per-row execution failure that must remain in evaluation evidence."""
