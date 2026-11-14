"""声明身份、挂载路径、标签和结果隔离必须保持一致。"""

import sys
from functools import partial

import pytest
from execution_support import Invoke, run_async_execution

from rivlet import (
    Execution,
    Graph,
    GraphMode,
    GraphStructure,
    NodeExecutionEvent,
    NodeId,
    NodePath,
    NodeRunContext,
    Option,
    Results,
    Step,
    export_graph,
)
from rivlet import run as run_execution
from rivlet._native import _rust_process_graph


def test_path_labels_do_not_change_identity_and_parent_preserves_labels():
    path = NodePath((2, 1), labels=("stage", "configure"))
    renamed = NodePath((2, 1), labels=("other", "name"))
    assert path == renamed
    assert hash(path) == hash(renamed)
    assert {path: "result"}[renamed] == "result"
    assert path.labels == ("stage", "configure")
    assert path.parent is not None
    assert path.parent.segments == (2,)
    assert path.parent.labels == ("stage",)
    assert path.parent.parent is None
    assert path.parent.child(1, label="configure").labels == path.labels


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        partial(run_execution, max_workers=2),
        partial(run_async_execution, max_workers=2),
    ],
)
def test_repeated_mounts_share_declaration_id_not_results(
    mode: GraphMode, options: Invoke
):
    received: list[tuple[NodeId, NodePath, object]] = []
    events: list[NodeExecutionEvent] = []

    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    def produce(ctx: NodeRunContext) -> tuple[int, ...]:
        return ctx.node_path.segments

    def consume(ctx: NodeRunContext, result: Option[object]) -> None:
        received.append((ctx.node_id, ctx.node_path, result.unwrap()))

    producer = Step(produce, label=("source"))
    consumer = Step(consume, label=("use"))
    child = Graph.from_wiring(
        (consumer.wire(after=(Results(producer),)), producer.wire())
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(
                Graph(structure=child.structure, label="left"),
                Graph(structure=child.structure, label="right"),
            )
        )
    )
    execution = Execution(Graph(structure=graph.structure), events=Sink())
    (options)(execution, mode=mode)
    assert producer.id != consumer.id
    assert {identity for identity, _, _ in received} == {consumer.id}
    assert {(path.segments, result) for _, path, result in received} == {
        ((0, 0, 0), (0, 0, 1)),
        ((0, 1, 0), (0, 1, 1)),
    }
    assert {path.labels for _, path, _ in received} == {
        ("0", "left", "use"),
        ("0", "right", "use"),
    }
    assert {
        event.node_path.labels for event in events if event.node_id == consumer.id
    } == {
        ("0", "left", "use"),
        ("0", "right", "use"),
    }
    first = export_graph(execution.compile())
    second = export_graph(Execution(execution.root).compile())
    assert first == second
    assert [node["node_id"] for node in first["nodes"] if node["label"] == "use"] == [
        consumer.id.value,
        consumer.id.value,
    ]


def test_dynamic_child_keeps_identity_and_full_path_labels():
    seen: list[NodeRunContext] = []

    def record(ctx: NodeRunContext) -> None:
        seen.append(ctx)

    step = Step(record, label=("leaf"))
    child = Graph(structure=GraphStructure(nodes=(step,)))
    graph = Graph(
        structure=GraphStructure(nodes=(Step(lambda: child.structure, label="expand"),))
    )
    run_execution(Execution(Graph(structure=graph.structure)))
    assert seen[0].node_id == step.id
    assert seen[0].node_path.segments == (0, 0, 0)
    assert seen[0].node_path.labels == ("0", "expand", "leaf")
    assert seen[0].node_path.parent is not None
    assert seen[0].node_path.parent.labels == ("0", "expand")


def test_native_declaration_ids_survive_compilation_and_repeated_mounts():
    native = _rust_process_graph(
        [("leaf", sys.executable, ["-c", "pass"], ".", {})], []
    )
    python_step = Step(lambda: None)
    graph = Graph(
        structure=GraphStructure(
            nodes=(
                Graph(structure=native, label="first"),
                Graph(structure=native, label="second"),
                python_step,
            )
        )
    )
    root = Graph(structure=graph.structure)
    first = export_graph(Execution(root).compile())
    second = export_graph(Execution(root).compile())
    assert first == second
    execution_nodes = [
        node
        for node in first["nodes"]
        if node["kind"] == "leaf" and node["action_kind"] == "native"
    ]
    assert [node["id"] for node in execution_nodes] == ["0.0.0", "0.1.0"]
    assert [node["node_id"] for node in execution_nodes] == [native.node_ids()[0]] * 2
    assert native.node_ids()[0] not in {step.id.value for step in graph.structure.nodes}
    assert NodeId().value not in {step.id.value for step in graph.structure.nodes}
