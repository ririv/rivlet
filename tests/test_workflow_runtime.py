from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

import pytest

from rivlet import (
    AsyncRunOptions,
    EventDeliveryError,
    Execution,
    ExecutionControl,
    ExecutionState,
    ExecutionStopped,
    Graph,
    GraphStructure,
    NodeExecutionEvent,
    NodeExecutionState,
    NodePath,
    NodeRunContext,
    ResultStore,
    SerialGraphExecutor,
    Step,
    ThreadRunOptions,
)


@dataclass
class Activity:
    active: int = 0
    peak: int = 0
    completed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def work(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.002)
        finally:
            with self.lock:
                self.active -= 1
                self.completed += 1


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("limit", [1, 3])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_dynamic_subgraphs_share_one_action_budget(mode, limit, asynchronous) -> None:
    activity = Activity()

    def branch(depth: int) -> Graph:
        if depth == 0:
            return Graph(
                structure=GraphStructure(
                    nodes=tuple(Step(activity.work) for _ in range(4))
                )
            )
        return Graph(
            structure=GraphStructure(
                nodes=tuple(Step(lambda: branch(depth - 1)) for _ in range(3))
            )
        )

    graph = branch(2)
    execution = Execution(Graph(structure=graph.structure))
    options = (
        AsyncRunOptions(concurrency=limit)
        if asynchronous
        else ThreadRunOptions(workers=limit)
    )
    execution.run(options, mode=mode)
    assert activity.completed == 36
    assert activity.active == 0
    assert 1 <= activity.peak <= limit


def test_arun_uses_the_callers_event_loop() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        seen = []

        async def action(ctx: NodeRunContext) -> str:
            ctx.control.require_running()
            seen.append(asyncio.get_running_loop())
            await asyncio.sleep(0)
            return "done"

        step = Step(action)
        execution = Execution(
            Graph(structure=GraphStructure(nodes=(step,))),
            execution_state=ExecutionState(results=ResultStore()),
        )
        await execution.arun(AsyncRunOptions(concurrency=2))
        assert seen == [loop]
        assert execution.require_result(step).value == "done"

    asyncio.run(scenario())


def test_events_report_committed_transitions_without_reclassifying_sink_failure() -> (
    None
):
    events: list[NodeExecutionEvent] = []

    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)
            if event.state is NodeExecutionState.COMPLETED:
                assert event.node_path in execution.execution_state.completed_steps
                raise ValueError("sink unavailable")

    execution = Execution(
        Graph(structure=GraphStructure(nodes=(Step(lambda: 1, label="task"),))),
        events=Sink(),
    )
    with pytest.raises(EventDeliveryError, match="step event delivery failed"):
        execution.run()
    assert [event.state for event in events] == [
        NodeExecutionState.STARTED,
        NodeExecutionState.STARTED,
        NodeExecutionState.COMPLETED,
    ]
    assert events[1].node_path.labels == ("0", "task")
    assert execution.execution_state.node_failures == {}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_events_and_failure_propagation_cross_dynamic_boundaries(asynchronous) -> None:
    events: list[NodeExecutionEvent] = []
    calls = []

    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    def fail() -> None:
        raise ValueError("action failed")

    failed = Step(fail, label=("failed"))
    blocked = Step(lambda: calls.append("blocked"), label=("blocked"))
    child = (failed >> blocked).graph()
    parent = Step(lambda: child.structure, label=("parent"))
    downstream = Step(lambda: calls.append("downstream"), label=("downstream"))
    independent = Step(lambda: calls.append("independent"), label=("independent"))
    graph = Graph.from_wiring(
        wiring=(parent.wire(), downstream.wire(after=(parent,)), independent.wire())
    )
    execution = Execution(Graph(structure=graph.structure), events=Sink())
    with pytest.raises(ValueError, match="action failed"):
        execution.run(
            AsyncRunOptions(concurrency=2)
            if asynchronous
            else ThreadRunOptions(workers=2)
        )
    assert calls == ["independent"]
    terminal = {event.node_path.labels: event.state for event in events}
    assert terminal[("0",)] is NodeExecutionState.FAILED
    assert terminal[("0", "parent", "failed")] is NodeExecutionState.FAILED
    assert terminal[("0", "parent", "blocked")] is NodeExecutionState.BLOCKED
    assert (
        terminal[
            (
                "0",
                "parent",
            )
        ]
        is NodeExecutionState.FAILED
    )
    assert (
        terminal[
            (
                "0",
                "downstream",
            )
        ]
        is NodeExecutionState.BLOCKED
    )
    assert (
        terminal[
            (
                "0",
                "independent",
            )
        ]
        is NodeExecutionState.COMPLETED
    )


