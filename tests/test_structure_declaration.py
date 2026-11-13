from __future__ import annotations

import json

from rivlet import (
    Case,
    Edge,
    Graph,
    GraphInterface,
    Leaf,
    StaticCondition,
    StaticConditions,
    Step,
    StepWiring,
    export_graph,
    static,
)


def forbidden(*_args: object) -> bool:
    raise AssertionError("export must not call host code")


def test_declaration_does_not_evaluate_any_host_code_or_select_cases() -> None:
    condition = forbidden
    left = Step(forbidden, label="left", condition=condition)
    right = Step(forbidden, label="right")
    rules = StaticConditions(evaluator=forbidden)
    graph = (
        rules.when(StaticCondition(object(), "secret payload"), left)
        | static.select(cases=(Case(False, right),))
    ).graph()
    document = export_graph(graph)
    assert document["view"] == "declaration"
    assert len(document["nodes"]) == 2
    assert document["nodes"][0]["condition"] == document["conditions"][0]["id"]
    assert document["static_rules"][0]["kind"] == "node"
    assert document["selections"][0]["cases"] == [{"node": "1", "condition": False}]
    assert "secret payload" not in json.dumps(document)
    assert json.loads(json.dumps(document)) == document


def test_export_preserves_order_result_and_conditional_edges() -> None:
    data = Step(lambda: "private result", label="data")
    gate = Step(lambda: True, label="gate")
    prerequisite = Step(lambda: None, label="prerequisite")

    def node_condition(flag):
        return flag.unwrap()

    def edge_condition(flag):
        return flag.unwrap()

    action = Leaf(lambda _ctx, value: value.unwrap())
    consumer = Step(action, label="consumer", condition=(node_condition, gate))
    graph = Graph(
        steps=(data, gate, prerequisite, consumer),
        edges=(
            Edge(gate, consumer),
            Edge(prerequisite, consumer, condition=(edge_condition, data)),
            *Edge.results((data,), consumer),
        ),
    )
    document = export_graph(graph)
    assert [edge["kind"] for edge in document["edges"]] == [
        "order",
        "conditional",
        "result",
    ]
    result = document["edges"][2]
    assert result["kind"] == "result"
    assert result["argument_index"] == 0
    assert [condition["inputs"] for condition in document["conditions"]] == [
        ["1"],
        ["0"],
    ]


def test_same_subgraph_mounts_have_distinct_ids_and_label_paths() -> None:
    leaf = Step(lambda: None, label="same")
    child = Graph(steps=(leaf,))
    graph = Graph(steps=(child.step(label="left"), child.step(label="right")))
    first = export_graph(graph)
    second = export_graph(graph)
    assert first == second
    assert [node["id"] for node in first["nodes"]] == ["0", "0.0", "1", "1.0"]
    assert [node["label_path"] for node in first["nodes"]] == [
        ["left"],
        ["left", "same"],
        ["right"],
        ["right", "same"],
    ]
    assert first["graphs"][1]["owner"] == "0"
    assert first["graphs"][2]["owner"] == "1"
    renamed = export_graph(
        Graph(steps=(child.step(label="renamed"), child.step(label="right")))
    )
    assert [node["id"] for node in first["nodes"]] == [
        node["id"] for node in renamed["nodes"]
    ]
    assert first["nodes"][0]["label_path"] != renamed["nodes"][0]["label_path"]


def test_ports_and_binding_refer_to_exact_descendants() -> None:
    ready = "ready"
    build = Step(lambda: None, label="build")
    archive = Step(lambda: None, label="archive")
    provider = (
        (build >> archive)
        .graph(interface=GraphInterface(outputs={ready: build}))
        .step()
    )
    configure = Step(lambda: None, label="configure")
    consume = Step(lambda: None, label="consume")
    consumer = (
        (configure >> consume)
        .graph(interface=GraphInterface(inputs={ready: (configure, consume)}))
        .step()
    )
    graph = Graph(
        steps=(provider, consumer),
        edges=(Edge(provider, consumer, ports=ready),),
    )
    document = export_graph(graph)
    edge = next(edge for edge in document["edges"] if edge["kind"] == "port")
    assert edge["provider_node"] == "0.0"
    assert edge["consumer_nodes"] == ["1.0", "1.1"]
    assert edge["source"] == "0" and edge["target"] == "1"
    assert document["graphs"][1]["ports"] == [
        {"kind": "output", "port": "ready", "node": "0.0"}
    ]


def test_documents_are_detached_and_from_wiring_keeps_real_edges() -> None:
    first = Step(lambda: "value", label="first")
    second = Step(lambda _ctx, value: value.unwrap(), label="second")
    graph = Graph.from_wiring((StepWiring(first), second.wire().results(first)))
    document = export_graph(graph)
    assert document["edges"][0]["kind"] == "result"
    document["nodes"][0]["label_path"].append("local mutation")
    document["edges"].clear()
    fresh = export_graph(graph)
    assert len(fresh["edges"]) == 1
    assert fresh["nodes"][0]["label_path"] == ["first"]
