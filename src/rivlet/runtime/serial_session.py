from __future__ import annotations

import asyncio
import inspect
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..events import EventDeliveryError
from .control import ExecutionStopped
from .session_tree import DynamicSubgraph, RootSession, SessionDefinition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .._native import ExecutionBatch, ExecutionSession
    from .session_tree import BatchExecutionHost


@dataclass(frozen=True, slots=True)
class _Child:
    node: int
    complete: Callable[[], None]


@dataclass(slots=True)
class _Frame:
    definition: SessionDefinition
    engine: ExecutionSession
    batch: ExecutionBatch
    owner: RootSession | _Child
    held_actions: list[int] = field(default_factory=list)


def _frame(definition: SessionDefinition, owner: RootSession | _Child) -> _Frame:
    engine = definition.graph.session(
        definition.host.skipped_nodes(),
    )
    return _Frame(definition, engine, engine.start(1), owner)


async def _await_condition(value: Awaitable[bool]) -> bool:
    return await value


def _resolve_conditions(
    host: BatchExecutionHost,
    requests: list[tuple[int, int]],
    get_runner: Callable[[], asyncio.Runner],
) -> list[tuple[int, bool | None]]:
    resolutions: list[tuple[int, bool | None]] = []
    for node, condition in requests:
        try:
            value = host.evaluate_condition(node, condition)
            if not isinstance(value, bool):
                value = get_runner().run(_await_condition(value))
        except (ExecutionStopped, EventDeliveryError):
            raise
        except Exception as error:
            host.record_failure(node, error)
            resolutions.append((condition, None))
        else:
            resolutions.append((condition, value))
    return resolutions


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
            host.require_running()
            actions, conditions, events, finished = frame.batch.parts()
            host.apply_events(events)
            if conditions:
                frame.held_actions.extend(actions)
                frame.batch = frame.engine.advance(
                    [],
                    _resolve_conditions(host, conditions, get_runner),
                    host.skipped_nodes(),
                    1 - len(frame.held_actions),
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
                            complete()
                            success = True
                        parent.batch = parent.engine.advance(
                            [(node, success)],
                            [],
                            parent.definition.host.skipped_nodes(),
                            1,
                        )
                continue
            if not actions:
                raise RuntimeError("running graph session returned no serial action")
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
            except (ExecutionStopped, EventDeliveryError):
                raise
            except Exception as error:
                host.record_failure(node, error)
                success = False
            else:
                success = True
            frame.batch = frame.engine.advance(
                [(node, success)],
                [],
                host.skipped_nodes(),
                1,
            )
    except BaseException:
        root.host.request_stop()
        raise
    root.host.require_running()
