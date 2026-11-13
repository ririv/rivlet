from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from .action_log import NO_ACTION_LOG, ActionLog
from .batch_driver import run_threaded_session
from .executor_common import invoke_sync_action, step_thread_name
from .python_actions import PythonActionRuntime
from .state import ExecutionState

if TYPE_CHECKING:
    from pathlib import Path

    from ..model import Leaf
    from ..observe import GraphObserver
    from ..option import Option
    from ..results import NodeRunContext
    from .control import ExecutionControl
    from .run import ExecutableGraph


def _invoke_thread_action(
    action: Leaf,
    ctx: NodeRunContext,
    step_log_path: Path | None,
    action_log: ActionLog,
    inputs: tuple[Option[object], ...] = (),
) -> object:
    thread = threading.current_thread()
    original_name = thread.name
    thread.name = step_thread_name(ctx.node_path.labels)
    try:
        return invoke_sync_action(action, ctx, step_log_path, action_log, inputs)
    finally:
        thread.name = original_name


class ParallelGraphExecutor:
    """Execute all Python sessions through one bounded thread pool."""

    def __init__(
        self,
        state: ExecutionState | None = None,
        *,
        shared: object | None = None,
        execution_control: ExecutionControl,
        observer: GraphObserver | None = None,
        action_log: ActionLog = NO_ACTION_LOG,
        record_composites_as_steps: bool = False,
    ) -> None:
        self.state = state or ExecutionState()
        self.execution_control = execution_control
        self._runtime = PythonActionRuntime(
            self.state,
            execution_control,
            observer,
            action_log,
            shared,
            record_composites_as_steps,
            _invoke_thread_action,
        )

    def execute(self, graph: ExecutableGraph, *, workers: int) -> ExecutionState:
        self.execution_control.require_running()
        self.state.begin_run()
        definition = self._runtime.prepare(graph)
        run_threaded_session(
            definition.graph,
            definition.host,
            workers=workers,
            rust_actions=definition.rust_actions,
            rust_contexts=definition.rust_contexts,
        )
        self.state.require_skip_requests_resolved()
        self._runtime.after_success(graph)
        return self.state
