from __future__ import annotations

import json

from rivlet import (
    Execution,
    ExecutionState,
    Graph,
    GraphStructure,
    ResultStore,
    Step,
    export_graph,
)
from rivlet import run as run_execution


def test_export_does_not_include_results_shared_state_or_callable_representation() -> (
    None
):
    class Secret:
        def __repr__(self):
            raise AssertionError("secret object was inspected")

    secret = Secret()
    step = Step(lambda: secret, label=("public label"))
    graph = Graph(structure=GraphStructure(nodes=(step,)))
    execution = Execution(
        Graph(structure=graph.structure),
        shared=secret,
        execution_state=ExecutionState(results=ResultStore()),
    )
    before = export_graph(execution.root)
    run_execution(execution)
    assert execution.require_result(step).unwrap() is secret
    after = export_graph(execution.compile())
    assert after["nodes"] == before["nodes"]
    assert "result" not in after and "state" not in after
    assert json.loads(json.dumps(after)) == after


def test_unused_invalid_selection_under_pruned_parent_is_never_evaluated() -> None:
    from rivlet import Case, Graph, GraphStructure, static

    unused = static.select(cases=()).graph()
    graph = static.select(
        cases=(
            Case(False, Graph(structure=unused.structure)),
            Case(True, Graph(structure=GraphStructure())),
        )
    ).graph()
    document = export_graph(Execution(Graph(structure=graph.structure)).compile())
    assert document["excluded_nodes"] == ["0.0"]
    assert [node["id"] for node in document["nodes"]] == ["0", "0.1"]
