"""Run against a separately built demo_native wheel, not a library-private fixture."""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import demo_native
import pytest

from rivlet import (
    Edge,
    Execution,
    ExecutionState,
    Graph,
    GraphError,
    Nothing,
    ResultStore,
    SerialGraphExecutor,
    Step,
    arun,
    export_graph,
    render_graph,
    render_graph_mermaid,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable

type Backend = Literal["serial", "thread", "async"]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_recompose_actual_nodes_edges_conditions_and_forwarded_ports(
    backend: Backend,
) -> None:
    native = demo_native.make_port_conditions()
    assert native.nodes is native.nodes
    assert native.edges is native.edges
    target, forwarded = native.nodes
    assert isinstance(target, Step) and isinstance(forwarded, Graph)
    assert target.condition is not None
    port = target.condition.inputs[0]
    assert port == forwarded.interface.output
    assert native.edges[0].result_source == port
    assert native.edges[0].result_target is target
    assert native.interface.declaration.outputs[None] is target
    child = forwarded.nodes[0]
    assert isinstance(child, Graph)
    assert forwarded.interface.declaration.outputs[None] == child.interface.output
    copy = Graph(
        nodes=native.nodes, edges=native.edges, interface=native.interface.declaration
    )
    original = export_graph(native)
    rebuilt = export_graph(copy)
    for field in ("nodes", "edges", "conditions", "graphs"):
        assert len(original[field]) == len(rebuilt[field])
    seen = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()))
    root = Graph(nodes=(copy, sink), edges=Edge.results((copy.interface.output,), sink))
    execute(Execution(root), backend)
    assert seen == [42]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_opaque_conditional_graph_keeps_native_batch_import(backend: Backend) -> None:
    from rivlet.model.native_graph import _NativeContents

    for number in (2, -3):
        native = demo_native.make_conditions()
        native = Graph(nodes=(native,), interface=native)
        source = Step(lambda number=number: number)
        seen = []
        sink = Step(lambda ctx, value, seen=seen: seen.append(value.unwrap()))
        root = Graph(
            nodes=(source, native, sink),
            edges=(
                *Edge.results((source,), native.interface.input),
                *Edge.results((native.interface.outputs["sum"],), sink),
            ),
        )
        child = native.nodes[0]
        assert isinstance(child, Graph)
        contents = child._contents
        assert isinstance(contents, _NativeContents)
        assert contents._nodes is None and contents._edges is None
        execute(Execution(root), backend)
        assert seen == [number * 3]
        assert contents._nodes is None and contents._edges is None


def execute(execution: Execution, backend: Backend) -> None:
    match backend:
        case "serial":
            SerialGraphExecutor(execution.execution_state).execute(execution.compile())
        case "thread":
            run(execution, max_workers=2)
        case "async":
            asyncio.run(arun(execution, max_workers=2))
        case _:
            raise AssertionError(backend)


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize(("value", "expected"), ((21, 42), (-1, None)))
def test_independent_rust_action_and_predicate_receive_python_values(
    backend: Backend, value: int, expected: int | None
) -> None:
    prepare = Step(lambda: value, label="prepare")
    native = demo_native.make_graph()
    assert isinstance(native, Graph)
    assert export_graph(native)["conditions"]
    seen = []
    sink = Step(
        lambda ctx, result: seen.append(
            None if isinstance(result, Nothing) else result.unwrap()
        ),
        label="sink",
    )
    execution = Execution(
        Graph(
            nodes=(prepare, native, sink),
            edges=(
                *Edge.results((prepare,), native.interface.input),
                *Edge.results((native.interface.output,), sink),
            ),
        )
    )
    compilation = execution.compile()
    assert "rust" in render_graph(execution.root, root_label="workflow")
    assert "subgraph" in render_graph_mermaid(execution.root, root_label="workflow")
    assert export_graph(compilation)["conditions"]
    execute(execution, backend)
    assert seen == [expected]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_opaque_nonclone_payload_crosses_graphs_without_serialization(
    backend: Backend,
) -> None:
    producer, consumer = demo_native.make_payload_pair()
    seen = []
    sink = Step(lambda ctx, result: seen.append(result.unwrap()))
    execution = Execution(
        Graph(
            nodes=(producer, consumer, sink),
            edges=(
                *Edge.results(
                    (producer.interface.output, producer.interface.output),
                    consumer.interface.input,
                ),
                *Edge.results((consumer.interface.output,), sink),
            ),
        )
    )
    execute(execution, backend)
    # Rust 接收方检查 Arc::ptr_eq；不能用 JSON round-trip 或复制载荷伪造此结果。
    assert seen == [42]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_errors_do_not_cancel_an_independent_python_branch(
    backend: Backend,
) -> None:
    native = demo_native.make_failures()
    calls = []
    independent = Step(lambda: calls.append("independent"))
    execution = Execution(Graph(nodes=(native, independent)))
    with pytest.raises(ExceptionGroup) as caught:
        execute(execution, backend)
    messages = [str(error) for error in caught.value.exceptions]
    assert any("example action failed" in error for error in messages)
    assert any("example predicate failed" in error for error in messages)
    assert calls == ["independent"]


