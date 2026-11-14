"""Typed Rust payloads and explicit language conversions on real result edges."""

from __future__ import annotations

import json
from functools import partial
from typing import Literal

import pytest
from execution_support import run_async_execution

from rivlet import (
    Edge,
    Execution,
    ExecutionState,
    Graph,
    GraphError,
    GraphInterface,
    GraphNodeFailure,
    GraphStructure,
    NodePath,
    ResultStore,
    SerialGraphExecutor,
    Step,
    export_graph,
    render_graph,
    render_graph_mermaid,
)
from rivlet import run as run_execution
from rivlet._native import (
    _compose_rust_graph,
    _rust_result_fixture,
)
from rivlet._native import (
    _ResultFixtureKind as Kind,
)

type Backend = Literal["serial", "thread", "async"]


def run(execution: Execution, backend: Backend) -> None:
    if backend == "serial":
        SerialGraphExecutor(execution.execution_state).execute(execution.compile())
    else:
        (
            partial(run_execution, max_workers=3)
            if backend == "thread"
            else partial(run_async_execution, max_workers=3)
        )(execution)


CASES = (
    (Kind.I64, 42),
    (Kind.U64, 2**64 - 1),
    (Kind.Bool, True),
    (Kind.F64, 1.25),
    (Kind.String, "native"),
    (Kind.Bytes, b"\x00\xff"),
    (Kind.Custom, {"value": 42}),
)


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize(("kind", "expected"), CASES)
def test_rust_to_rust_to_python_preserves_actual_typed_values(
    kind: Kind, expected: object, backend: Backend
) -> None:
    source, target = _rust_result_fixture(kind)
    body = _compose_rust_graph(
        [("producer", source), ("consumer", target)],
        [([(0, "value")], (1, "apply"))],
        [],
        [("result", 1, "value")],
    )
    native = Graph(body, label="native")
    seen: list[object] = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()), label="sink")
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(native, sink),
                edges=Edge.results((native.interface.outputs["result"],), sink),
            )
        )
    )
    run(execution, backend)
    assert seen == [expected]
    assert type(seen[0]) is type(expected)
    declaration = export_graph(execution.root)
    result_edges = [edge for edge in declaration["edges"] if edge["kind"] == "result"]
    assert len(result_edges) == 2
    assert result_edges[0]["provider_node"] == "0.0.0.0"
    assert result_edges[0]["consumer_nodes"] == ["0.0.1.0"]
    assert "action input: 0" in render_graph(execution.root, root_label="root")
    assert "action input 0" in render_graph_mermaid(execution.root, root_label="root")


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize(("kind", "value"), CASES)
def test_python_to_rust_and_back_uses_receiver_type(
    kind: Kind, value: object, backend: Backend
) -> None:
    _, body = _rust_result_fixture(kind)
    native = Graph(body, label="native")
    producer = Step(lambda: value)
    seen: list[object] = []
    sink = Step(lambda ctx, result: seen.append(result.unwrap()))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(producer, native, sink),
                edges=(
                    *Edge.results((producer,), native.interface.inputs["apply"]),
                    *Edge.results((native.interface.outputs["value"],), sink),
                ),
            )
        )
    )
    run(execution, backend)
    assert seen == [value]
    assert type(seen[0]) is type(value)


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_custom_native_payload_needs_no_codec_for_native_only_edges(
    backend: Backend,
) -> None:
    source, target = _rust_result_fixture(Kind.Opaque)
    body = _compose_rust_graph(
        [("source", source), ("target", target)],
        [([(0, "value")], (1, "apply"))],
        [],
        [],
    )
    execution = Execution(Graph(body))
    run(execution, backend)
    assert execution.execution_state.results is None


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize("skipped", (False, True))
def test_native_option_distinguishes_skipped_from_successful_unit(
    skipped: bool, backend: Backend
) -> None:
    source, target = _rust_result_fixture(Kind.Unit)
    producer = Graph(source, condition=(lambda: False) if skipped else None)
    consumer = Graph(target)
    seen: list[object] = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(producer, consumer, sink),
                edges=(
                    *Edge.results(
                        (producer.interface.outputs["value"],),
                        consumer.interface.inputs["apply"],
                    ),
                    *Edge.results((consumer.interface.outputs["value"],), sink),
                ),
            )
        )
    )
    run(execution, backend)
    assert seen == [not skipped]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize(
    ("kind", "bad_value"),
    (
        (Kind.I64, True),
        (Kind.I64, "42"),
        (Kind.I64, 2**100),
        (Kind.Bytes, "bytes"),
        (Kind.Bool, 1),
        (Kind.Custom, {"other": 42}),
    ),
)
def test_bad_python_import_fails_receiver_only(
    kind: Kind, bad_value: object, backend: Backend
) -> None:
    _, body = _rust_result_fixture(kind)
    native = Graph(body, label="receiver")
    producer = Step(lambda: bad_value)
    seen: list[str] = []
    independent = Step(lambda: seen.append("independent"))
    blocked = Step(lambda: seen.append("blocked"))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(producer, native, independent, blocked),
                edges=(
                    *Edge.results((producer,), native.interface.inputs["apply"]),
                    Edge(native, blocked),
                ),
            )
        )
    )
    with pytest.raises(RuntimeError):
        run(execution, backend)
    assert seen == ["independent"]
    failures = execution.execution_state.node_failures
    assert failures[NodePath((0, 1, 0))] is GraphNodeFailure.FAILED
    assert failures[NodePath((0, 3))] is GraphNodeFailure.BLOCKED


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_unregistered_native_export_fails_python_reader_not_native_producer(
    backend: Backend,
) -> None:
    source, _ = _rust_result_fixture(Kind.Opaque)
    native = Graph(source)
    reader = Step(lambda ctx, value: value.unwrap())
    seen: list[str] = []
    independent = Step(lambda: seen.append("independent"))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(native, reader, independent),
                edges=Edge.results((native.interface.outputs["value"],), reader),
            )
        )
    )
    with pytest.raises(TypeError, match="no Python export codec"):
        run(execution, backend)
    assert seen == ["independent"]
    assert (
        execution.execution_state.node_failures[NodePath((0, 1))]
        is GraphNodeFailure.FAILED
    )
    assert NodePath((0, 0, 0)) not in execution.execution_state.node_failures


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_forwarded_reused_consumers_keep_values_bound_to_each_mount(
    backend: Backend,
) -> None:
    _, body = _rust_result_fixture(Kind.Custom)
    inner = Graph(body, label="inner")
    shared = GraphStructure(
        nodes=(inner,), interface=GraphInterface(inputs=inner, outputs=inner)
    )
    left, right = Graph(shared, label="left"), Graph(shared, label="right")
    first, second = Step(lambda: {"value": 1}), Step(lambda: {"value": 2})
    seen: list[object] = []
    sink = Step(lambda ctx, a, b: seen.extend((a.unwrap(), b.unwrap())))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(first, second, left, right, sink),
                edges=(
                    *Edge.results((first,), left.interface.inputs["apply"]),
                    *Edge.results((second,), right.interface.inputs["apply"]),
                    *Edge.results(
                        (
                            right.interface.outputs["value"],
                            left.interface.outputs["value"],
                        ),
                        sink,
                    ),
                ),
            )
        )
    )
    run(execution, backend)
    assert seen == [{"value": 2}, {"value": 1}]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_custom_export_is_cached_for_fanout_and_retained_results(
    backend: Backend,
) -> None:
    body, _ = _rust_result_fixture(Kind.Custom)
    native = Graph(body)
    seen: list[object] = []
    one = Step(lambda ctx, value: seen.append(value.unwrap()))
    two = Step(lambda ctx, value: seen.append(value.unwrap()))
    graph = Graph(
        GraphStructure(
            nodes=(native, one, two),
            edges=(
                *Edge.results((native.interface.outputs["value"],), one),
                *Edge.results((native.interface.outputs["value"],), two),
            ),
        )
    )
    execution = Execution(graph, execution_state=ExecutionState(results=ResultStore()))
    run(execution, backend)
    assert seen == [{"value": 42}, {"value": 42}]
    assert seen[0] is seen[1]
    results = execution.execution_state.results
    assert results is not None
    assert results.require(NodePath((0, 0, 0))).value is seen[0]
    assert '"value": 42' not in json.dumps(export_graph(execution.compile()))


def test_known_native_result_type_mismatch_is_rejected_before_execution() -> None:
    source, _ = _rust_result_fixture(Kind.Bool)
    _, target = _rust_result_fixture(Kind.I64)
    first, second = Graph(source), Graph(target)
    graph = Graph(
        GraphStructure(
            nodes=(first, second),
            edges=Edge.results(
                (first.interface.outputs["value"],), second.interface.inputs["apply"]
            ),
        )
    )
    with pytest.raises(GraphError, match="native result type mismatch"):
        Execution(graph).compile()
