"""验证条件输入声明、接线边界和静态裁剪之间的约束。"""

from __future__ import annotations

import pytest

from rivlet import (
    Edge,
    Execution,
    Graph,
    GraphError,
    Option,
    StaticCondition,
    StaticConditions,
    Step,
    ThreadRunOptions,
    compile_graph,
    export_graph,
)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_flat_condition_accepts_many_ordered_sources(mode):
    sources = tuple(Step(lambda value=value: value) for value in range(128))
    seen = []

    def predicate(*values: Option[object]) -> bool:
        seen.extend(value.unwrap() for value in values)
        return True

    consumer = Step(
        lambda: seen.append("action"),
        condition=(predicate, sources[0], *sources[1:]),
    )
    Execution(Step(Graph(steps=(*sources, consumer)))).run(
        ThreadRunOptions(workers=4), mode=mode
    )
    assert seen == [*range(128), "action"]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_external_condition_source_does_not_remove_dsl_sequence_edge(mode):
    calls = []
    probe = Step(lambda: True)
    prepare = Step(lambda: calls.append("prepare"))
    consumer = Step(
        lambda: calls.append("consume"),
        condition=(lambda value: value.unwrap(), probe),
    )
    graph = (probe >> prepare >> consumer).graph()
    assert Edge(prepare, consumer) in graph.edges
    Execution(Step(graph)).run(ThreadRunOptions(workers=3), mode=mode)
    assert calls == ["prepare", "consume"]


def test_condition_sources_alone_participate_in_cycle_validation():
    source = Step(lambda: True)
    consumer = Step(lambda: None, condition=(lambda value: value.unwrap(), source))
    with pytest.raises(GraphError, match="dependency cycle"):
        Graph(steps=(source, consumer), edges=(Edge(consumer, source),))


def test_condition_sources_must_belong_to_the_local_graph():
    outside = Step(lambda: True)
    consumer = Step(lambda: None, condition=(lambda value: value.unwrap(), outside))
    with pytest.raises(GraphError, match="outside this graph"):
        Graph(steps=(consumer,))


def test_static_rule_cannot_remove_an_active_condition_input():
    source = Step(lambda: True)
    consumer = Step(lambda: None, condition=(lambda value: value.unwrap(), source))
    graph = Graph(
        steps=(source, consumer),
        static=StaticConditions(
            evaluator=lambda condition: condition.payload is True,
            conditions={
                (source, consumer): StaticCondition(False, "remove input"),
            },
        ),
    )
    with pytest.raises(GraphError, match="condition input was removed"):
        compile_graph(graph, root_label="root")


def test_excluded_edge_does_not_keep_or_resolve_its_condition_inputs():
    probe, source, target = (Step(lambda: True) for _ in range(3))

    def unused(_value: Option[object]) -> bool:
        pytest.fail("a statically excluded predicate must not run")

    graph = Graph(
        steps=(probe, source, target),
        edges=(Edge(source, target, condition=(unused, probe)),),
        static=StaticConditions(
            evaluator=lambda condition: condition.payload is True,
            conditions={
                probe: StaticCondition(False, "remove source"),
                (source, target): StaticCondition(False, "remove edge"),
            },
        ),
    )
    execution = Execution(Step(graph))
    execution.run()
    document = export_graph(execution.compile())
    assert document["conditions"] == []
    assert document["edges"] == []


def test_condition_signature_is_checked_without_evaluating_predicate():
    def needs_two(_first: Option[object], _second: Option[object]) -> bool:
        pytest.fail("declaration must not evaluate predicates")

    source = Step(lambda: True)
    consumer = Step(lambda: None, condition=(needs_two, source))
    with pytest.raises(GraphError, match="signature does not match 1"):
        Graph(steps=(source, consumer))
