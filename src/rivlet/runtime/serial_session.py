from __future__ import annotations

import asyncio
import inspect
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..cancellation import CancellationRequested
from ..events import EventDeliveryError
from .control import ExecutionStopped
from .session_tree import DynamicSubgraph, RootSession, SessionDefinition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .._native import _DriverBatch, _DriverSession
    from .session_tree import BatchExecutionHost


@dataclass(frozen=True, slots=True)
class _Child:
    node: int
    complete: Callable[[], None]


@dataclass(slots=True)
class _Frame:
    definition: SessionDefinition
    engine: _DriverSession
    batch: _DriverBatch
    owner: RootSession | _Child
    held_actions: list[int] = field(default_factory=list)


def _frame(definition: SessionDefinition, owner: RootSession | _Child) -> _Frame:
    engine = definition.graph.mixed_session(
        definition.host.skipped_nodes(),
        definition.rust_actions,
        definition.rust_contexts,
        1,
        retain_results=definition.host.retains_rust_results(),
    )
    if definition.host.cancellation_requested():
        engine.request_cancel()
    return _Frame(definition, engine, engine.start(1), owner)


async def _await_condition(value: Awaitable[bool]) -> bool:
    return await value


def _resolve_conditions(
    host: BatchExecutionHost,
    requests: list[tuple[int, int]],
    get_runner: Callable[[], asyncio.Runner],
) -> tuple[list[tuple[int, bool | None]], list[int]]:
    resolutions: list[tuple[int, bool | None]] = []
    cancelled: list[int] = []
    for node, condition in requests:
        try:
            value = host.evaluate_condition(node, condition)
            if not isinstance(value, bool):
                value = get_runner().run(_await_condition(value))
        except CancellationRequested as error:
            if host.owns_cancellation(error):
                cancelled.append(condition)
            else:
                host.record_failure(node, error)
                resolutions.append((condition, None))
        except (ExecutionStopped, EventDeliveryError):
            raise
        except Exception as error:
            host.record_failure(node, error)
            resolutions.append((condition, None))
        else:
            resolutions.append((condition, value))
    return resolutions, cancelled


def run_serial_definition(root: SessionDefinition) -> None:
    # 显式串行入口保留逐项等待语义；只有真的遇到 awaitable 才创建一个循环。
    with ExitStack() as resources:
        runner: asyncio.Runner | None = None

        def get_runner() -> asyncio.Runner:
            nonlocal runner
            if runner is None:
                runner = resources.enter_context(asyncio.Runner())
            return runner

        _run_serial_definition(root, get_runner)


def _run_serial_definition(
    root: SessionDefinition, get_runner: Callable[[], asyncio.Runner]
) -> None:
    # 串行只需显式栈：O(D) 个 session 帧，D 为动态嵌套深度。
    # 静态路径按原有单批次流程执行，不为每个 action 构造并发调度工作对象。
    stack = [_frame(root, RootSession())]
    try:
        while stack:
            frame = stack[-1]
            host = frame.definition.host
            host.require_not_stopped()
            actions, conditions, events, finished, rust_running, failures, results = (
                frame.batch.parts()
            )
            host.apply_events(events, results)
            for node, message in failures:
                host.record_failure(node, RuntimeError(message))
            if host.cancellation_requested():
                frame.engine.request_cancel()
                if actions or conditions or frame.held_actions:
                    frame.batch = frame.engine.advance(
                        [],
                        [],
                        host.skipped_nodes(),
                        1,
                        cancelled=[*frame.held_actions, *actions],
                        cancelled_conditions=[condition for _, condition in conditions],
                    )
                    frame.held_actions.clear()
                    continue
            if conditions:
                frame.held_actions.extend(actions)
                resolutions, cancelled_conditions = _resolve_conditions(
                    host, conditions, get_runner
                )
                if host.cancellation_requested():
                    frame.engine.request_cancel()
                frame.batch = frame.engine.advance(
                    [],
                    resolutions,
                    host.skipped_nodes(),
                    1 - len(frame.held_actions) - rust_running,
                    cancelled_conditions=cancelled_conditions,
                )
                continue
            if frame.held_actions:
                actions = frame.held_actions + actions
                frame.held_actions = []
            if finished:
                stack.pop()
                match frame.owner:
                    case RootSession():
                        host.raise_failures()
                    case _Child(node=node, complete=complete):
                        parent = stack[-1]
                        try:
                            host.raise_failures()
                        except Exception as error:
                            parent.definition.host.record_subgraph_failure(
                                node, host, error
                            )
                            success = False
                        else:
                            if host.cancellation_requested():
                                parent.engine.request_cancel()
                                parent.batch = parent.engine.advance(
                                    [],
                                    [],
                                    parent.definition.host.skipped_nodes(),
                                    1,
                                    cancelled=[node],
                                )
                                continue
                            complete()
                            success = True
                        parent.batch = parent.engine.advance(
                            [(node, success)],
                            [],
                            parent.definition.host.skipped_nodes(),
                            1,
                            values=parent.definition.host.rust_input_values(
                                [(node, success)]
                            ),
                        )
                continue
            if not actions:
                if not rust_running:
                    raise RuntimeError(
                        "running graph session returned no serial action"
                    )
                frame.engine.wait_rust()
                frame.batch = frame.engine.advance([], [], host.skipped_nodes(), 0)
                continue
            node = actions[0]
            try:
                result = host.run_step(node)
                if isinstance(result, DynamicSubgraph):
                    stack.append(
                        _frame(result.definition, _Child(node, result.complete))
                    )
                    continue
                if inspect.isawaitable(result):
                    raise TypeError(
                        "synchronous driver received an asynchronous action host"
                    )
            except CancellationRequested as error:
                if not host.owns_cancellation(error):
                    host.record_failure(node, error)
                    success = False
                else:
                    frame.engine.request_cancel()
                    frame.batch = frame.engine.advance(
                        [], [], host.skipped_nodes(), 1, cancelled=[node]
                    )
                    continue
            except (ExecutionStopped, EventDeliveryError):
                raise
            except Exception as error:
                host.record_failure(node, error)
                success = False
            else:
                success = True
            if host.cancellation_requested():
                frame.engine.request_cancel()
            frame.batch = frame.engine.advance(
                [(node, success)],
                [],
                host.skipped_nodes(),
                1,
                values=host.rust_input_values([(node, success)]),
            )
    except BaseException:
        root.host.request_stop()
        raise
    root.host.require_not_stopped()
