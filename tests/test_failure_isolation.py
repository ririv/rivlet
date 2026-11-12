from __future__ import annotations

import asyncio
import os
import sys

import pytest

from rivlet import (
    AsyncGraphRunOptions,
    ConditionalPrerequisite,
    Edge,
    ExactProviderRequirement,
    Graph,
    GraphExecution,
    GraphExecutionControl,
    GraphExecutionState,
    GraphExecutionStopped,
    GraphInterface,
    GraphNodeFailure,
    ResultEdge,
    SerialGraphExecutor,
    Step,
    StepId,
    ThreadGraphRunOptions,
    compile_graph,
    render_graph_execution,
    render_live_graph_execution,
    resolve_port_requirements,
)
from rivlet._native import _native_process_graph


@pytest.fixture(params=["serial", "thread", "async"])
def execute(request):
    def run(graph: Graph, state: GraphExecutionState) -> None:
        if request.param == "serial":
            SerialGraphExecutor(state).execute(compile_graph(graph, root_label="root"))
        else:
            options = (
                ThreadGraphRunOptions(workers=1)
                if request.param == "thread"
                else AsyncGraphRunOptions(concurrency=1)
            )
            GraphExecution(graph=graph, execution_state=state).run(options)

    return run


def fail() -> None:
    raise RuntimeError("action failed")


def test_failure_blocks_only_downstream_and_renders_terminal_states(execute):
    calls = []
    failed = Step(fail, label="failed")
    blocked = Step(lambda: calls.append("blocked"), label="blocked")
    good = Step(lambda: calls.append("good"), label="good")
    later = Step(lambda: calls.append("later"), label="later")
    graph = Graph.from_wiring(
        (
            failed.wire(),
            blocked.wire(after=(failed,)),
            good.wire(),
            later.wire(after=(good,)),
        )
    )
    state = GraphExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["good", "later"]
    assert state.node_failures == {
        StepId((0,)): GraphNodeFailure.FAILED,
        StepId((1,)): GraphNodeFailure.BLOCKED,
    }
    assert state.skipped_steps == set()
    rendered = render_graph_execution(graph, state, root_label="root")
    assert "failed [failed]" in rendered
    assert "blocked [blocked]" in rendered
    assert "remaining=0" in render_live_graph_execution(graph, state, root_label="root")


def test_multiple_independent_errors_are_reported_together(execute):
    def other() -> None:
        raise LookupError("other failed")

    graph = Graph(steps=(Step(fail, label="first"), Step(other, label="second")))
    with pytest.raises(ExceptionGroup) as caught:
        execute(graph, GraphExecutionState())
    assert {str(error) for error in caught.value.exceptions} == {
        "action failed",
        "other failed",
    }
    assert all(error.__notes__ for error in caught.value.exceptions)


def test_condition_exception_blocks_all_users_without_stopping_other_work(execute):
    calls = []

    def broken() -> bool:
        calls.append("condition")
        raise RuntimeError("predicate failed")

    condition = broken
    graph = Graph(
        steps=(
            Step(lambda: calls.append("wrong-a"), condition=condition),
            Step(lambda: calls.append("wrong-b"), condition=condition),
            Step(lambda: calls.append("good")),
        )
    )
    state = GraphExecutionState()
    with pytest.raises(ExceptionGroup) as caught:
        execute(graph, state)
    assert len(caught.value.exceptions) == 2
    assert calls == ["condition", "condition", "good"]
    assert state.node_failures == {
        StepId((0,)): GraphNodeFailure.BLOCKED,
        StepId((1,)): GraphNodeFailure.BLOCKED,
    }


def test_condition_does_not_read_failed_action_result(execute):
    calls = []
    producer = Step(fail)

    def needs_result(_ctx) -> bool:
        calls.append("wrong-condition")
        return True

    graph = Graph(
        steps=(
            producer,
            (
                consumer_134 := Step(
                    lambda _ctx, *_inputs: None,
                    condition=(_needs_result_condition := (needs_result, producer)),
                )
            ),
            Step(lambda: calls.append("good")),
        ),
        edges=(ResultEdge(producer, consumer_134, 0, receiver=consumer_134.action),),
    )
    state = GraphExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["good"]
    assert state.node_failures[StepId((1,))] is GraphNodeFailure.BLOCKED


def test_inactive_conditional_edge_does_not_propagate_failure(execute):
    calls = []
    producer = Step(fail)

    def disabled() -> bool:
        return False

    consumer = Step(lambda: calls.append("consumer"))
    graph = Graph.from_wiring(
        (
            producer.wire(),
            consumer.wire(
                after=(ConditionalPrerequisite(producer, condition=disabled),)
            ),
        )
    )
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, GraphExecutionState())
    assert calls == ["consumer"]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_port_success_survives_failure_in_another_subgraph_branch(mode):
    calls = []
    ready = Step(lambda: calls.append("ready"))
    port = "ready"
    provider = Graph(
        steps=(Step(fail), ready),
        interface=GraphInterface(outputs={port: ready}),
    ).step(label="provider")
    consume = Step(lambda: calls.append("consume"))
    consumer = Graph(
        steps=(consume,),
        interface=GraphInterface(inputs={port: consume}),
    ).step(label="consumer")
    whole = Step(lambda: calls.append("wrong-whole"))
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=port),),
    )
    graph = Graph(
        steps=(provider, consumer, whole),
        edges=(*bindings, Edge(source=provider, target=whole)),
    )
    plan = GraphExecution(graph=graph)
    with pytest.raises(RuntimeError, match="action failed"):
        plan.run(ThreadGraphRunOptions(workers=1), mode=mode)
    assert calls == ["ready", "consume"]
    assert plan.execution_state.node_failures[StepId((0,))] is GraphNodeFailure.FAILED
    assert plan.execution_state.node_failures[StepId((2,))] is GraphNodeFailure.BLOCKED


