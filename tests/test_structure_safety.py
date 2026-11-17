from __future__ import annotations

import json

from rivlet import (
    Execution,
    ExecutionState,
    Graph,
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
    graph = Graph(nodes=(step,))
    execution = Execution(
        graph,
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
    from rivlet import Case, Graph, static

    unused = static.select(cases=()).graph()
    graph = static.select(
        cases=(
            Case(
                False,
                Graph(
                    nodes=unused.nodes,
                    edges=unused.edges,
                    observation=unused.observation,
                    static=unused.static,
                    static_selections=unused.static_selections,
                    interface=unused.interface.declaration,
                ),
            ),
            Case(True, Graph()),
        )
    ).graph()
    document = export_graph(Execution(graph).compile())
    assert document["excluded_nodes"] == ["0.0"]
    assert [node["id"] for node in document["nodes"]] == ["0", "0.1"]
