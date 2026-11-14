"""匿名与具名端点共存，保留真实归属、转发和结果语义。"""

from __future__ import annotations

import json
import sys
from threading import Event

import pytest
from execution_support import run_async_execution

from rivlet import (
    Edge,
    Execution,
    Graph,
    GraphInterface,
    GraphStructure,
    Step,
    export_graph,
    render_graph,
    render_graph_mermaid,
    run,
)
from rivlet._native import (
    _compose_rust_graph,
    _rust_process_graph,
    _rust_result_fixture,
)
from rivlet._native import _ResultFixtureKind as Kind


def test_anonymous_shorthand_mapping_and_named_ports_share_one_declaration():
    start, finish = Step(lambda: None), Step(lambda: None)
    shared = GraphInterface(inputs=start, outputs=finish)
    assert dict(shared.inputs) == {None: (start,)}
    assert dict(shared.outputs) == {None: finish}
    child = (start >> finish).graph(
        interface=GraphInterface(
            inputs={None: start, "named": start},
            outputs={None: finish, "named": finish},
        )
    )
    assert child.interface.input == child.interface.inputs[None]
    assert child.interface.output == child.interface.outputs[None]
    assert set(child.interface.inputs) == {None, "named"}
    assert child.interface.input != child.interface.inputs["named"]
    assert child.interface.input.nodes == (start,)
    outer = Graph.from_wiring(
        (child.wire(),), interface=GraphInterface(inputs=child, outputs=child)
    )
    assert outer.interface.input.nodes == (start,)
    assert outer.structure.interface.outputs[None] == child.interface.output
    reused = GraphInterface(inputs=shared.inputs, outputs=shared.outputs)
    assert reused.inputs == shared.inputs
    assert reused.outputs == shared.outputs
    grouped = GraphInterface(inputs=(start, finish))
    assert grouped.inputs[None] == (start, finish)
    # Graph 简写仍转发接口；显式映射才能把整个子图当作匿名端点。
    whole = GraphInterface(inputs={None: child}, outputs={None: child})
    assert whole.inputs[None] == (child,)
    assert whole.outputs[None] is child


def test_single_named_endpoint_is_not_an_implicit_anonymous_endpoint():
    node = Step(lambda: None)
    child = Graph(
        GraphStructure(
            nodes=(node,),
            interface=GraphInterface(
                inputs={"default": node}, outputs={"default": node}
            ),
        )
    )
    for access in (
        lambda: child.interface.input,
        lambda: child.interface.inputs[None],
        lambda: child.interface.output,
        lambda: child.interface.outputs[None],
    ):
        with pytest.raises(KeyError) as error:
            access()
        assert error.value.args == (None,)


@pytest.mark.parametrize("async_mode", (False, True))
@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
def test_mixed_ports_forward_results_in_source_order(async_mode, mode):
    plan, report = Step(lambda: 41), Step(lambda: "report")
    source = (plan | report).graph(
        interface=GraphInterface(outputs={None: plan, "report": report})
    )
    seen = []
    apply = Step(
        lambda ctx, value, report: seen.append((value.unwrap(), report.unwrap()))
    )
    inner = Graph(
        GraphStructure(
            nodes=(apply,), interface=GraphInterface(inputs=apply, outputs=apply)
        )
    )
    receiver = Graph.from_wiring((inner.wire(),), interface=inner)
    root = Graph(
        GraphStructure(
            nodes=(source, receiver),
            edges=Edge.results(
                (source.interface.output, source.interface.outputs["report"]),
                receiver.interface.input,
            ),
        )
    )
    execution = Execution(root)
    driver = run_async_execution if async_mode else run
    driver(execution, max_workers=3, mode=mode)
    assert seen == [(41, "report")]
    for subject in (root, execution.compile()):
        document = export_graph(subject)
        json.dumps(document)
        edges = [e for e in document["edges"] if e["kind"] == "result"]
        assert edges[0]["source_port"] is None
        assert edges[0]["target_port"] is None
        assert edges[1]["source_port"] == "report"
        assert edges[0]["provider_node"] != edges[1]["provider_node"]
    assert "anonymous" in render_graph(root, root_label="root")
    assert "anonymous" in render_graph_mermaid(root, root_label="root")


