from __future__ import annotations

import asyncio
from typing import Literal

import pytest

from rivlet import (
    AsyncGraphRunOptions,
    ConditionalPrerequisite,
    Edge,
    Graph,
    GraphError,
    GraphExecution,
    GraphExecutionState,
    Nothing,
    Option,
    SerialGraphExecutor,
    Some,
    Step,
    StepRunContext,
    ThreadGraphRunOptions,
    compile_graph,
)

type Backend = Literal["serial", "thread", "async"]


def execute(graph: Graph, backend: Backend) -> None:
    if backend == "serial":
        SerialGraphExecutor(GraphExecutionState()).execute(
            compile_graph(graph, root_label="root")
        )
    else:
        options = (
            ThreadGraphRunOptions(workers=2)
            if backend == "thread"
            else AsyncGraphRunOptions(concurrency=2)
        )
        GraphExecution(graph).run(options)


def test_option_preserves_none_and_false_values() -> None:
    assert Some(None).is_some()
    assert not Some(None).is_none()
    assert Some(None).unwrap() is None
    assert Some(None).unwrap_or("default") is None
    assert Some(False).unwrap_or(True) is False
    assert Nothing().is_none()
    assert not Nothing().is_some()
    assert Nothing().unwrap_or("default") == "default"
    with pytest.raises(ValueError, match="absent result"):
        Nothing().unwrap()
    for option in (Some(False), Nothing()):
        with pytest.raises(TypeError, match="explicitly"):
            bool(option)


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
@pytest.mark.parametrize("edge", [False, True])
def test_inputs_distinguish_skipped_from_none_in_declared_order(
    backend: Backend, edge: bool
) -> None:
    seen: list[tuple[Option[object], ...]] = []
    calls: list[str] = []
    skipped = Step(lambda: "unreachable", condition=lambda: False)
    returned_none = Step(lambda: None)
    returned_false = Step(lambda: False)

    def condition(
        missing: Option[object], present: Option[object], false: Option[object]
    ) -> bool:
        seen.append((missing, present, false))
        return (
            missing.is_none() and present.unwrap() is None and false.unwrap() is False
        )

    predicate = condition
    prerequisite = Step(lambda: calls.append("prerequisite"))
    consumer = Step(
        lambda _ctx, *_inputs: calls.append("consumer"),
        condition=None if edge else (predicate, skipped, returned_none, returned_false),
    )
    graph = Graph.from_wiring(
        (
            consumer.wire(
                after=(
                    ConditionalPrerequisite(
                        prerequisite,
                        condition=(predicate, skipped, returned_none, returned_false),
                    ),
                )
                if edge
                else ()
            ).results(skipped, returned_none, returned_false),
            prerequisite.wire(),
            returned_false.wire(),
            returned_none.wire(),
            skipped.wire(),
        )
    )
    execute(graph, backend)
    assert seen == [(Nothing(), Some(None), Some(False))]
    assert "consumer" in calls
    if edge:
        assert calls == ["prerequisite", "consumer"]


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_repeated_nested_graph_mounts_supply_their_own_results(
    backend: Backend,
) -> None:
    seen: list[object] = []

    def produce(ctx: StepRunContext) -> object:
        return ctx.step_path

    probe = Step(produce, label="probe")

    def condition(result: Option[object]) -> bool:
        seen.append(result.unwrap())
        return True

    consumer = Step(
        lambda _ctx, _result: None,
        condition=(_consumer_condition := (condition, probe)),
    )
    child = Graph.from_wiring(
        (
            consumer.wire().results(probe),
            probe.wire(),
        )
    )
    nested = Graph(steps=(Step(child),))
    execute(Graph(steps=(Step(nested), Step(nested))), backend)
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_repeated_input_preserves_arguments_without_rewrapping() -> None:
    probe = Step(lambda: None)
    seen: list[tuple[Option[object], Option[object]]] = []

    def condition(first: Option[object], second: Option[object]) -> bool:
        seen.append((first, second))
        return True

    consumer = Step(
        lambda _ctx, _first, _second: None,
        condition=(_consumer_condition := (condition, probe, probe)),
    )
    execution = GraphExecution(
        Graph.from_wiring(
            (
                consumer.wire().results(probe, probe),
                probe.wire(),
            )
        )
    )
    execution.run()
    assert len(seen) == 1
    first, second = seen[0]
    assert first is second
    assert first is execution.require_result(probe)
    assert first.unwrap() is None


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_actions_and_conditions_share_explicit_result_bindings(
    backend: Backend,
) -> None:
    seen: list[tuple[Option[object], Option[object]]] = []

    async def produce() -> int:
        return 42

    source = Step(produce)
    skipped = Step(lambda: "unused", condition=lambda: False)

    def allowed(value: Option[object], absent: Option[object]) -> bool:
        seen.append((value, absent))
        return True

    async def consume(
        ctx: StepRunContext, value: Option[object], absent: Option[object]
    ) -> None:
        ctx.control.require_running()
        assert value is seen[0][0]
        assert value.unwrap() == 42
        assert absent.is_none()
        assert not hasattr(ctx, "results")
        assert not hasattr(ctx, "view")
        seen.append((value, absent))

    consumer = Step(
        consume, condition=(_consumer_condition := (allowed, source, skipped))
    )
    execute(
        Graph.from_wiring(
            (
                consumer.wire().results(source, skipped),
                skipped.wire(),
                source.wire(),
            )
        ),
        backend,
    )
    assert len(seen) == 2


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_action_only_inputs_enable_recording_and_bind_each_mount(
    backend: Backend,
) -> None:
    seen: list[object] = []
    producer = Step(lambda ctx: ctx.step_path)

    def consume(_ctx: StepRunContext, value: Option[object]) -> None:
        seen.append(value.unwrap())

    child = Graph.from_wiring(
        (
            producer.wire(),
            Step(consume).wire().results(producer),
        )
    )
    execute(Graph(steps=(child.step(), child.step())), backend)
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_result_binding_validates_callable_boundary_before_execution() -> None:
    source = Step(lambda: 42)
    consumer = Step(lambda _ctx, _first, _second: None)
    with pytest.raises(GraphError, match="signature"):
        Graph.from_wiring((source.wire(), consumer.wire().results(source)))


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_each_receiver_binds_its_own_results_on_each_mount(backend: Backend) -> None:
    seen: list[tuple[str, object]] = []
    permission = Step(lambda: "permission")
    check = Step(lambda: "edge")
    design = Step(lambda ctx: ctx.step_path)
    prepare = Step(lambda: seen.append(("prepare", None)))

    def check_value(value: Option[object]) -> bool:
        seen.append(("check", value.unwrap()))
        return True

    node_condition = check_value
    edge_condition = check_value
    consumer = Step(
        lambda _ctx, plan: seen.append(("action", plan.unwrap())),
        condition=(node_condition, permission),
    )
    child = Graph.from_wiring(
        (
            permission.wire(),
            check.wire(),
            design.wire(),
            prepare.wire(),
            consumer.wire(
                after=(
                    ConditionalPrerequisite(prepare, condition=(edge_condition, check)),
                )
            ).results(design),
        )
    )
    execute(Graph(steps=(child.step(), child.step())), backend)
    checks = [value for kind, value in seen if kind == "check"]
    assert checks.count("permission") == 2
    assert checks.count("edge") == 2
    plans = [value for kind, value in seen if kind == "action"]
    assert len(plans) == 2 and plans[0] != plans[1]