def test_skip_event_has_a_label_without_running_the_action() -> None:
    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    events: list[NodeExecutionEvent] = []
    execution = Execution(
        Graph(
            structure=GraphStructure(
                nodes=(Step(lambda: pytest.fail("must be skipped"), label="skip"),)
            )
        ),
        execution_state=ExecutionState(
            initial_skip_requests=(
                NodePath(
                    (
                        0,
                        0,
                    )
                ),
            )
        ),
        events=Sink(),
    )
    execution.run()
    assert [(event.node_path.labels, event.state) for event in events] == [
        (("0",), NodeExecutionState.STARTED),
        (
            (
                "0",
                "skip",
            ),
            NodeExecutionState.SKIPPED,
        ),
        (("0",), NodeExecutionState.COMPLETED),
    ]


def test_arun_cancellation_waits_for_cooperative_action_cleanup() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def action(ctx: NodeRunContext) -> None:
            entered.set()
            try:
                while True:
                    ctx.control.require_running()
                    await asyncio.sleep(0)
            finally:
                cleaned.set()

        execution = Execution(Graph(structure=GraphStructure(nodes=(Step(action),))))
        task = asyncio.create_task(execution.arun())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        assert execution.execution_control.stop_requested()

    asyncio.run(scenario())


def test_threaded_action_observes_external_stop() -> None:
    control = ExecutionControl()
    entered = threading.Event()
    cleaned = threading.Event()

    def action(ctx: NodeRunContext) -> None:
        entered.set()
        try:
            while not ctx.control.stop_requested():
                time.sleep(0.001)
            ctx.control.require_running()
        finally:
            cleaned.set()

    def stop() -> None:
        assert entered.wait(timeout=2)
        control.request_stop()

    thread = threading.Thread(target=stop)
    thread.start()
    try:
        with pytest.raises(ExecutionStopped):
            Execution(
                Graph(structure=GraphStructure(nodes=(Step(action),))),
                execution_control=control,
            ).run()
    finally:
        thread.join(timeout=2)
    assert cleaned.is_set()


def test_stop_handler_registered_after_stop_runs_immediately() -> None:
    control = ExecutionControl()
    control.request_stop()
    calls = []
    control.register_stop_handler(lambda: calls.append("closed"))
    assert calls == ["closed"]


def test_no_subscription_constructs_no_events(monkeypatch) -> None:
    from rivlet.runtime import state

    def unexpected(*args, **kwargs):
        pytest.fail("disabled event delivery must not construct events")

    monkeypatch.setattr(state, "NodeExecutionEvent", unexpected)
    Execution(Graph(structure=GraphStructure(nodes=(Step(lambda: 1),)))).run()


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_composite_events_are_independent_of_presentation_mode(mode) -> None:
    events: list[NodeExecutionEvent] = []

    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    child = Graph(structure=GraphStructure(nodes=(Step(lambda: None, label="leaf"),)))
    Execution(
        Graph(
            structure=GraphStructure(
                nodes=(Graph(structure=child.structure, label="child"),)
            )
        ),
        events=Sink(),
    ).run(mode=mode)
    assert [(event.node_path.labels, event.state) for event in events] == [
        (("0",), NodeExecutionState.STARTED),
        (
            (
                "0",
                "child",
            ),
            NodeExecutionState.STARTED,
        ),
        (("0", "child", "leaf"), NodeExecutionState.STARTED),
        (("0", "child", "leaf"), NodeExecutionState.COMPLETED),
        (
            (
                "0",
                "child",
            ),
            NodeExecutionState.COMPLETED,
        ),
        (("0",), NodeExecutionState.COMPLETED),
    ]


def test_serial_dynamic_expansion_uses_an_explicit_stack() -> None:
    calls = []

    def expand(depth: int) -> Graph:
        return Graph(
            structure=GraphStructure(
                nodes=(
                    Step(lambda: calls.append("done"))
                    if depth == 0
                    else Step(lambda: expand(depth - 1)),
                )
            )
        )

    graph = expand(128)
    SerialGraphExecutor().execute(Execution(Graph(structure=graph.structure)).compile())
    assert calls == ["done"]
