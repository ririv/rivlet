"""跨图结果的端口归属、复用隔离与最终参数绑定。"""

from __future__ import annotations

from functools import partial
from typing import Literal

import pytest
from execution_support import run_async_execution

from rivlet import (
    Edge,
    Execution,
    ExecutionState,
    Graph,
    GraphError,
    GraphInterface,
    GraphStructure,
    NodeExecutionEvent,
    NodeExecutionState,
    NodeRunContext,
    Option,
    ResultStore,
    SerialGraphExecutor,
    StaticCondition,
    StaticConditions,
    Step,
    export_graph,
    render_graph,
    render_graph_mermaid,
)
from rivlet import run as run_execution

type Backend = Literal["serial", "thread", "async"]


def run(root: Graph, backend: Backend) -> Execution:
    execution = Execution(root, execution_state=ExecutionState(results=ResultStore()))
    if backend == "serial":
        SerialGraphExecutor(execution.execution_state).execute(execution.compile())
    else:
        (
            partial(run_async_execution, max_workers=2)
            if backend == "async"
            else partial(run_execution, max_workers=2)
        )(execution)
    return execution


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_shared_source_structure_keeps_ordered_results_in_their_own_graph_nodes(
    backend: Backend,
) -> None:
    produce = Step(lambda ctx: ctx.node_path.labels[-2], label="produce")
    shared = GraphStructure(
        nodes=(produce,), interface=GraphInterface(outputs={"value": produce})
    )
    left, right = Graph(shared, label="left"), Graph(shared, label="right")
    seen: list[tuple[object, object]] = []
    consume = Step(lambda ctx, a, b: seen.append((a.unwrap(), b.unwrap())))
    receiver = Graph(
        GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={"apply": consume})
        )
    )
    root = Graph(
        GraphStructure(
            nodes=(left, right, receiver),
            edges=Edge.results(
                (right.interface.outputs["value"], left.interface.outputs["value"]),
                receiver.interface.inputs["apply"],
            ),
        )
    )
    execution = run(root, backend)
    assert seen == [("right", "left")]
    compilation = execution.compile()
    assert len(compilation.steps) == 3
    document = export_graph(compilation)
    assert [
        edge["argument_index"] for edge in document["edges"] if edge["kind"] == "result"
    ] == [0, 1]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_shared_consumer_structure_receives_each_graph_nodes_own_binding(
    backend: Backend,
) -> None:
    first, second = Step(lambda: 1), Step(lambda: 2)
    seen: list[tuple[str, object]] = []

    def consume(ctx: NodeRunContext, value: Option[object]) -> None:
        seen.append((ctx.node_path.labels[-2], value.unwrap()))

    consumer = Step(consume)
    shared = GraphStructure(
        nodes=(consumer,), interface=GraphInterface(inputs={"value": consumer})
    )
    left, right = Graph(shared, label="left"), Graph(shared, label="right")
    run(
        Graph(
            GraphStructure(
                nodes=(first, second, left, right),
                edges=(
                    *Edge.results((first,), left.interface.inputs["value"]),
                    *Edge.results((second,), right.interface.inputs["value"]),
                ),
            )
        ),
        backend,
    )
    assert sorted(seen) == [("left", 1), ("right", 2)]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_result_ports_allow_interleaved_graphs_without_a_component_level_cycle(
    backend: Backend,
) -> None:
    start = Step(lambda: 2)
    finish = Step(lambda ctx, value: value.unwrap() + 1)
    first = Graph(
        GraphStructure(
            nodes=(start, finish),
            interface=GraphInterface(
                inputs={"finish": finish}, outputs={"started": start}
            ),
        )
    )
    middle = Step(lambda ctx, value: value.unwrap() + 1)
    second = Graph(
        GraphStructure(
            nodes=(middle,),
            interface=GraphInterface(
                inputs={"start": middle}, outputs={"done": middle}
            ),
        )
    )
    execution = run(
        Graph(
            GraphStructure(
                nodes=(first, second),
                edges=(
                    *Edge.results(
                        (first.interface.outputs["started"],),
                        second.interface.inputs["start"],
                    ),
                    *Edge.results(
                        (second.interface.outputs["done"],),
                        first.interface.inputs["finish"],
                    ),
                ),
            )
        ),
        backend,
    )
    assert execution.require_result(finish).unwrap() == 4


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_skipped_graph_output_supplies_nothing_to_the_consumer(
    backend: Backend,
) -> None:
    produce = Step(lambda: pytest.fail("skipped producer ran"))
    producer = Graph(
        GraphStructure(
            nodes=(produce,), interface=GraphInterface(outputs={"value": produce})
        ),
        condition=lambda: False,
    )
    seen: list[bool] = []
    consume = Step(lambda ctx, value: seen.append(value.is_none()))
    run(
        Graph(
            GraphStructure(
                nodes=(producer, consume),
                edges=Edge.results((producer.interface.outputs["value"],), consume),
            )
        ),
        backend,
    )
    assert seen == [True]