def test_false_node_condition_does_not_wait_for_other_receiver_inputs() -> None:
    decided = asyncio.Event()

    def reject(value: Option[object]) -> bool:
        assert value.unwrap() is False
        decided.set()
        return False

    async def delayed() -> str:
        # 若节点条件错误地等待其它接收方输入，这里会超时。
        await asyncio.wait_for(decided.wait(), timeout=2)
        return "plan"

    permission = Step(lambda: False)
    action_input = Step(delayed)
    edge_input = Step(delayed)
    prepare = Step(lambda: None)
    condition = reject

    def edge_condition(_value):
        return pytest.fail("unused edge condition ran")

    consumer = Step(
        lambda _ctx, _value: pytest.fail("skipped action ran"),
        condition=(condition, permission),
    )
    graph = Graph.from_wiring(
        (
            permission.wire(),
            action_input.wire(),
            edge_input.wire(),
            prepare.wire(),
            consumer.wire(
                after=(
                    ConditionalPrerequisite(
                        prepare, condition=(edge_condition, edge_input)
                    ),
                )
            ).results(action_input),
        )
    )
    GraphExecution(graph).run(AsyncGraphRunOptions(concurrency=4))
    assert decided.is_set()


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_one_condition_object_can_bind_two_conditional_edges(backend: Backend) -> None:
    source = Step(lambda: False)

    def condition(value):
        return value.unwrap() is True

    left = Step(lambda: None)
    right = Step(lambda: None)
    seen: list[str] = []
    consumer = Step(lambda: seen.append("ran"))
    execute(
        Graph.from_wiring(
            (
                source.wire(),
                left.wire(),
                right.wire(),
                consumer.wire(
                    after=(
                        ConditionalPrerequisite(left, condition=(condition, source)),
                        ConditionalPrerequisite(right, condition=(condition, source)),
                    ),
                ),
            )
        ),
        backend,
    )
    assert seen == ["ran"]


