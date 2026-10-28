from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Never

import pytest

from rivlet import (
    ActionFailed,
    AsyncGraphRunOptions,
    BlockedByCondition,
    BlockedByConditionInput,
    BlockedByDependency,
    BlockedByParent,
    Capability,
    CapabilityInterface,
    ConditionalPrerequisite,
    EdgeConditionSite,
    ExactProviderRequirement,
    Graph,
    GraphExecution,
    GraphMode,
    NodeConditionSite,
    Option,
    SerialGraphExecutor,
    Step,
    StepExecutionEvent,
    StepExecutionPhase,
    StepId,
    StepTransition,
    SubgraphFailed,
    ThreadGraphRunOptions,
    resolve_capability_requirements,
    runtime_condition,
)
from rivlet._native import _native_process_graph


@dataclass
class Events:
    items: list[StepExecutionEvent] = field(default_factory=list)

    def emit(self, event: StepExecutionEvent) -> None:
        self.items.append(event)

    def failures(self) -> dict[StepId, StepTransition]:
        return {
            event.step_id: event.transition
            for event in self.items
            if event.phase in (StepExecutionPhase.FAILED, StepExecutionPhase.BLOCKED)
        }


type Execute = Callable[[Graph, Events, GraphMode], GraphExecution]


@pytest.fixture(params=["serial", "threaded", "async"])
def execute(request: pytest.FixtureRequest) -> Execute:
    def run(graph: Graph, events: Events, mode: GraphMode) -> GraphExecution:
        execution = GraphExecution(graph, events=events)
        with pytest.raises((RuntimeError, ExceptionGroup)):
            match request.param:
                case "serial":
                    SerialGraphExecutor(
                        execution.execution_state,
                        record_composites_as_steps=mode == "dynamic",
                    ).execute(execution.compile())
                case "threaded":
                    execution.run(ThreadGraphRunOptions(workers=3), mode=mode)
                case "async":
                    execution.run(AsyncGraphRunOptions(concurrency=3), mode=mode)
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
        StepId((0,)): ActionFailed(),
        StepId((1,)): BlockedByDependency(StepId((0,))),
        StepId((2,)): BlockedByDependency(StepId((1,))),
    }
    assert StepId((3,)) in execution.execution_state.completed_steps
    assert "secret-should-not-enter-events" not in repr(events.items)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("edge", [False, True])
