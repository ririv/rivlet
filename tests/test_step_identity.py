"""声明身份、挂载路径、标签和结果隔离必须保持一致。"""

import sys

import pytest

from rivlet import (
    AsyncRunOptions,
    Execution,
    Graph,
    GraphMode,
    Option,
    Step,
    StepExecutionEvent,
    StepId,
    StepPath,
    StepRunContext,
    ThreadRunOptions,
    export_graph,
)
from rivlet._native import _native_process_graph


def test_path_labels_do_not_change_identity_and_parent_preserves_labels():
    path = StepPath((2, 1), labels=("stage", "configure"))
    renamed = StepPath((2, 1), labels=("other", "name"))
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
    "options", [ThreadRunOptions(workers=2), AsyncRunOptions(concurrency=2)]
)
def test_repeated_mounts_share_declaration_id_not_results(
    mode: GraphMode, options: ThreadRunOptions | AsyncRunOptions
):
    received: list[tuple[StepId, StepPath, object]] = []
    events: list[StepExecutionEvent] = []

    class Sink:
        def emit(self, event: StepExecutionEvent) -> None:
            events.append(event)

    def produce(ctx: StepRunContext) -> tuple[int, ...]:
        return ctx.step_path.segments

    def consume(ctx: StepRunContext, result: Option[object]) -> None:
        received.append((ctx.step_id, ctx.step_path, result.unwrap()))

    producer = Step(produce, label="source")
    consumer = Step(consume, label="use")
    child = Graph.from_wiring((consumer.wire().results(producer), producer.wire()))
    graph = Graph(steps=(child.step(label="left"), child.step(label="right")))
    execution = Execution(Step(graph), events=Sink())
    execution.run(options, mode=mode)
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
        event.step_path.labels for event in events if event.step_id == consumer.id
    } == {
        ("0", "left", "use"),
        ("0", "right", "use"),
    }
    first = export_graph(execution.compile())
    second = export_graph(Execution(execution.root).compile())
    assert first == second
    assert [node["step_id"] for node in first["nodes"] if node["label"] == "use"] == [
        consumer.id.value,
        consumer.id.value,
    ]


def test_dynamic_child_keeps_identity_and_full_path_labels():
    seen: list[StepRunContext] = []

    def record(ctx: StepRunContext) -> None:
        seen.append(ctx)

    leaf = Step(record, label="leaf")
    child = Graph(steps=(leaf,))
    graph = Graph(steps=(Step(lambda: child, label="expand"),))
    Execution(Step(graph)).run()
    assert seen[0].step_id == leaf.id
    assert seen[0].step_path.segments == (0, 0, 0)
    assert seen[0].step_path.labels == ("0", "expand", "leaf")
    assert seen[0].step_path.parent is not None
    assert seen[0].step_path.parent.labels == ("0", "expand")


def test_native_declaration_ids_survive_compilation_and_repeated_mounts():
    native = _native_process_graph(
        [("leaf", sys.executable, ["-c", "pass"], ".", {})], []
    )
    python_step = Step(lambda: None)
    graph = Graph(
        steps=(Step(native, label="first"), Step(native, label="second"), python_step)
    )
    root = Step(graph)
    first = export_graph(Execution(root).compile())
    second = export_graph(Execution(root).compile())
    assert first == second
    native_nodes = [
        node
        for node in first["nodes"]
        if node["kind"] == "step" and node["action_kind"] == "native"
    ]
    assert [node["id"] for node in native_nodes] == ["0.0.0", "0.1.0"]
    assert [node["step_id"] for node in native_nodes] == [native.step_ids()[0]] * 2
    assert native.step_ids()[0] not in {step.id.value for step in graph.steps}
    assert StepId().value not in {step.id.value for step in graph.steps}