def test_result_wiring_is_immutable_and_chained_sources_keep_order() -> None:
    first = Step(lambda: 1)
    second = Step(lambda: 2)
    seen: list[tuple[object, object]] = []
    consumer = Step(lambda _ctx, a, b: seen.append((a.unwrap(), b.unwrap())))
    original = consumer.wire(after=(first,))
    partial = original.results(first)
    complete = partial.results(second)
    assert original is not partial and partial is not complete
    with pytest.raises(GraphError, match="signature"):
        Graph.from_wiring((first.wire(), second.wire(), partial))
    execute(Graph.from_wiring((first.wire(), second.wire(), complete)), "serial")
    assert seen == [(1, 2)]


def test_result_edge_group_is_complete_and_unique_per_action() -> None:
    first = Step(lambda: 1)
    second = Step(lambda: 2)
    consumer = Step(lambda _ctx, *values: None)
    connections = Edge.results((first, second), consumer)
    with pytest.raises(GraphError, match="included in full"):
        Graph(steps=(first, second, consumer), edges=connections[:1])
    with pytest.raises(GraphError, match="must not be repeated"):
        Graph(steps=(first, second, consumer), edges=(*connections, connections[0]))
    with pytest.raises(GraphError, match="one result connection group"):
        Graph(
            steps=(first, second, consumer),
            edges=(
                *Edge.results((first,), consumer),
                *Edge.results((second,), consumer),
            ),
        )


@pytest.mark.parametrize("backend", ["serial", "thread", "async"])
def test_result_edges_supply_ordered_values_without_extra_order_edges(
    backend: Backend,
) -> None:
    first = Step(lambda: 1)
    second = Step(lambda: 2)
    seen: list[tuple[object, object]] = []
    consumer = Step(lambda _ctx, a, b: seen.append((a.unwrap(), b.unwrap())))
    execute(
        Graph(
            steps=(consumer, second, first),
            edges=tuple(reversed(Edge.results((first, second), consumer))),
        ),
        backend,
    )
    assert seen == [(1, 2)]


def test_result_connections_require_local_producers_and_a_callable_target() -> None:
    producer = Step(lambda: 1)
    child = Graph(steps=(producer,)).step()
    consumer = Step(lambda _ctx, value: None)
    with pytest.raises(GraphError, match="unknown|outside"):
        Graph(steps=(child, consumer), edges=Edge.results((producer,), consumer))
    with pytest.raises(GraphError, match="callable action"):
        Edge.results((producer,), child)
