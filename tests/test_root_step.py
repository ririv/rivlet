"""验证根 Node 与内部节点共用身份、条件、生命周期及日志规则。"""

from __future__ import annotations

import asyncio
import sys
from functools import partial
from pathlib import Path
from typing import Literal

import pytest
from execution_support import run_async_execution

from rivlet import (
    ActionFailed,
    Execution,
    ExecutionState,
    Graph,
    GraphError,
    GraphMode,
    GraphStructure,
    NodeExecutionEvent,
    NodeExecutionState,
    NodePath,
    NodeRunContext,
    ResultStore,
    SerialGraphExecutor,
    Step,
    SubgraphFailed,
    export_graph,
    render_execution,
)
from rivlet import arun as arun_execution
from rivlet import run as run_execution

type Backend = Literal["serial", "thread", "async"]


def execute(execution: Execution, backend: Backend, mode: GraphMode) -> None:
    if backend == "serial":
        SerialGraphExecutor(
            execution.execution_state,
            record_composites_as_steps=mode == "dynamic",
        ).execute(execution.compile())
    else:
        (
            partial(run_async_execution, max_workers=2)
            if backend == "async"
            else partial(run_execution, max_workers=2)
        )(execution, mode=mode)


class Events:
    def __init__(self) -> None:
        self.items: list[NodeExecutionEvent] = []

    def emit(self, event: NodeExecutionEvent) -> None:
        self.items.append(event)


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_step_root_is_the_executed_declaration(
    backend: Backend, mode: GraphMode
) -> None:
    contexts: list[NodeRunContext] = []

    def action(ctx: NodeRunContext) -> int:
        contexts.append(ctx)
        return 42

    root = Step(action, label=("single action"), condition=(lambda: True))
    events = Events()
    execution = Execution(
        root,
        events=events,
        execution_state=ExecutionState(results=ResultStore()),
    )
    compilation = execution.compile()
    assert compilation.composites == ()
    assert len(compilation.steps) == 1
    assert compilation.steps[0].id == root.id
    execute(execution, backend, mode)
    assert execution.require_result(root).unwrap() == 42
    assert contexts[0].node_id == root.id
    assert contexts[0].node_path == NodePath((0,))
    assert contexts[0].node_path.labels == ("single action",)
    assert [(event.node_id, event.state) for event in events.items] == [
        (root.id, NodeExecutionState.STARTED),
        (root.id, NodeExecutionState.COMPLETED),
    ]


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_false_root_condition_skips_the_entire_graph(
    backend: Backend, mode: GraphMode
) -> None:
    calls: list[str] = []
    child = Step(lambda: calls.append("child"), label=("child"))
    root = Graph(
        structure=GraphStructure(nodes=(child,)),
        label="pipeline",
        condition=lambda: False,
    )
    events = Events()
    execution = Execution(root, events=events)
    compiled = execution.compile()
    assert len(compiled.steps) == len(compiled.composites) == 1
    assert compiled.composites[0].id == root.id
    execute(execution, backend, mode)
    assert not calls
    assert execution.execution_state.skipped_steps == {
        NodePath((0,)),
        NodePath((0, 0)),
    }
    assert any(
        event.node_id == root.id and event.state is NodeExecutionState.SKIPPED
        for event in events.items
    )
    rendered = render_execution(root, execution.execution_state)
    assert "pipeline [graph]" in rendered
    assert "condition=false" in rendered


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_root_failure_keeps_its_direct_child_as_the_witness(backend: Backend) -> None:
    def fail() -> None:
        raise ValueError("failure")

    child = Step(fail, label=("child"))
    root = Graph(structure=GraphStructure(nodes=(child,)), label="pipeline")
    events = Events()
    execution = Execution(root, events=events)
    with pytest.raises(ValueError, match="failure"):
        execute(execution, backend, "compiled")
    causes = {
        event.node_path: event.state_detail
        for event in events.items
        if event.state is NodeExecutionState.FAILED
    }
    assert causes == {
        NodePath((0, 0)): ActionFailed(),
        NodePath((0,)): SubgraphFailed(NodePath((0, 0))),
    }


def test_root_condition_cannot_wait_for_its_own_descendant() -> None:
    child = Step(lambda: True)
    root = Graph(
        structure=GraphStructure(nodes=(child,)),
        condition=(lambda value: value.unwrap(), child),
    )
    with pytest.raises(GraphError, match="outside this graph"):
        Execution(root)


def test_root_identity_is_preserved_in_declaration_and_compiled_exports() -> None:
    root = Graph(structure=GraphStructure(), label="empty")
    execution = Execution(root)
    declaration = export_graph(root)
    compiled = export_graph(execution.compile())
    assert declaration["nodes"] == compiled["nodes"]
    assert len(compiled["nodes"]) == 1
    assert compiled["nodes"][0]["node_id"] == root.id.value
    assert compiled["nodes"][0]["label"] == "empty"
    assert execution.compile().steps == ()
    assert len(execution.compile().composites) == 1


def test_async_step_root_runs_in_the_callers_loop() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()

        async def action() -> object:
            assert asyncio.get_running_loop() is loop
            return loop

        root = Step(action)
        execution = Execution(
            root, execution_state=ExecutionState(results=ResultStore())
        )
        await arun_execution(execution)
        assert execution.require_result(root).unwrap() is loop

    asyncio.run(scenario())


def test_runtime_graph_returned_by_step_root_keeps_the_same_root_identity() -> None:
    contexts: list[NodeRunContext] = []
    child = Step(lambda ctx: contexts.append(ctx), label=("child"))
    root = Step(
        lambda: Graph(structure=GraphStructure(nodes=(child,))).structure,
        label=("expand"),
    )
    events = Events()
    execution = Execution(root, events=events)
    assert len(execution.compile().steps) == 1
    assert execution.compile().composites == ()
    run_execution(execution)
    assert contexts[0].node_id == child.id
    assert contexts[0].node_path == NodePath((0, 0))
    assert contexts[0].node_path.labels == ("expand", "child")
    assert [event.state for event in events.items if event.node_id == root.id] == [
        NodeExecutionState.STARTED,
        NodeExecutionState.COMPLETED,
    ]


@pytest.mark.parametrize("enabled", [False, True])
def test_native_graph_root_uses_the_outer_steps_condition_and_label(
    tmp_path: Path, enabled: bool
) -> None:
    from rivlet._native import _rust_process_graph

    output = tmp_path / "result"
    native = _rust_process_graph(
        [
            (
                "native leaf",
                sys.executable,
                [
                    "-c",
                    "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                    str(output),
                ],
                str(tmp_path),
                {},
            )
        ],
        [],
    )
    root = Graph(structure=native, label="native root", condition=lambda: enabled)
    execution = Execution(root)
    assert execution.compile().composites[0].id == root.id
    assert execution.compile().composites[0].graph_label == "native root"
    run_execution(execution)
    assert output.exists() is enabled


def test_log_binding_can_reference_the_root_declaration(tmp_path: Path) -> None:
    root = Step(lambda: None, label=("root"))
    log_path = tmp_path / "root.log"
    execution = Execution(root, node_log_paths={root: log_path})
    assert execution.execution_state.get_step_log_path(NodePath((0,))) == log_path
