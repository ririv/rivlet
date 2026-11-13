from __future__ import annotations

import asyncio
import os
import sys

import pytest

from rivlet import (
    AsyncRunOptions,
    ConditionalPrerequisite,
    Edge,
    ExactProviderRequirement,
    Execution,
    ExecutionControl,
    ExecutionState,
    ExecutionStopped,
    Graph,
    GraphInterface,
    GraphNodeFailure,
    GraphStructure,
    Leaf,
    NodePath,
    SerialGraphExecutor,
    ThreadRunOptions,
    render_execution,
    render_live_execution,
    resolve_port_requirements,
)
from rivlet._native import _rust_process_graph


@pytest.fixture(params=["serial", "thread", "async"])
def execute(request):
    def run(graph: Graph, state: ExecutionState) -> None:
        if request.param == "serial":
            SerialGraphExecutor(state).execute(
                Execution(Graph(structure=graph.structure)).compile()
            )
        else:
            options = (
                ThreadRunOptions(workers=1)
                if request.param == "thread"
                else AsyncRunOptions(concurrency=1)
            )
            Execution(root=Graph(structure=graph.structure), execution_state=state).run(
                options
            )

    return run


def fail() -> None:
    raise RuntimeError("action failed")


def test_failure_blocks_only_downstream_and_renders_terminal_states(execute):
    calls = []
    failed = Leaf(fail, label=("failed"))
    blocked = Leaf(lambda: calls.append("blocked"), label=("blocked"))
    good = Leaf(lambda: calls.append("good"), label=("good"))
    later = Leaf(lambda: calls.append("later"), label=("later"))
    graph = Graph.from_wiring(
        (
            failed.wire(),
            blocked.wire(after=(failed,)),
            good.wire(),
            later.wire(after=(good,)),
        )
    )
    state = ExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["good", "later"]
    assert state.node_failures == {
        NodePath((0,)): GraphNodeFailure.FAILED,
        NodePath(
            (
                0,
                0,
            )
        ): GraphNodeFailure.FAILED,
        NodePath(
            (
                0,
                1,
            )
        ): GraphNodeFailure.BLOCKED,
    }
    assert state.skipped_steps == set()
    rendered = render_execution(
        Graph(structure=graph.structure, label="root"),
        state,
    )
    assert "failed [failed]" in rendered
    assert "blocked [blocked]" in rendered
    assert "remaining=0" in render_live_execution(
        Graph(structure=graph.structure, label="root"),
        state,
    )


def test_multiple_independent_errors_are_reported_together(execute):
    def other() -> None:
        raise LookupError("other failed")

    graph = Graph(
        structure=GraphStructure(
            nodes=(Leaf(fail, label="first"), Leaf(other, label="second"))
        )
    )
    with pytest.raises(ExceptionGroup) as caught:
        execute(graph, ExecutionState())
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
        structure=GraphStructure(
            nodes=(
                Leaf(lambda: calls.append("wrong-a"), condition=condition),
                Leaf(lambda: calls.append("wrong-b"), condition=condition),
                Leaf(lambda: calls.append("good")),
            )
        )
    )
    state = ExecutionState()
    with pytest.raises(ExceptionGroup) as caught:
        execute(graph, state)
    assert len(caught.value.exceptions) == 2
    assert calls == ["condition", "condition", "good"]
    assert state.node_failures == {
        NodePath((0,)): GraphNodeFailure.FAILED,
        NodePath(
            (
                0,
                0,
            )
        ): GraphNodeFailure.BLOCKED,
        NodePath(
            (
                0,
                1,
            )
        ): GraphNodeFailure.BLOCKED,
    }


def test_condition_does_not_read_failed_action_result(execute):
    calls = []
    producer = Leaf(fail)

    def needs_result(_ctx) -> bool:
        calls.append("wrong-condition")
        return True

    graph = Graph(
        structure=GraphStructure(
            nodes=(
                producer,
                (
                    consumer_134 := Leaf(
                        lambda _ctx, *_inputs: None,
                        condition=(_needs_result_condition := (needs_result, producer)),
                    )
                ),
                Leaf(lambda: calls.append("good")),
            ),
            edges=(*Edge.results((producer,), consumer_134),),
        )
    )
    state = ExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["good"]
    assert (
        state.node_failures[
            NodePath(
                (
                    0,
                    1,
                )
            )
        ]
        is GraphNodeFailure.BLOCKED
    )


def test_inactive_conditional_edge_does_not_propagate_failure(execute):
    calls = []
    producer = Leaf(fail)

    def disabled() -> bool:
        return False

    consumer = Leaf(lambda: calls.append("consumer"))
    graph = Graph.from_wiring(
        (
            producer.wire(),
            consumer.wire(
                after=(ConditionalPrerequisite(producer, condition=disabled),)
            ),
        )
    )
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, ExecutionState())
    assert calls == ["consumer"]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_port_success_survives_failure_in_another_subgraph_branch(mode):
    calls = []
    ready = Leaf(lambda: calls.append("ready"))
    port = "ready"
    provider = Graph(
        structure=GraphStructure(
            nodes=(Leaf(fail), ready), interface=GraphInterface(outputs={port: ready})
        ),
        label="provider",
    )
    consume = Leaf(lambda: calls.append("consume"))
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={port: consume})
        ),
        label="consumer",
    )
    whole = Leaf(lambda: calls.append("wrong-whole"))
    bindings = resolve_port_requirements(
        (provider, consumer),
        (ExactProviderRequirement(consumer=consumer, provider=provider, port=port),),
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(provider, consumer, whole),
            edges=(*bindings, Edge(source=provider, target=whole)),
        )
    )
    plan = Execution(root=Graph(structure=graph.structure))
    with pytest.raises(RuntimeError, match="action failed"):
        plan.run(ThreadRunOptions(workers=1), mode=mode)
    assert calls == ["ready", "consume"]
    assert (
        plan.execution_state.node_failures[
            NodePath(
                (
                    0,
                    0,
                )
            )
        ]
        is GraphNodeFailure.FAILED
    )
    assert (
        plan.execution_state.node_failures[
            NodePath(
                (
                    0,
                    2,
                )
            )
        ]
        is GraphNodeFailure.BLOCKED
    )


