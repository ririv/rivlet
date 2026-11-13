from __future__ import annotations

import pytest

from rivlet import (
    Edge,
    ExactProviderRequirement,
    Execution,
    Graph,
    GraphError,
    GraphInterface,
    GraphMode,
    GraphStructure,
    Leaf,
    Node,
    StaticCondition,
    StaticConditions,
    ThreadRunOptions,
    compile_graph,
    render_graph,
    resolve_port_requirements,
)

ENABLED = StaticCondition(payload=True, description="enabled")
DISABLED = StaticCondition(payload=False, description="disabled")


def evaluate(condition: StaticCondition) -> bool:
    return condition.payload is True


def test_static_dsl_and_direct_declaration_compile_identically() -> None:
    first = Leaf(lambda: None, label=("first"))
    last = Leaf(lambda: None, label=("last"))
    static = StaticConditions(evaluator=evaluate)
    dsl = (static.when(ENABLED, first) >> last).graph()
    direct = Graph(
        structure=GraphStructure(
            nodes=(first, last),
            edges=(Edge(first, last),),
            static=StaticConditions(evaluator=evaluate, conditions={first: ENABLED}),
        )
    )

    assert compile_graph(dsl.structure, root_label="root") == compile_graph(
        direct.structure, root_label="root"
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_static_edge_keeps_both_actions(enabled: bool, mode: GraphMode) -> None:
    calls: list[str] = []
    first = Leaf(lambda: calls.append("first"))
    last = Leaf(lambda: calls.append("last"))
    graph = (
        StaticConditions(evaluator=evaluate)
        .edge(first, last, condition=ENABLED if enabled else DISABLED)
        .graph()
    )

    compiled = compile_graph(graph.structure, root_label="root")
    assert bool(compiled.steps[1].after) is enabled
    Execution(Graph(structure=graph.structure)).run(
        options=ThreadRunOptions(), mode=mode
    )
    assert sorted(calls) == ["first", "last"]


def test_static_scopes_compose_without_replacing_evaluators() -> None:
    left = Leaf(lambda: None, label=("left"))
    right = Leaf(lambda: None, label=("right"))
    yes = StaticConditions(evaluator=lambda _condition: True)
    no = StaticConditions(evaluator=lambda _condition: False)
    graph = (yes.when(ENABLED, left) | no.when(ENABLED, right)).graph()

    assert len(compile_graph(graph.structure, root_label="root").steps) == 1
    assert graph.structure.nodes == (left, right)


def test_result_edge_static_rule_is_evaluated_once() -> None:
    evaluated: list[StaticCondition] = []

    def record(condition: StaticCondition) -> bool:
        evaluated.append(condition)
        return True

    source = Leaf(lambda: 42)
    consumer = Leaf(lambda _ctx, result: result.unwrap())
    graph = Graph(
        structure=GraphStructure(
            nodes=(source, consumer),
            edges=(*Edge.results((source,), consumer),),
            static=StaticConditions(
                evaluator=record, conditions={(source, consumer): ENABLED}
            ),
        )
    )
    compiled = compile_graph(graph.structure, root_label="root")
    assert compiled.steps[1].after
    assert evaluated == [ENABLED]


def test_inactive_subgraph_does_not_evaluate_nested_static_rules() -> None:
    def unexpected(_condition: StaticCondition) -> bool:
        pytest.fail("inactive subgraph evaluator must not run")

    child = (
        StaticConditions(evaluator=unexpected)
        .when(ENABLED, Leaf(lambda: pytest.fail("inactive action must not run")))
        .graph()
    )
    graph = (
        StaticConditions(evaluator=evaluate)
        .when(DISABLED, Graph(structure=child.structure))
        .graph()
    )

    assert not compile_graph(graph.structure, root_label="root").steps
    Execution(Graph(structure=graph.structure)).run(options=ThreadRunOptions())


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_static_and_runtime_rules_coexist_on_nodes_and_edges(mode: GraphMode) -> None:
    calls: list[str] = []
    first = Leaf(lambda: calls.append("first"))
    last = Leaf(lambda: calls.append("last"), condition=(lambda: False))
    graph = Graph(
        structure=GraphStructure(
            nodes=(first, last),
            edges=(Edge(first, last, condition=lambda: True),),
            static=StaticConditions(
                evaluator=evaluate, conditions={last: ENABLED, (first, last): ENABLED}
            ),
        )
    )

    compiled = compile_graph(graph.structure, root_label="root")
    assert compiled.steps[1].condition is not None
    assert compiled.steps[1].after
    Execution(Graph(structure=graph.structure)).run(
        options=ThreadRunOptions(), mode=mode
    )
    assert calls == ["first"]


def test_declaration_render_keeps_conditions_without_evaluation() -> None:
    def unexpected(_condition: StaticCondition) -> bool:
        pytest.fail("declaration rendering must not evaluate conditions")

    first = Leaf(lambda: None, label=("first"))
    last = Leaf(lambda: None, label=("last"))
    conditions: dict[Node | tuple[Node, Node], StaticCondition] = {first: ENABLED}
    static = StaticConditions(evaluator=unexpected, conditions=conditions)
    conditions.clear()
    graph = Graph(
        structure=GraphStructure(
            nodes=(first, last),
            edges=(Edge(first, last),),
            static=(
                static,
                StaticConditions(
                    evaluator=unexpected, conditions={(first, last): DISABLED}
                ),
            ),
        )
    )

    rendered = render_graph(graph.structure, root_label="root")
    assert "[static: enabled]" in rendered
    assert "(static: disabled)" in rendered


def test_static_rules_validate_graph_membership_and_reject_overrides() -> None:
    first = Leaf(lambda: None)
    last = Leaf(lambda: None)
    static = StaticConditions(evaluator=evaluate, conditions={first: ENABLED})
    with pytest.raises(GraphError, match="outside this graph"):
        Graph(structure=GraphStructure(nodes=(last,), static=static))
    with pytest.raises(GraphError, match="unknown graph edge"):
        Graph(
            structure=GraphStructure(
                nodes=(first, last),
                static=StaticConditions(
                    evaluator=evaluate, conditions={(first, last): ENABLED}
                ),
            )
        )
    with pytest.raises(GraphError, match="already declared"):
        Graph(structure=GraphStructure(nodes=(first,), static=(static, static)))


def test_static_pruning_does_not_silently_drop_required_port() -> None:
    ready = "ready"
    produce = Leaf(lambda: None)
    consume = Leaf(lambda: None)
    provider = Graph(
        structure=GraphStructure(
            nodes=(produce,), interface=GraphInterface(outputs={ready: produce})
        )
    )
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={ready: consume})
        )
    )
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=ready),),
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(provider, consumer),
            edges=bindings,
            static=StaticConditions(
                evaluator=evaluate, conditions={provider: DISABLED}
            ),
        )
    )

    with pytest.raises(GraphError, match="inactive provider"):
        compile_graph(graph.structure, root_label="root")


def test_static_pruning_does_not_leave_runtime_reads_without_a_source() -> None:
    probe = Leaf(lambda: True)
    consumer = Leaf(
        lambda _ctx, *_inputs: None,
        condition=(
            _consumer_condition := (lambda result: bool(result.unwrap()), probe)
        ),
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(probe, consumer),
            static=StaticConditions(evaluator=evaluate, conditions={probe: DISABLED}),
            edges=(*Edge.results((probe,), consumer),),
        )
    )

    with pytest.raises(GraphError, match="result input was removed"):
        compile_graph(graph.structure, root_label="root")
