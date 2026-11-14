from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Never

import pytest
from execution_support import run_async_execution

from rivlet import (
    ActionFailed,
    BlockedByCondition,
    BlockedByConditionInput,
    BlockedByDependency,
    BlockedByParent,
    ConditionalPrerequisite,
    EdgeConditionSite,
    ExactProviderRequirement,
    Execution,
    Graph,
    GraphInterface,
    GraphMode,
    GraphStructure,
    NodeConditionSite,
    NodeExecutionEvent,
    NodeExecutionState,
    NodeExecutionStateDetail,
    NodePath,
    Option,
    SerialGraphExecutor,
    Step,
    SubgraphFailed,
    resolve_port_requirements,
)
from rivlet import run as run_execution
from rivlet._native import _rust_process_graph


@dataclass
class Events:
    items: list[NodeExecutionEvent] = field(default_factory=list)

    def emit(self, event: NodeExecutionEvent) -> None:
        self.items.append(event)

    def failures(self) -> dict[NodePath, NodeExecutionStateDetail]:
        failures = {
            event.node_path: event.state_detail
            for event in self.items
            if event.state in (NodeExecutionState.FAILED, NodeExecutionState.BLOCKED)
        }
        root_failure = failures.pop(NodePath((0,)))
        assert isinstance(root_failure, SubgraphFailed)
        assert root_failure.source.parent == NodePath((0,))
        return failures


type Execute = Callable[[Graph, Events, GraphMode], Execution]


@pytest.fixture(params=["serial", "threaded", "async"])
def execute(request: pytest.FixtureRequest) -> Execute:
    def run(graph: Graph, events: Events, mode: GraphMode) -> Execution:
        execution = Execution(Graph(structure=graph.structure), events=events)
        with pytest.raises((RuntimeError, ExceptionGroup)):
            match request.param:
                case "serial":
                    SerialGraphExecutor(
                        execution.execution_state,
                        record_composites_as_steps=mode == "dynamic",
                    ).execute(execution.compile())
                case "threaded":
                    run_execution(execution, max_workers=3, mode=mode)
                case "async":
                    run_async_execution(execution, max_workers=3, mode=mode)
                case _:
                    pytest.fail("unknown test driver")
        return execution

    return run


def fail() -> Never:
    raise RuntimeError("secret-should-not-enter-events")


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_failure_events_describe_direct_causes_without_exceptions(
    execute: Execute, mode: GraphMode
) -> None:
    source, blocked, later = Step(fail), Step(lambda: None), Step(lambda: None)
    good = Step(lambda: None)
    graph = ((source >> blocked >> later) | good).graph()
    events = Events()
    execution = execute(graph, events, mode)
    assert events.failures() == {
        NodePath(
            (
                0,
                0,
            )
        ): ActionFailed(),
        NodePath(
            (
                0,
                1,
            )
        ): BlockedByDependency(
            NodePath(
                (
                    0,
                    0,
                )
            )
        ),
        NodePath(
            (
                0,
                2,
            )
        ): BlockedByDependency(
            NodePath(
                (
                    0,
                    1,
                )
            )
        ),
    }
    assert (
        NodePath(
            (
                0,
                3,
            )
        )
        in execution.execution_state.completed_steps
    )
    assert "secret-should-not-enter-events" not in repr(events.items)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("edge", [False, True])
