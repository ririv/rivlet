from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..events import EventDeliveryError
from .control import ExecutionStopped
from .session_tree import DynamicSubgraph, RootSession, SessionDefinition

if TYPE_CHECKING:
    from collections.abc import Callable

    from .._native import MixedExecutionBatch, MixedExecutionSession


@dataclass(frozen=True, slots=True)
class _Child:
    node: int
    complete: Callable[[], None]


@dataclass(slots=True)
class _Frame:
    definition: SessionDefinition
    engine: MixedExecutionSession
    batch: MixedExecutionBatch
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
    return _Frame(definition, engine, engine.start(1), owner)


def run_serial_definition(root: SessionDefinition) -> None:
    # 串行只需显式栈：O(D) 个 session 帧，D 为动态嵌套深度。
    # 静态路径按原有单批次流程执行，不为每个 action 构造并发调度工作对象。
    stack = [_frame(root, RootSession())]
    try:
        while stack:
            frame = stack[-1]
            host = frame.definition.host
            host.require_running()
            actions, conditions, events, finished, rust_running, failures, results = (
                frame.batch.parts()
            )
            host.apply_events(events, results)
            for node, message in failures:
                host.record_failure(node, RuntimeError(message))
            if conditions:
                frame.held_actions.extend(actions)
                frame.batch = frame.engine.advance(
                    [],
                    host.resolve_conditions(conditions),
                    host.skipped_nodes(),
                    1 - len(frame.held_actions) - rust_running,
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
                result = host.run_leaf(node)
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
                values=host.rust_input_values([(node, success)]),
            )
    except BaseException:
        root.host.request_stop()
        raise
    root.host.require_running()