def test_exported_nested_views_can_be_remounted_and_reused() -> None:
    body = demo_native.make_nested()
    first, second = (
        Graph(
            nodes=(body.nodes),
            edges=(body.edges),
            observation=(body.observation),
            static=(body.static),
            static_selections=(body.static_selections),
            interface=body.interface.declaration,
            label=("one"),
        ),
        Graph(
            nodes=(body.nodes),
            edges=(body.edges),
            observation=(body.observation),
            static=(body.static),
            static_selections=(body.static_selections),
            interface=body.interface.declaration,
            label=("two"),
        ),
    )
    execution = Execution((first | second).graph())
    run(execution, max_workers=2)
    assert len(execution.compile().steps) == 4
    # 只读结构中的嵌套 scope 可重新挂载；导出者重新投影，不带入原父图连线。
    child = body.nodes[0]
    assert isinstance(child, Graph)
    child_execution = Execution(child)
    run(child_execution)
    assert len(child_execution.compile().steps) == 1


def test_result_lease_outlives_the_factory_and_graph_variables() -> None:
    producer, consumer = demo_native.make_payload_pair()
    store = ResultStore()
    execution = Execution(
        Graph(
            nodes=(producer, consumer),
            edges=(
                *Edge.results(
                    (producer.interface.output, producer.interface.output),
                    consumer.interface.input,
                ),
            ),
        ),
        execution_state=ExecutionState(results=store),
    )
    output_path = execution.compile().steps[-1].path
    run(execution)
    del execution, producer, consumer
    gc.collect()
    assert store.require(output_path).value == 42


def test_custom_value_has_no_implicit_python_conversion() -> None:
    producer, _ = demo_native.make_payload_pair()
    sink = Step(lambda ctx, value: value.unwrap())
    execution = Execution(
        Graph(
            nodes=(producer, sink),
            edges=Edge.results((producer.interface.output,), sink),
        )
    )
    with pytest.raises(TypeError, match="codec"):
        run(execution)


def test_separate_libraries_keep_custom_types_distinct_and_transfer_builtins(
    tmp_path: Path,
) -> None:
    # 复制真实扩展产物并加载第二份映像，验证不依赖跨库 TypeId、Rust 布局或类型名。
    import demo_native.demo_native as native_library

    library = Path(native_library.__file__)
    second = tmp_path / library.name
    shutil.copyfile(library, second)
    spec = importlib.util.spec_from_file_location("demo_native", second)
    assert spec is not None and spec.loader is not None
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    producer, _ = demo_native.make_payload_pair()
    _, consumer = other.make_payload_pair()
    execution = Execution(
        Graph(
            nodes=(producer, consumer),
            edges=Edge.results(
                (producer.interface.output, producer.interface.output),
                consumer.interface.input,
            ),
        )
    )
    with pytest.raises(GraphError, match="type mismatch"):
        execution.compile()

    prepare = Step(lambda: 21)
    first, second = demo_native.make_graph(), other.make_graph()
    seen: list[int] = []
    sink = Step(lambda ctx, result: seen.append(result.unwrap()))
    run(
        Graph(
            nodes=(prepare, first, second, sink),
            edges=(
                *Edge.results((prepare,), first.interface.input),
                *Edge.results((first.interface.output,), second.interface.input),
                *Edge.results((second.interface.output,), sink),
            ),
        )
    )
    assert seen == [84]


def test_borrowed_thread_pool_can_drive_the_same_native_graph() -> None:
    source = Step(lambda: 2)
    native = demo_native.make_graph()
    seen = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()))
    root = Graph(
        nodes=(source, native, sink),
        edges=(
            *Edge.results((source,), native.interface.input),
            *Edge.results((native.interface.output,), sink),
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        Execution(root).run(pool=pool)
    assert seen == [4]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_conditions_keep_mounts_and_expanded_edge_positions(backend: Backend) -> None:
    body = demo_native.make_conditions()
    snapshot = export_graph(body)
    assert [edge["kind"] for edge in snapshot["edges"]] == [
        "result",
        "result",
        "result",
        "conditional",
        "port",
    ]
    assert len(snapshot["conditions"]) == 3
    # 快照是分离的数据；修改它不会修改可执行声明。
    snapshot["nodes"].clear()
    assert len(body.nodes) == 6

    def source_node(value: int) -> Step:
        return Step(lambda: value)

    def sink_node(seen: list[int]) -> Step:
        return Step(lambda ctx, result: seen.append(result.unwrap()))

    for values in ((2, -3), (-4, 5)):
        sources = tuple(source_node(value) for value in values)
        mounts = tuple(
            Graph(
                nodes=body.nodes,
                edges=body.edges,
                interface=body.interface.declaration,
                label=f"mount-{index}",
            )
            for index in range(2)
        )
        seen: list[int] = []
        sinks = tuple(sink_node(seen) for _ in values)
        root = Graph(
            nodes=(*sources, *mounts, *sinks),
            edges=tuple(
                edge
                for source, mount, sink in zip(sources, mounts, sinks, strict=True)
                for edge in (
                    *Edge.results((source,), mount.interface.input),
                    *Edge.results((mount.interface.outputs["sum"],), sink),
                )
            ),
        )
        execution = Execution(root)
        assert len(export_graph(execution.compile())["conditions"]) == 6
        execute(execution, backend)
        assert sorted(seen) == sorted(value * 3 for value in values)


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_exported_module_attributes_are_concrete_nodes(backend: Backend) -> None:
    assert isinstance(demo_native.double, Step)
    assert isinstance(demo_native.pipeline, Graph)
    assert demo_native.double.label == "double"
    assert demo_native.pipeline.label == "rust"
    source = Step(lambda: 21)
    seen = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()))
    root = Graph(
        nodes=(source, demo_native.double, demo_native.pipeline, sink),
        edges=(
            *Edge.results((source,), demo_native.double),
            *Edge.results((demo_native.double,), demo_native.pipeline.interface.input),
            *Edge.results((demo_native.pipeline.interface.output,), sink),
        ),
    )
    execution = Execution(root)
    document = export_graph(execution.compile())
    assert any(
        node["node_id"] == str(demo_native.double.id.value)
        for node in document["nodes"]
    )
    declaration = export_graph(root)
    assert (
        next(
            node
            for node in declaration["nodes"]
            if node["node_id"] == str(demo_native.double.id.value)
        )["action_kind"]
        == "native"
    )
    assert "double" in render_graph(root, root_label="workflow")
    assert "double" in render_graph_mermaid(root, root_label="workflow")
    execute(execution, backend)
    assert seen == [84]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
