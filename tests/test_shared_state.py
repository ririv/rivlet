from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, assert_type

import pytest
from execution_support import run_async_execution

from rivlet import (
    Execution,
    Graph,
    GraphMode,
    GraphStructure,
    NodeControl,
    NodeId,
    NodePath,
    NodeRunContext,
    SerialGraphExecutor,
    Step,
)
from rivlet import run as run_execution


@dataclass
class Progress:
    completed: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete(self, name: str) -> None:
        with self.lock:
            self.completed.add(name)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("executor", ["serial", "threads", "async"])
def test_shared_object_reaches_parallel_and_nested_actions(
    mode: GraphMode, executor: Literal["serial", "threads", "async"]
) -> None:
    def record(ctx: NodeRunContext) -> None:
        shared = ctx.shared(Progress)
        assert_type(shared, Progress)
        shared.complete(str(ctx.node_path))

    async def record_async(ctx: NodeRunContext) -> None:
        record(ctx)

    def expand(ctx: NodeRunContext) -> GraphStructure:
        record(ctx)
        return Graph(structure=GraphStructure(nodes=(Step(record_async),))).structure

    graph = Graph(
        structure=GraphStructure(
            nodes=(
                *(Step(record) for _ in range(12)),
                Graph(structure=GraphStructure(nodes=(Step(record),))),
                Step(expand),
            )
        )
    )
    # 同一份声明执行两次，各次显式提供的对象互不污染。
    for _ in range(2):
        progress = Progress()
        execution = Execution(root=Graph(structure=graph.structure), shared=progress)
        match executor:
            case "serial":
                SerialGraphExecutor(shared=progress).execute(execution.compile())
            case "threads":
                run_execution(execution, max_workers=4, mode=mode)
            case "async":
                run_async_execution(execution, max_workers=4, mode=mode)
        assert progress.completed == {
            *(f"0.{index}" for index in range(12)),
            "0.12.0",
            "0.13",
            "0.13.0",
        }
        assert execution.execution_state.results is None


def test_shared_type_check_is_an_explicit_boundary() -> None:
    context = NodeRunContext(
        node_id=NodeId(),
        node_path=NodePath((0,)),
        control=NodeControl(),
        _shared=Progress(),
    )
    assert_type(context.shared(Progress), Progress)
    with pytest.raises(TypeError, match="expected str, got Progress"):
        context.shared(str)


def test_missing_shared_state_is_not_created_implicitly() -> None:
    context = NodeRunContext(
        node_id=NodeId(),
        node_path=NodePath((0,)),
        control=NodeControl(),
    )
    with pytest.raises(RuntimeError, match="no shared state was provided"):
        context.shared(Progress)
