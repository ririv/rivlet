from __future__ import annotations

import json

import pytest

from rivlet import (
    Case,
    Edge,
    Graph,
    GraphExecution,
    GraphInterface,
    LeafAction,
    ResultEdge,
    StaticCondition,
    StaticConditions,
    Step,
    StepPath,
    compile_graph,
    export_graph,
    static,
)


def test_compiled_export_never_repeats_static_evaluation() -> None:
    calls = []

    def evaluate(_condition):
        calls.append("evaluated")
        return False

    first, second = Step(lambda: None), Step(lambda: None)
    graph = Graph(
        steps=(first, second),
        edges=(Edge(first, second),),
        static=StaticConditions(
            evaluator=evaluate,
            conditions={(first, second): StaticCondition(object(), "private")},
        ),
    )
    compiled = GraphExecution(graph).compile()
    assert calls == ["evaluated"]
    document = export_graph(compiled)
    assert calls == ["evaluated"]
    assert document["edges"] == document["dependencies"] == []
    assert document["excluded_edges"] == ["graph:root/edge:0"]
    assert export_graph(compiled) == document
    assert calls == ["evaluated"]


def test_pruned_subtree_ids_are_excluded_not_runtime_skipped() -> None:
    def forbidden(*_args):
        raise AssertionError("not reached")

    kept = Step(forbidden, label="kept")
    removed = Graph(steps=(Step(forbidden, label="nested"),))
    graph = static.select(cases=(Case(True, kept), Case(False, removed))).graph()
    document = export_graph(GraphExecution(graph).compile())
    assert document["view"] == "compiled"
    assert [node["id"] for node in document["nodes"]] == ["0"]
    assert document["excluded_nodes"] == ["1", "1.0"]
    assert [scope["id"] for scope in document["graphs"]] == ["graph:root"]
    assert "skipped" not in json.dumps(document)


def test_runtime_condition_origins_and_result_receivers_stay_distinct() -> None:
    def forbidden(*_args):
        raise AssertionError("runtime predicate must not run")

    first, second = Step(forbidden), Step(forbidden)
    shared = forbidden
    # 同一 predicate 可作为不同声明对象，也可复用于同一节点的多个具体位置。
    other = forbidden
    action = LeafAction(forbidden)
    target = Step(action, condition=(shared, first))
    graph = Graph(
        steps=(first, second, target),
        edges=(
            Edge(first, target, condition=(shared, first)),
            Edge(second, target, condition=(other, second)),
            ResultEdge(second, target, 0, action),
        ),
    )
    document = export_graph(GraphExecution(graph).compile())
    assert len(document["conditions"]) == 3
    assert document["nodes"][-1]["condition"] == "condition:0"
    edges = document["edges"]
    assert edges[0]["kind"] == edges[1]["kind"] == "conditional"
    assert edges[0]["condition"] == "condition:1"
    assert edges[1]["condition"] == "condition:2"
    assert edges[2]["kind"] == "result" and edges[2]["receiver"] == {"kind": "action"}
    assert [condition["inputs"] for condition in document["conditions"]] == [
        ["0"],
        ["0"],
        ["1"],
    ]
    assert document["dependencies"] == [
        {
            "kind": "conditional",
            "source": "0",
            "target": "2",
            "condition": "condition:1",
        },
        {
            "kind": "conditional",
            "source": "1",
            "target": "2",
            "condition": "condition:2",
        },
        {"kind": "required", "source": "1", "target": "2"},
    ]


def test_lowered_port_dependencies_do_not_wait_for_entire_provider() -> None:
    ready = "ready"
    build, archive = Step(lambda: None), Step(lambda: None)
    provider = (
        (build >> archive)
        .graph(interface=GraphInterface(outputs={ready: build}))
        .step()
    )
    prepare, consume = Step(lambda: None), Step(lambda: None)
    consumer = (
        (prepare >> consume)
        .graph(interface=GraphInterface(inputs={ready: consume}))
        .step()
    )
    graph = Graph(
        steps=(provider, consumer),
        edges=(Edge(provider, consumer, ports=ready),),
    )
    document = export_graph(GraphExecution(graph).compile())
    assert {"kind": "required", "source": "0.0", "target": "1.1"} in document[
        "dependencies"
    ]
    assert {"kind": "required", "source": "0", "target": "1"} not in document[
        "dependencies"
    ]
    edge = next(edge for edge in document["edges"] if edge["kind"] == "port")
    assert edge["provider_node"] == "0.0"
    assert edge["consumer_nodes"] == ["1.1"]


@pytest.mark.parametrize("parent", [None, StepPath((5, 7))])
def test_mount_identity_matches_compilation_and_events(parent) -> None:
    child = Graph(steps=(Step(lambda: None, label="duplicate"),))
    graph = Graph(steps=(child.step(), child.step()))
    compiled = compile_graph(graph, root_label="root", parent_step_path=parent)
    document = export_graph(compiled)
    assert {node["id"] for node in document["nodes"]} == {
        str(node.path) for node in (*compiled.steps, *compiled.composites)
    }
    if parent is not None:
        assert all(node["id"].startswith("5.7.") for node in document["nodes"])
        assert document["parent"] == "5.7"
        assert document["root"] == "graph:5.7"
        assert document["graphs"][0]["owner"] == "5.7"
    else:
        assert document["parent"] is None
        assert document["root"] == "graph:root"


def test_empty_compilation_is_exportable() -> None:
    document = export_graph(GraphExecution(Graph()).compile())
    assert document["nodes"] == document["edges"] == document["dependencies"] == []
    assert len(document["graphs"]) == 1


def test_mounted_export_keeps_static_edge_provenance_in_parent_namespace() -> None:
    first, second = Step(lambda: None), Step(lambda: None)
    graph = Graph(
        steps=(first, second),
        edges=(Edge(first, second),),
        static=StaticConditions(
            evaluator=lambda _: False,
            conditions={(first, second): StaticCondition(object(), "condition")},
        ),
    )
    compiled = compile_graph(graph, root_label="child", parent_step_path=StepPath((9,)))
    document = export_graph(compiled)
    assert document["root"] == "graph:9"
    assert document["excluded_edges"] == ["graph:9/edge:0"]
    assert document["edges"] == document["dependencies"] == []
