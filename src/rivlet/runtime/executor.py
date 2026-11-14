from __future__ import annotations

import asyncio
import inspect
import threading
from contextvars import Context, copy_context
from typing import TYPE_CHECKING

from .action_log import NO_ACTION_LOG, ActionLog
from .batch_driver import arun_session, run_session
from .executor_common import step_thread_name
from .pools import require_shared_thread_pool
from .python_actions import PythonActionRuntime
from .state import ExecutionState

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from ..model import Step
    from ..observe import GraphObserver
    from ..option import Option
    from ..results import NodeRunContext
    from .control import ExecutionControl
    from .run import ExecutableGraph
    from .session_tree import SessionDefinition


async def _await_action(
    value: Awaitable[object],
    path: Path | None,
    action_log: ActionLog,
) -> object:
    if path is None:
        return await value
    with action_log.scope(path):
        return await value


async def _await_in_context(value: Awaitable[object], context: Context) -> object:
    async def finish() -> object:
        return await value

    # 同步函数可能在返回协程前修改 ContextVar；协程切换到事件循环后
    # 继续使用动作自己的上下文，不继承调度线程的上下文。
    return await asyncio.create_task(finish(), context=context)


def invoke_action(
    action: Step,
    ctx: NodeRunContext,
    path: Path | None,
    action_log: ActionLog,
    inputs: tuple[Option[object], ...] = (),
) -> object:
    thread = threading.current_thread()
    original_name = thread.name
    coroutine_action = inspect.iscoroutinefunction(action.action)
    if not coroutine_action:
        thread.name = step_thread_name(ctx.node_path.labels)
    try:
        if path is None:
            value = action.invoke(ctx, inputs)
        else:
            with action_log.scope(path):
                value = action.invoke(ctx, inputs)
        # 协程体尚未执行，日志作用域要在实际等待期间重新进入。
        if inspect.isawaitable(value):
            awaited = _await_action(value, path, action_log)
            return (
                awaited
                if coroutine_action
                else _await_in_context(awaited, copy_context())
            )
        return value
    finally:
        thread.name = original_name


class GraphExecutor:
    """Execute mixed actions using a borrowed same-interpreter thread pool."""

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
            invoke_action,
        )

    def _prepare(self, graph: ExecutableGraph) -> SessionDefinition:
        self.execution_control.require_running()
        self.state.begin_run()
        return self._runtime.prepare(graph)

    def _finish(self, graph: ExecutableGraph) -> ExecutionState:
        self.state.require_skip_requests_resolved()
        self._runtime.after_success(graph)
        return self.state

    def execute(
        self, graph: ExecutableGraph, *, pool: ThreadPoolExecutor
    ) -> ExecutionState:
        require_shared_thread_pool(pool)
        run_session(self._prepare(graph), pool)
        return self._finish(graph)

    async def aexecute(
        self, graph: ExecutableGraph, *, pool: ThreadPoolExecutor
    ) -> ExecutionState:
        require_shared_thread_pool(pool)
        await arun_session(self._prepare(graph), pool)
        return self._finish(graph)