@pytest.mark.parametrize("allowed", (False, True))
@pytest.mark.parametrize(
    "factory", (demo_native.make_conditional_step, demo_native.make_conditional_graph)
)
def test_exported_root_keeps_its_condition_and_identity(
    backend: Backend, allowed: bool, factory: Callable[[bool], Step | Graph]
) -> None:
    native = factory(allowed)
    assert native.condition is not None
    assert native.condition.inputs == ()
    seen = []
    sink = Step(
        lambda ctx, value: seen.append(
            None if isinstance(value, Nothing) else value.unwrap()
        )
    )
    output = native.interface.output if isinstance(native, Graph) else native
    root = Graph(nodes=(native, sink), edges=Edge.results((output,), sink))
    execution = Execution(root)
    assert export_graph(root)["conditions"]
    assert export_graph(execution.compile())["conditions"]
    execute(execution, backend)
    assert seen == [21 if allowed else None]
    # 同一个完整根可以直接执行；没有额外空动作或包装 Graph。
    direct = Execution(native)
    assert export_graph(direct.compile())["nodes"][0]["node_id"] == str(native.id.value)
    execute(direct, backend)


def test_graph_structure_reuse_does_not_copy_root_metadata() -> None:
    native = demo_native.make_conditional_graph(False)
    left = Graph(
        nodes=(native.nodes),
        edges=(native.edges),
        observation=(native.observation),
        static=(native.static),
        static_selections=(native.static_selections),
        interface=native.interface.declaration,
        label=("left"),
    )
    right = Graph(
        nodes=(native.nodes),
        edges=(native.edges),
        observation=(native.observation),
        static=(native.static),
        static_selections=(native.static_selections),
        interface=native.interface.declaration,
        label=("right"),
    )
    assert left is not right
    assert left.nodes[0] is right.nodes[0]
    assert len({native.id, left.id, right.id}) == 3
    assert left.condition is None and right.condition is None
    seen = []
    sink = Step(lambda ctx, a, b: seen.append((a.unwrap(), b.unwrap())))
    root = Graph(
        nodes=(left, right, sink),
        edges=Edge.results((left.interface.output, right.interface.output), sink),
    )
    run(root, max_workers=2)
    assert seen == [(21, 21)]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_root_condition_can_be_explicitly_forwarded(backend: Backend) -> None:
    native = demo_native.make_conditional_graph(False)
    assert native.condition is not None
    copied = Graph(
        nodes=(native.nodes),
        edges=(native.edges),
        observation=(native.observation),
        static=(native.static),
        static_selections=(native.static_selections),
        interface=native.interface.declaration,
        condition=(native.condition.as_input()),
        label=("copy"),
    )
    assert copied.condition is native.condition
    seen = []
    sink = Step(lambda ctx, value: seen.append(isinstance(value, Nothing)))
    root = Graph(
        nodes=(copied, sink), edges=Edge.results((copied.interface.output,), sink)
    )
    execute(Execution(root), backend)
    assert seen == [True]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_condition_can_be_reused_on_a_python_step_and_edge(
    backend: Backend,
) -> None:
    native = demo_native.make_conditional_step(False)
    assert native.condition is not None
    condition = native.condition.as_input()
    calls = []
    source = Step(lambda: calls.append("source"))
    gated = Step(lambda: calls.append("gated"), condition=condition)
    target = Step(lambda: calls.append("target"))
    root = Graph(
        nodes=(source, gated, target),
        edges=(Edge(source, target, condition=condition),),
    )
    execution = Execution(root)
    execute(execution, backend)
    assert sorted(calls) == ["source", "target"]
    assert len(export_graph(execution.compile())["conditions"]) == 2