def test_blocked_composite_marks_descendants_without_entering_them(execute):
    calls = []
    producer = Leaf(fail)
    inner = Graph(
        structure=GraphStructure(
            nodes=(
                Graph(
                    structure=GraphStructure(
                        nodes=(Leaf(lambda: calls.append("wrong")),)
                    )
                ),
            )
        )
    )
    graph = Graph.from_wiring((producer.wire(), inner.wire(after=(producer,))))
    state = ExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == []
    assert state.entered_composites == {NodePath((0,))}
    assert all(
        state.node_failures[NodePath((0, *path))] is GraphNodeFailure.BLOCKED
        for path in [(1,), (1, 0), (1, 0, 0)]
    )


def test_failed_port_blocks_input_but_not_consumer_preparation(execute):
    calls = []
    port = "ready"
    ready = Leaf(fail)
    provider = Graph(
        structure=GraphStructure(
            nodes=(ready,), interface=GraphInterface(outputs={port: ready})
        )
    )
    prepare = Leaf(lambda: calls.append("prepare"))
    consume = Leaf(lambda: calls.append("wrong"))
    consumer = (prepare >> consume).graph(
        interface=GraphInterface(inputs={port: consume}),
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(provider, consumer),
            edges=resolve_port_requirements(
                (provider, consumer),
                (
                    ExactProviderRequirement(
                        consumer=consumer, provider=provider, port=port
                    ),
                ),
            ),
        )
    )
    state = ExecutionState()
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, state)
    assert calls == ["prepare"]
    assert state.node_failures[NodePath((0, 1, 1))] is GraphNodeFailure.BLOCKED
    assert (
        NodePath(
            (
                0,
                1,
            )
        )
        not in state.completed_composites
    )


def test_dynamic_returned_graph_failure_is_local_to_its_parent_action(execute):
    calls = []
    inner = Graph(
        structure=GraphStructure(
            nodes=(Leaf(fail), Leaf(lambda: calls.append("inner")))
        )
    )
    parent = Leaf(lambda: inner.structure)
    graph = Graph.from_wiring(
        (
            parent.wire(),
            Leaf(lambda: calls.append("wrong")).wire(after=(parent,)),
            Leaf(lambda: calls.append("outer")).wire(),
        )
    )
    with pytest.raises(RuntimeError, match="action failed"):
        execute(graph, ExecutionState())
    assert calls == ["inner", "outer"]


def test_native_failure_preserves_successes_in_same_completion_batch(execute, tmp_path):
    native = _rust_process_graph(
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
    graph = Graph(
        structure=GraphStructure(
            nodes=(Graph(structure=native), Leaf(lambda: calls.append("python")))
        )
    )
    with pytest.raises(RuntimeError, match=r"exited with status .*7"):
        execute(graph, ExecutionState())
    assert (tmp_path / "success").is_file()
    assert calls == ["python"]


@pytest.mark.parametrize(
    "options", [ThreadRunOptions(workers=1), AsyncRunOptions(concurrency=1)]
)
def test_explicit_stop_still_prevents_unrelated_pending_actions(options):
    calls = []
    control = ExecutionControl()
    graph = Graph(
        structure=GraphStructure(
            nodes=(Leaf(control.request_stop), Leaf(lambda: calls.append("wrong")))
        )
    )
    plan = Execution(root=Graph(structure=graph.structure), execution_control=control)
    with pytest.raises(ExecutionStopped):
        plan.run(options)
    assert calls == []
    assert control.stop_requested()


def test_cancellation_from_condition_waits_for_started_async_action():
    started = asyncio.Event()
    completed = []
    control = ExecutionControl()

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

    ready = Leaf(prepare)
    graph = Graph(
        structure=GraphStructure(
            nodes=(
                Leaf(survive),
                ready,
                (
                    consumer_337 := Leaf(
                        lambda _ctx, *_inputs: None,
                        condition=(_cancel_condition := (cancel, ready)),
                    )
                ),
            ),
            edges=(*Edge.results((ready,), consumer_337),),
        )
    )
    plan = Execution(root=Graph(structure=graph.structure), execution_control=control)
    with pytest.raises(ExecutionStopped):
        plan.run(AsyncRunOptions(concurrency=2))
    assert completed == ["survived"]


@pytest.mark.parametrize(
    "options", [ThreadRunOptions(workers=3), AsyncRunOptions(concurrency=3)]
)
def test_parallel_native_errors_are_collected_without_losing_completions(
    options, tmp_path
):
    native = _rust_process_graph(
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
    plan = Execution(
        root=Graph(
            structure=GraphStructure(nodes=(Graph(structure=native),)), label="root"
        )
    )
    with pytest.raises(ExceptionGroup) as caught:
        plan.run(options)
    assert len(caught.value.exceptions) == 2
    assert (tmp_path / "success").is_file()
    assert not plan.execution_control.stop_requested()