def test_unbound_public_result_input_is_rejected_by_final_compilation() -> None:
    consume = Step(lambda ctx, value: value.unwrap())
    receiver = Graph(
        GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={"value": consume})
        )
    )
    with pytest.raises(GraphError, match="signature"):
        Execution(receiver).compile()


def test_different_port_aliases_cannot_install_two_result_groups_on_one_action() -> (
    None
):
    produce = Step(lambda: 1)
    consume = Step(lambda ctx, value: value.unwrap())
    receiver = Graph(
        GraphStructure(
            nodes=(consume,),
            interface=GraphInterface(inputs={"first": consume, "second": consume}),
        )
    )
    root = Graph(
        GraphStructure(
            nodes=(produce, receiver),
            edges=(
                *Edge.results((produce,), receiver.interface.inputs["first"]),
                *Edge.results((produce,), receiver.interface.inputs["second"]),
            ),
        )
    )
    with pytest.raises(GraphError, match="one result connection group"):
        Execution(root).compile()


def test_cycle_through_result_ports_is_rejected_after_resolving_step_endpoints() -> (
    None
):
    a = Step(lambda ctx, value: value.unwrap())
    b = Step(lambda ctx, value: value.unwrap())
    first = Graph(
        GraphStructure(
            nodes=(a,), interface=GraphInterface(inputs={"in": a}, outputs={"out": a})
        )
    )
    second = Graph(
        GraphStructure(
            nodes=(b,), interface=GraphInterface(inputs={"in": b}, outputs={"out": b})
        )
    )
    root = Graph(
        GraphStructure(
            nodes=(first, second),
            edges=(
                *Edge.results(
                    (first.interface.outputs["out"],), second.interface.inputs["in"]
                ),
                *Edge.results(
                    (second.interface.outputs["out"],), first.interface.inputs["in"]
                ),
            ),
        )
    )
    with pytest.raises(GraphError, match="cycle"):
        Execution(root).compile()


def test_dynamically_returned_graph_keeps_its_identity_and_condition() -> None:
    forbidden = Step(lambda: pytest.fail("inactive returned graph ran"))
    child = Graph(
        GraphStructure(nodes=(forbidden,)), label="returned", condition=lambda: False
    )
    expand = Step(lambda: child, label="expand")
    events: list[NodeExecutionEvent] = []

    class Events:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    execution = Execution(expand, events=Events())
    run_execution(execution)
    snapshot = execution.execution_state.snapshot()
    assert snapshot.skipped_steps
    assert any(
        event.node_id == child.id and event.state is NodeExecutionState.SKIPPED
        for event in events
    )


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_forwarded_result_ports_keep_ownership_and_fan_out(backend: Backend) -> None:
    produce = Step(lambda: None, label="produce")
    inner = Graph(
        GraphStructure(
            nodes=(produce,),
            interface=GraphInterface(outputs={"value": produce}),
        )
    )
    producer = Graph.from_wiring((inner.wire(),), interface=inner, label="producer")
    seen: list[object] = []
    first = Step(lambda ctx, value: seen.append(value.unwrap()))
    second = Step(lambda ctx, value: seen.append(value.unwrap()))
    inner_consumer = Graph(
        GraphStructure(
            nodes=(first, second),
            interface=GraphInterface(inputs={"apply": (first, second)}),
        )
    )
    consumer = Graph.from_wiring((inner_consumer.wire(),), interface=inner_consumer)
    root = Graph(
        GraphStructure(
            nodes=(producer, consumer),
            edges=Edge.results(
                (producer.interface.outputs["value"],),
                consumer.interface.inputs["apply"],
            ),
        )
    )
    run(root, backend)
    assert seen == [None, None]
    document = export_graph(root)
    edge = next(edge for edge in document["edges"] if edge["kind"] == "result")
    assert edge["source_port"] == "value"
    assert edge["target_port"] == "apply"
    assert len(edge["consumer_nodes"]) == 2
    assert "from value to apply" in render_graph(root, root_label="root")
    assert "from value to apply" in render_graph_mermaid(root, root_label="root")


def test_statically_removed_exported_result_is_not_a_missing_runtime_value() -> None:
    produce = Step(lambda: 1)
    producer = Graph(
        GraphStructure(
            nodes=(produce,),
            interface=GraphInterface(outputs={"value": produce}),
            static=StaticConditions(
                evaluator=lambda condition: condition.payload is True,
                conditions={
                    produce: StaticCondition(payload=False, description="disabled")
                },
            ),
        )
    )
    consumer = Step(lambda ctx, value: value.unwrap())
    root = Graph(
        GraphStructure(
            nodes=(producer, consumer),
            edges=Edge.results((producer.interface.outputs["value"],), consumer),
        )
    )
    with pytest.raises(GraphError, match="removed by static"):
        Execution(root).compile()
