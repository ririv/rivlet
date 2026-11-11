from __future__ import annotations

import json

from rivlet import (
    Graph,
    GraphExecution,
    GraphExecutionState,
    GraphResultStore,
    Step,
    export_graph,
)
from rivlet._native import _native_process_graph


def test_native_subgraph_exports_all_nodes_and_edges_without_process_details(
    tmp_path,
) -> None:
    marker = tmp_path / "must-not-exist"
    native = _native_process_graph(
        "native",
        [
            (
                "compile",
                "/private/secret/program",
                [str(marker), "secret-token"],
                str(tmp_path),
                {"SECRET": "hidden"},
            ),
            ("link", "/private/secret/linker", [], str(tmp_path), {}),
        ],
        [(0, 1)],
    )
    graph = Graph(steps=(Step(native, label="native mount"),))
    for subject in (graph, GraphExecution(graph).compile()):
        document = export_graph(subject)
        assert [node["id"] for node in document["nodes"]] == ["0", "0.0", "0.1"]
        assert [node["label"] for node in document["nodes"]] == [
            "native mount",
            "compile",
            "link",
        ]
        assert document["edges"] == [
            {
                "kind": "order",
                "id": "graph:0/edge:0",
                "graph": "graph:0",
                "source": "0.0",
                "target": "0.1",
            }
        ]
        encoded = json.dumps(document)
        assert "secret" not in encoded
        assert "hidden" not in encoded
        assert str(tmp_path) not in encoded
        assert not marker.exists()


def test_export_does_not_include_results_shared_state_or_callable_representation() -> (
    None
):
    class Secret:
        def __repr__(self):
            raise AssertionError("secret object was inspected")

    secret = Secret()
    step = Step(lambda: secret, label="public label")
    graph = Graph(steps=(step,))
    execution = GraphExecution(
        graph,
        shared=secret,
        execution_state=GraphExecutionState(results=GraphResultStore()),
    )
    before = export_graph(graph)
    execution.run()
    assert execution.require_result(step).unwrap() is secret
    after = export_graph(execution.compile())
    assert after["nodes"] == before["nodes"]
    assert "result" not in after and "state" not in after
    assert json.loads(json.dumps(after)) == after


def test_unused_invalid_selection_under_pruned_parent_is_never_evaluated() -> None:
    from rivlet import Case, static

    unused = static.select(cases=()).graph()
    graph = static.select(cases=(Case(False, unused), Case(True, Graph()))).graph()
    document = export_graph(GraphExecution(graph).compile())
    assert document["excluded_nodes"] == ["0"]
    assert [node["id"] for node in document["nodes"]] == ["1"]
