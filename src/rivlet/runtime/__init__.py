from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .action_log import (
        NO_ACTION_LOG,
        ActionLog,
        NoActionLog,
    )
    from .control import ExecutionControl, ExecutionStopped, StopHandler
    from .executor import GraphExecutor
    from .serial_executor import SerialGraphExecutor
    from .state import ExecutionSnapshot, ExecutionState, GraphNodeFailure

__all__ = [
    "GraphNodeFailure",
    "ExecutionState",
    "ExecutionSnapshot",
    "ExecutionControl",
    "ExecutionStopped",
    "StopHandler",
    "ActionLog",
    "NoActionLog",
    "NO_ACTION_LOG",
    "SerialGraphExecutor",
    "GraphExecutor",
]

_EXPORT_MODULES = {
    "GraphNodeFailure": ".state",
    "ActionLog": ".action_log",
    "NoActionLog": ".action_log",
    "NO_ACTION_LOG": ".action_log",
    "ExecutionControl": ".control",
    "ExecutionStopped": ".control",
    "StopHandler": ".control",
    "GraphExecutor": ".executor",
    "SerialGraphExecutor": ".serial_executor",
    "ExecutionState": ".state",
    "ExecutionSnapshot": ".state",
}


def __getattr__(name: str) -> object:
    """Load only the runtime component requested by the public API."""

    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public symbols to interactive API discovery."""

    return sorted({*globals(), *__all__})