def test_predicate_failure_is_not_an_action_or_dependency_failure(
    execute: Execute, mode: GraphMode, edge: bool
) -> None:
    condition = fail
    source = Step(lambda: None)
    target = Step(lambda: None, condition=(None if edge else condition))
    after = (ConditionalPrerequisite(source, condition=condition),) if edge else ()
    graph = Graph.from_wiring((source.wire(), target.wire(after=after)))
    events = Events()
    execute(graph, events, mode)
    causes = events.failures()
    assert set(causes) == {
        NodePath(
            (
                0,
                1,
            )
        )
    }
    cause = causes[
        NodePath(
            (
                0,
                1,
            )
        )
    ]
    assert isinstance(cause, BlockedByCondition)
    site = cause.condition
    assert site.condition_index >= 0
    if edge:
        assert isinstance(site, EdgeConditionSite)
        assert site.prerequisite == NodePath(
            (
                0,
                0,
            )
        )
    else:
        assert isinstance(site, NodeConditionSite)
    assert not any(
        event.node_path
        == NodePath(
            (
                0,
                1,
            )
        )
        and event.state is NodeExecutionState.STARTED
        for event in events.items
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("edge", [False, True])
def test_failed_condition_input_does_not_claim_the_predicate_ran(
    execute: Execute, mode: GraphMode, edge: bool
) -> None:
    def should_not_run(_value: Option[object]) -> bool:
        pytest.fail("predicate must not read a failed producer")

    condition = should_not_run
    source, predecessor = Step(fail), Step(lambda: None)
    target = Step(lambda: None, condition=(None if edge else (condition, source)))
    after = (
        (ConditionalPrerequisite(predecessor, condition=(condition, source)),)
        if edge
        else ()
    )
    graph = Graph.from_wiring(
        (
            source.wire(),
            predecessor.wire(),
            target.wire(
                after=after,
            ),
        )
    )
    events = Events()
    execute(graph, events, mode)
    cause = events.failures()[
        NodePath(
            (
                0,
                2,
            )
        )
    ]
    assert isinstance(cause, BlockedByConditionInput)
    assert cause.source == NodePath(
        (
            0,
            0,
        )
    )
    if edge:
        assert isinstance(cause.condition, EdgeConditionSite)
        assert cause.condition.prerequisite == NodePath(
            (
                0,
                1,
            )
        )
    else:
        assert isinstance(cause.condition, NodeConditionSite)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_false_edge_does_not_report_a_failed_but_unused_predecessor(
    execute: Execute, mode: GraphMode
) -> None:
    source = Step(fail)
    target = Step(lambda: None)

    def condition():
        return False

    graph = Graph.from_wiring(
        (
            source.wire(),
            target.wire(after=(ConditionalPrerequisite(source, condition=condition),)),
        )
    )
    events = Events()
    execution = execute(graph, events, mode)
    assert events.failures() == {
        NodePath(
            (
                0,
                0,
            )
        ): ActionFailed()
    }
    assert (
        NodePath(
            (
                0,
                1,
            )
        )
        in execution.execution_state.completed_steps
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_unentered_subgraph_children_reference_their_blocked_parent(
    execute: Execute, mode: GraphMode
) -> None:
    source = Step(fail)
    inner = Graph(structure=GraphStructure(nodes=(Step(lambda: None),)))
    container = Graph(structure=GraphStructure(nodes=(inner,)))
    events = Events()
    execute((source >> container).graph(), events, mode)
    assert events.failures() == {
        NodePath(
            (
                0,
                0,
            )
        ): ActionFailed(),
        NodePath(
            (
                0,
                1,
            )
        ): BlockedByDependency(
            NodePath(
                (
                    0,
                    0,
                )
            )
        ),
        NodePath((0, 1, 0)): BlockedByParent(
            NodePath(
                (
                    0,
                    1,
                )
            )
        ),
        NodePath((0, 1, 0, 0)): BlockedByParent(NodePath((0, 1, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_repeated_subgraphs_keep_failure_sources_in_their_own_mount(
    execute: Execute, mode: GraphMode, dynamic: bool
) -> None:
    child = Graph(structure=GraphStructure(nodes=(Step(fail),)))
    left = (
        Step(lambda: child.structure) if dynamic else Graph(structure=child.structure)
    )
    right = (
        Step(lambda: child.structure) if dynamic else Graph(structure=child.structure)
    )
    events = Events()
    execute((left | right).graph(), events, mode)
    assert events.failures() == {
        NodePath((0, 0, 0)): ActionFailed(),
        NodePath(
            (
                0,
                0,
            )
        ): SubgraphFailed(NodePath((0, 0, 0))),
        NodePath((0, 1, 0)): ActionFailed(),
        NodePath(
            (
                0,
                1,
            )
        ): SubgraphFailed(NodePath((0, 1, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_dynamic_failure_chain_preserves_each_session_boundary(
    execute: Execute, mode: GraphMode
) -> None:
    grandchild = Graph(structure=GraphStructure(nodes=(Step(fail),)))
    child = Graph(structure=GraphStructure(nodes=(Step(lambda: grandchild.structure),)))
    events = Events()
    execute(
        Graph(structure=GraphStructure(nodes=(Step(lambda: child.structure),))),
        events,
        mode,
    )
    assert events.failures() == {
        NodePath((0, 0, 0, 0)): ActionFailed(),
        NodePath((0, 0, 0)): SubgraphFailed(NodePath((0, 0, 0, 0))),
        NodePath(
            (
                0,
                0,
            )
        ): SubgraphFailed(NodePath((0, 0, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_port_failure_points_to_the_actual_provider_step(
    execute: Execute, mode: GraphMode
) -> None:
    port = "ready"
    provide, consume = Step(fail), Step(lambda: None)
    provider = Graph(
        structure=GraphStructure(
            nodes=(provide,), interface=GraphInterface(outputs={port: provide})
        )
    )
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={port: consume})
        )
    )
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=port),),
    )
    events = Events()
    execute(
        Graph(structure=GraphStructure(nodes=(provider, consumer), edges=bindings)),
        events,
        mode,
    )
    assert events.failures()[NodePath((0, 1, 0))] == BlockedByDependency(
        NodePath((0, 0, 0))
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_provider_failure_after_ready_does_not_retroactively_block_consumer(
    execute: Execute, mode: GraphMode
) -> None:
    port = "ready"
    ready, late_failure, consume = Step(lambda: None), Step(fail), Step(lambda: None)
    provider = (ready >> late_failure).graph(
        interface=GraphInterface(outputs={port: ready}),
    )
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={port: consume})
        )
    )
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=port),),
    )
    events = Events()
    execution = execute(
        Graph(structure=GraphStructure(nodes=(provider, consumer), edges=bindings)),
        events,
        mode,
    )
    assert events.failures() == {
        NodePath((0, 0, 1)): ActionFailed(),
        NodePath(
            (
                0,
                0,
            )
        ): SubgraphFailed(NodePath((0, 0, 1))),
    }
    assert NodePath((0, 1, 0)) in execution.execution_state.completed_steps


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_native_action_failure_uses_the_same_causal_model(
    execute: Execute, mode: GraphMode, tmp_path: Path
) -> None:
    native = _rust_process_graph(
        [("fail", sys.executable, ["-c", "raise SystemExit(7)"], str(tmp_path), {})],
        [],
    )
    events = Events()
    execute(
        Graph(structure=GraphStructure(nodes=(Graph(structure=native),))),
        events,
        mode,
    )
    assert events.failures() == {
        NodePath((0, 0, 0)): ActionFailed(),
        NodePath(
            (
                0,
                0,
            )
        ): SubgraphFailed(NodePath((0, 0, 0))),
    }