def test_predicate_failure_is_not_an_action_or_dependency_failure(
    execute: Execute, mode: GraphMode, edge: bool
) -> None:
    condition = runtime_condition(when=fail)
    source = Step(lambda: None)
    target = Step(lambda: None, condition=None if edge else condition)
    after = (ConditionalPrerequisite(source, condition=condition),) if edge else ()
    graph = Graph.from_wiring((source.wire(), target.wire(after=after)))
    events = Events()
    execute(graph, events, mode)
    causes = events.failures()
    assert set(causes) == {StepId((1,))}
    cause = causes[StepId((1,))]
    assert isinstance(cause, BlockedByCondition)
    site = cause.condition
    assert site.condition_index >= 0
    if edge:
        assert isinstance(site, EdgeConditionSite)
        assert site.prerequisite == StepId((0,))
    else:
        assert isinstance(site, NodeConditionSite)
    assert not any(
        event.step_id == StepId((1,)) and event.phase is StepExecutionPhase.STARTED
        for event in events.items
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("edge", [False, True])
def test_failed_condition_input_does_not_claim_the_predicate_ran(
    execute: Execute, mode: GraphMode, edge: bool
) -> None:
    def should_not_run(_value: Option[object]) -> bool:
        pytest.fail("predicate must not read a failed producer")

    condition = runtime_condition(when=should_not_run)
    source, predecessor = Step(fail), Step(lambda: None)
    target = Step(lambda: None, condition=None if edge else condition)
    after = (ConditionalPrerequisite(predecessor, condition=condition),) if edge else ()
    graph = Graph.from_wiring(
        (
            source.wire(),
            predecessor.wire(),
            target.wire(after=after, inputs={condition: (source,)}),
        )
    )
    events = Events()
    execute(graph, events, mode)
    cause = events.failures()[StepId((2,))]
    assert isinstance(cause, BlockedByConditionInput)
    assert cause.source == StepId((0,))
    if edge:
        assert isinstance(cause.condition, EdgeConditionSite)
        assert cause.condition.prerequisite == StepId((1,))
    else:
        assert isinstance(cause.condition, NodeConditionSite)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_false_edge_does_not_report_a_failed_but_unused_predecessor(
    execute: Execute, mode: GraphMode
) -> None:
    source = Step(fail)
    target = Step(lambda: None)
    condition = runtime_condition(when=lambda: False)
    graph = Graph.from_wiring(
        (
            source.wire(),
            target.wire(after=(ConditionalPrerequisite(source, condition=condition),)),
        )
    )
    events = Events()
    execution = execute(graph, events, mode)
    assert events.failures() == {StepId((0,)): ActionFailed()}
    assert StepId((1,)) in execution.execution_state.completed_steps


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_unentered_subgraph_children_reference_their_blocked_parent(
    execute: Execute, mode: GraphMode
) -> None:
    source = Step(fail)
    inner = Graph(steps=(Step(lambda: None),)).step()
    container = Graph(steps=(inner,)).step()
    events = Events()
    execute((source >> container).graph(), events, mode)
    assert events.failures() == {
        StepId((0,)): ActionFailed(),
        StepId((1,)): BlockedByDependency(StepId((0,))),
        StepId((1, 0)): BlockedByParent(StepId((1,))),
        StepId((1, 0, 0)): BlockedByParent(StepId((1, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_repeated_subgraphs_keep_failure_sources_in_their_own_mount(
    execute: Execute, mode: GraphMode, dynamic: bool
) -> None:
    child = Graph(steps=(Step(fail),))
    left = Step(lambda: child) if dynamic else child.step()
    right = Step(lambda: child) if dynamic else child.step()
    events = Events()
    execute((left | right).graph(), events, mode)
    assert events.failures() == {
        StepId((0, 0)): ActionFailed(),
        StepId((0,)): SubgraphFailed(StepId((0, 0))),
        StepId((1, 0)): ActionFailed(),
        StepId((1,)): SubgraphFailed(StepId((1, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_dynamic_failure_chain_preserves_each_session_boundary(
    execute: Execute, mode: GraphMode
) -> None:
    grandchild = Graph(steps=(Step(fail),))
    child = Graph(steps=(Step(lambda: grandchild),))
    events = Events()
    execute(Graph(steps=(Step(lambda: child),)), events, mode)
    assert events.failures() == {
        StepId((0, 0, 0)): ActionFailed(),
        StepId((0, 0)): SubgraphFailed(StepId((0, 0, 0))),
        StepId((0,)): SubgraphFailed(StepId((0, 0))),
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_capability_failure_points_to_the_actual_provider_step(
    execute: Execute, mode: GraphMode
) -> None:
    capability = Capability("ready")
    provide, consume = Step(fail), Step(lambda: None)
    provider = Graph(
        steps=(provide,),
        capabilities=CapabilityInterface(outputs={capability: provide}),
    ).step()
    consumer = Graph(
        steps=(consume,), capabilities=CapabilityInterface(inputs={capability: consume})
    ).step()
    bindings = resolve_capability_requirements(
        (provider, consumer),
        (
            ExactProviderRequirement(
                consumer=consumer, provider=provider, capability=capability
            ),
        ),
    )
    events = Events()
    execute(Graph(steps=(provider, consumer), edges=bindings), events, mode)
    assert events.failures()[StepId((1, 0))] == BlockedByDependency(StepId((0, 0)))


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_provider_failure_after_ready_does_not_retroactively_block_consumer(
    execute: Execute, mode: GraphMode
) -> None:
    capability = Capability("ready")
    ready, late_failure, consume = Step(lambda: None), Step(fail), Step(lambda: None)
    provider = (
        (ready >> late_failure)
        .graph(capabilities=CapabilityInterface(outputs={capability: ready}))
        .step()
    )
    consumer = Graph(
        steps=(consume,), capabilities=CapabilityInterface(inputs={capability: consume})
    ).step()
    bindings = resolve_capability_requirements(
        (provider, consumer),
        (
            ExactProviderRequirement(
                consumer=consumer, provider=provider, capability=capability
            ),
        ),
    )
    events = Events()
    execution = execute(Graph(steps=(provider, consumer), edges=bindings), events, mode)
    assert events.failures() == {
        StepId((0, 1)): ActionFailed(),
        StepId((0,)): SubgraphFailed(StepId((0, 1))),
    }
    assert StepId((1, 0)) in execution.execution_state.completed_steps


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_native_action_failure_uses_the_same_causal_model(
    execute: Execute, mode: GraphMode, tmp_path: Path
) -> None:
    native = _native_process_graph(
        "native",
        [("fail", sys.executable, ["-c", "raise SystemExit(7)"], str(tmp_path), {})],
        [],
    )
    events = Events()
    execute(Graph(steps=(Step(native),)), events, mode)
    assert events.failures() == {
        StepId((0, 0)): ActionFailed(),
        StepId((0,)): SubgraphFailed(StepId((0, 0))),
    }
