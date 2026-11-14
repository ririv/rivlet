from __future__ import annotations

from typing import TYPE_CHECKING

from .action_log import NO_ACTION_LOG, ActionLog
from .control import ExecutionControl
from .executor_common import invoke_sync_action
from .python_actions import PythonActionRuntime
from .state import ExecutionState

if TYPE_CHECKING:
    from ..observe import GraphObserver
    from .run import ExecutableGraph


class SerialGraphExecutor:
    """Execute sessions serially, including runtime-created subgraphs."""

    def __init__(
        self,
        state: ExecutionState | None = None,
        *,
        shared: object | None = None,
        execution_control: ExecutionControl | None = None,
        observer: GraphObserver | None = None,
        action_log: ActionLog = NO_ACTION_LOG,
        record_composites_as_steps: bool = False,
    ) -> None:
        self.state = state or ExecutionState()
        self.execution_control = execution_control or ExecutionControl()
        self._runtime = PythonActionRuntime(
            self.state,
            self.execution_control,
            observer,
            action_log,
            shared,
            record_composites_as_steps,
            invoke_sync_action,
        )

    def execute(self, graph: ExecutableGraph) -> ExecutionState:
        from .serial_session import run_serial_definition

        self.execution_control.require_running()
        self.state.begin_run()
        definition = self._runtime.prepare(graph)
        run_serial_definition(definition)
        self.state.require_skip_requests_resolved()
        self._runtime.after_success(graph)
        return self.state