@pytest.mark.parametrize("mode", ("compiled", "dynamic"))
@pytest.mark.parametrize("connection", ("reference", "pair", "whole"))
def test_anonymous_readiness_does_not_change_whole_node_completion(mode, connection):
    configured = Event()
    installed = Event()

    def install():
        installed.set()

    def report():
        if connection != "whole":
            assert configured.wait(5), "consumer waited for the whole provider"

    def configure():
        assert installed.is_set()
        configured.set()

    install_node, report_node = Step(install), Step(report)
    producer = (install_node >> report_node).graph(
        interface=GraphInterface(outputs={None: install_node, "report": report_node})
    )
    configure_node = Step(configure)
    consumer = Graph(
        GraphStructure(
            nodes=(configure_node,), interface=GraphInterface(inputs=configure_node)
        )
    )
    match connection:
        case "reference":
            edge = Edge(producer.interface.output, consumer.interface.input)
        case "pair":
            edge = Edge(producer, consumer, ports=(None, None))
        case "whole":
            edge = Edge(producer, consumer)
    root = Graph(GraphStructure(nodes=(producer, consumer), edges=(edge,)))
    execution = Execution(root)
    run(execution, max_workers=3, mode=mode)
    assert configured.is_set()
    document = export_graph(execution.compile())
    # 按类型查找，避免把内部先后边当作外部端口边。
    ports = [e for e in document["edges"] if e["kind"] == "port"]
    assert len(ports) == (0 if connection == "whole" else 1)
    if ports:
        assert ports[0]["source_port"] is None
        assert ports[0]["target_port"] is None


@pytest.mark.parametrize("async_mode", (False, True))
def test_rust_anonymous_results_survive_native_composition_and_python_forwarding(
    async_mode,
):
    source_body, target_body = _rust_result_fixture(Kind.I64)
    source = _compose_rust_graph(
        [("source", source_body)],
        [],
        [],
        [(None, 0, "value"), ("named", 0, "value")],
    )
    target = _compose_rust_graph(
        [("target", target_body)],
        [],
        [(None, [(0, "apply")]), ("named", [(0, "apply")])],
        [(None, 0, "value")],
    )
    body = _compose_rust_graph(
        [("source", source), ("target", target)],
        [([(0, None)], (1, None))],
        [],
        [(None, 1, None)],
    )
    native = Graph(body)
    assert set(Graph(source).interface.outputs) == {None, "named"}
    assert set(Graph(target).interface.inputs) == {None, "named"}
    assert native.interface.output == native.interface.outputs[None]
    with pytest.raises(KeyError):
        _ = native.interface.input
    wrapped = Graph.from_wiring((native.wire(),), interface=native)
    seen = []
    sink = Step(lambda ctx, value: seen.append(value.unwrap()))
    root = Graph(
        GraphStructure(
            nodes=(wrapped, sink), edges=Edge.results((wrapped.interface.output,), sink)
        )
    )
    execution = Execution(root)
    (run_async_execution if async_mode else run)(execution, max_workers=3)
    assert seen == [42]
    for subject in (root, execution.compile()):
        document = export_graph(subject)
        json.dumps(document)
        edges = [e for e in document["edges"] if e["kind"] == "result"]
        anonymous = [e for e in edges if "target_port" in e]
        assert len(anonymous) == 1
        assert anonymous[0]["source_port"] is None
        assert anonymous[0]["target_port"] is None
        assert anonymous[0]["consumer_nodes"]
    render_graph(root, root_label="root")
    render_graph_mermaid(root, root_label="root")


def test_native_anonymous_readiness_routes_by_identity(tmp_path):
    source = _rust_process_graph(
        [("unit", sys.executable, ["-c", "pass"], str(tmp_path), {})],
        [],
        [(None, [0])],
        [(None, 0), ("named", 0)],
    )
    anonymous = _compose_rust_graph(
        [("left", source), ("right", source)],
        [(0, 1, None, None)],
        [(None, [(0, None)])],
        [(None, 1, None)],
    )
    # 使用公开 Python 入口消费真实 Rust 匿名出口。
    native = Graph(anonymous)
    sink = Step(lambda: None)
    consumer = Graph(
        GraphStructure(nodes=(sink,), interface=GraphInterface(inputs=sink))
    )
    root = Graph(
        GraphStructure(
            nodes=(native, consumer),
            edges=(Edge(native.interface.output, consumer.interface.input),),
        )
    )
    run(root)
    assert "anonymous" in render_graph_mermaid(root, root_label="root")
    ports = [e for e in export_graph(root)["edges"] if e["kind"] == "port"]
    assert len(ports) == 2
    assert all(e["source_port"] is None and e["target_port"] is None for e in ports)