def test_blocked_composite_marks_descendants_without_entering_them(execute):
    calls = []
    producer = Step(fail)
    inner = Graph(
        steps=(Graph(steps=(Step(lambda: calls.append("wrong")),)).step(),)
    ).step()
    graph = Graph.from_wiring((producer.wire(), inner.wire(after=(producer,))))
    state = GraphExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == []
    assert state.entered_composites == set()
    assert all(
        state.node_failures[StepId(path)] is GraphNodeFailure.BLOCKED
        for path in [(1,), (1, 0), (1, 0, 0)]
    )


def test_failed_port_blocks_input_but_not_consumer_preparation(execute):
    calls = []
    port = "ready"
    ready = Step(fail)
    provider = Graph(
        steps=(ready,), interface=GraphInterface(outputs={port: ready})
    ).step()
    prepare = Step(lambda: calls.append("prepare"))
    consume = Step(lambda: calls.append("wrong"))
    consumer = (
        (prepare >> consume)
        .graph(interface=GraphInterface(inputs={port: consume}))
        .step()
    )
    graph = Graph(
        steps=(provider, consumer),
        edges=resolve_port_requirements(
            (provider, consumer),
            (
                ExactProviderRequirement(
                    consumer=consumer, provider=provider, port=port
                ),
            ),
        ),
    )
    state = GraphExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["prepare"]
    assert state.node_failures[StepId((1, 1))] is GraphNodeFailure.BLOCKED
    assert StepId((1,)) not in state.completed_composites


def test_dynamic_returned_graph_failure_is_local_to_its_parent_action(execute):
    calls = []
    inner = Graph(steps=(Step(fail), Step(lambda: calls.append("inner"))))
    parent = Step(lambda: inner)
    graph = Graph.from_wiring(
        (
            parent.wire(),
            Step(lambda: calls.append("wrong")).wire(after=(parent,)),
            Step(lambda: calls.append("outer")).wire(),
        )
    )
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, GraphExecutionState())
    assert calls == ["inner", "outer"]


def test_native_failure_preserves_successes_in_same_completion_batch(execute, tmp_path):
    native = _native_process_graph(
        "native",
        [
            (
                "fail",
                sys.executable,
                ["-c", "raise SystemExit(7)"],
                str(tmp_path),
                dict(os.environ),
            ),
            (
                "succeed",
                sys.executable,
                ["-c", "from pathlib import Path; Path('success').touch()"],
                str(tmp_path),
                dict(os.environ),
            ),
        ],
        [],
    )
    calls = []
    graph = Graph(steps=(Step(native), Step(lambda: calls.append("python"))))
    with pytest.raises(RuntimeError, match=r"exited with status .*7"):
        execute(graph, GraphExecutionState())
    assert (tmp_path / "success").is_file()
    assert calls == ["python"]


@pytest.mark.parametrize(
    "options", [ThreadGraphRunOptions(workers=1), AsyncGraphRunOptions(concurrency=1)]
)
def test_explicit_stop_still_prevents_unrelated_pending_actions(options):
    calls = []
    control = GraphExecutionControl()
    graph = Graph(
        steps=(Step(control.request_stop), Step(lambda: calls.append("wrong")))
    )
    plan = GraphExecution(graph=graph, execution_control=control)
    with pytest.raises(GraphExecutionStopped):
        plan.run(options)
    assert calls == []
    assert control.stop_requested()


def test_cancellation_from_condition_waits_for_started_async_action():
    started = asyncio.Event()
    completed = []
    control = GraphExecutionControl()

    async def survive():
        started.set()
        await asyncio.sleep(0.02)
        completed.append("survived")

    async def prepare():
        await started.wait()

    def cancel(_ctx):
        control.request_stop()
        control.require_running()
        return True

    ready = Step(prepare)
    graph = Graph(
        steps=(
            Step(survive),
            ready,
            (
                consumer_337 := Step(
                    lambda _ctx, *_inputs: None,
                    condition=(_cancel_condition := (cancel, ready)),
                )
            ),
        ),
        edges=(ResultEdge(ready, consumer_337, 0, receiver=consumer_337.action),),
    )
    plan = GraphExecution(graph=graph, execution_control=control)
    with pytest.raises(GraphExecutionStopped):
        plan.run(AsyncGraphRunOptions(concurrency=2))
    assert completed == ["survived"]


@pytest.mark.parametrize(
    "options", [ThreadGraphRunOptions(workers=3), AsyncGraphRunOptions(concurrency=3)]
)
def test_parallel_native_errors_are_collected_without_losing_completions(
    options, tmp_path
):
    native = _native_process_graph(
        "native",
        [
            (
                "fail-a",
                sys.executable,
                ["-c", "raise SystemExit(7)"],
                str(tmp_path),
                dict(os.environ),
            ),
            (
                "fail-b",
                sys.executable,
                ["-c", "raise SystemExit(8)"],
                str(tmp_path),
                dict(os.environ),
            ),
            (
                "succeed",
                sys.executable,
                ["-c", "from pathlib import Path; Path('success').touch()"],
                str(tmp_path),
                dict(os.environ),
            ),
        ],
        [],
    )
    plan = GraphExecution(graph=Graph(steps=(Step(native),), label="root"))
    with pytest.raises(ExceptionGroup) as caught:
        plan.run(options)
    assert len(caught.value.exceptions) == 2
    assert (tmp_path / "success").is_file()
    assert not plan.execution_control.stop_requested()
