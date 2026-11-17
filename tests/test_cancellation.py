from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest

from rivlet import (
    CancellationSource,
    Edge,
    Execution,
    ExecutionCancelled,
    ExecutionControl,
    Graph,
    GraphInterface,
    NodeExecutionEvent,
    NodeRunContext,
    SerialGraphExecutor,
    Step,
)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pre_cancel_does_not_invoke_actions_or_predicates(asynchronous: bool) -> None:
    source = CancellationSource()
    source.cancel()

    def forbidden() -> None:
        pytest.fail("取消后不应执行")

    step = Step(forbidden, condition=lambda: pytest.fail("取消后不应求值"))
    execution = Execution(step, cancellation=source.token)
    with ThreadPoolExecutor(1) as pool, pytest.raises(ExecutionCancelled) as caught:
        if asynchronous:
            asyncio.run(execution.arun(pool=pool))
        else:
            execution.run(pool=pool)
    assert caught.value.state is execution.execution_state
    assert execution.execution_state.cancelled_steps
    assert not execution.execution_state.skipped_steps
    assert not execution.execution_state.completed_steps


def test_async_action_cancellation_propagates_across_ports() -> None:
    async def main() -> None:
        source = CancellationSource()
        started = asyncio.Event()

        async def work(ctx: NodeRunContext) -> None:
            started.set()
            await ctx.control.wait_cancelled()
            ctx.control.require_running()

        producing = Step(work)
        provider = Graph(
            nodes=(producing,), interface=GraphInterface(outputs={None: producing})
        )
        consuming = Step(lambda _ctx, _value: pytest.fail("不应消费已取消的结果"))
        consumer = Graph(
            nodes=(consuming,), interface=GraphInterface(inputs={None: consuming})
        )
        graph = Graph(
            nodes=(provider, consumer),
            edges=Edge.results((provider.interface.output,), consumer.interface.input),
        )
        execution = Execution(graph, cancellation=source.token)
        with ThreadPoolExecutor(2) as pool:
            task = asyncio.create_task(execution.arun(pool=pool))
            await asyncio.wait_for(started.wait(), 3)
            source.cancel()
            with pytest.raises(ExecutionCancelled):
                await asyncio.wait_for(task, 3)
        assert not execution.execution_state.node_failures
        assert not execution.execution_state.completed_steps
        assert execution.execution_state.cancelled_steps

    asyncio.run(main())


def test_blocking_action_is_drained_and_keeps_its_success() -> None:
    started, release, stopped = Event(), Event(), Event()
    source = CancellationSource()

    def work() -> int:
        started.set()
        assert release.wait(3)
        return 42

    first = Step(work)
    tail = Step(lambda: pytest.fail("取消后不应派发"))
    graph = Graph(nodes=(first, tail), edges=(Edge(first, tail),))
    execution = Execution(graph, cancellation=source.token)
    errors: list[BaseException] = []
    with ThreadPoolExecutor(1) as pool:

        def run() -> None:
            try:
                execution.run(pool=pool)
            except BaseException as error:
                errors.append(error)
            finally:
                stopped.set()

        driver = Thread(target=run)
        driver.start()
        try:
            assert started.wait(3)
            source.cancel()
            assert not stopped.is_set()
        finally:
            release.set()
            driver.join(5)
        assert not driver.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ExecutionCancelled)
    assert len(execution.execution_state.completed_steps) == 1


def test_real_failure_during_cancellation_is_not_hidden() -> None:
    source = CancellationSource()
    failure = ValueError("真实失败")

    def fail() -> None:
        source.cancel()
        raise failure

    execution = Execution(Step(fail), cancellation=source.token)
    with ThreadPoolExecutor(1) as pool, pytest.raises(ValueError) as caught:
        execution.run(pool=pool)
    assert caught.value is failure
    assert execution.execution_state.node_failures


def test_cancellation_wakes_pending_condition_without_running_its_action() -> None:
    async def main() -> None:
        source = CancellationSource()
        started = asyncio.Event()

        async def condition() -> bool:
            started.set()
            await source.token.wait_cancelled()
            source.token.require_running()
            return True

        execution = Execution(
            Step(lambda: pytest.fail("不应执行"), condition=condition),
            cancellation=source.token,
        )
        with ThreadPoolExecutor(1) as pool:
            task = asyncio.create_task(execution.arun(pool=pool))
            await asyncio.wait_for(started.wait(), 3)
            source.cancel()
            with pytest.raises(ExecutionCancelled):
                await asyncio.wait_for(task, 3)
        assert not execution.execution_state.node_failures

    asyncio.run(main())


def test_dynamic_subgraph_is_cancelled_without_completing_parent() -> None:
    async def main() -> None:
        source = CancellationSource()
        started = asyncio.Event()

        async def child(ctx: NodeRunContext) -> None:
            started.set()
            await ctx.control.wait_cancelled()
            ctx.control.require_running()

        execution = Execution(
            Step(lambda: Graph(nodes=(Step(child),))), cancellation=source.token
        )
        with ThreadPoolExecutor(1) as pool:
            task = asyncio.create_task(execution.arun(pool=pool))
            await asyncio.wait_for(started.wait(), 3)
            source.cancel()
            with pytest.raises(ExecutionCancelled):
                await asyncio.wait_for(task, 3)
        assert not execution.execution_state.completed_steps
        assert not execution.execution_state.node_failures

    asyncio.run(main())


def test_late_cancel_does_not_rewrite_success_or_cancel_another_execution() -> None:
    source = CancellationSource()
    step = Step(lambda: 42)
    first = Execution(step, cancellation=source.token)
    with ThreadPoolExecutor(1) as pool:
        state = first.run(pool=pool)
        source.cancel()
        assert not state.cancelled_steps
        assert Execution(step).run(pool=pool).completed_steps


def test_cancellation_is_retained_even_when_a_running_root_finishes_successfully() -> (
    None
):
    source = CancellationSource()

    def work() -> int:
        source.cancel()
        return 42

    execution = Execution(Step(work), cancellation=source.token)
    with ThreadPoolExecutor(1) as pool, pytest.raises(ExecutionCancelled) as caught:
        execution.run(pool=pool)
    assert caught.value.state.cancelled
    assert len(caught.value.state.completed_steps) == 1
    assert not caught.value.state.cancelled_steps


def test_serial_driver_observes_cooperative_cancellation_and_stops_tail() -> None:
    source = CancellationSource()

    def work(ctx: NodeRunContext) -> None:
        source.cancel()
        assert ctx.control.stop_requested()
        ctx.control.require_running()

    first = Step(work)
    tail = Step(lambda: pytest.fail("串行入口也不能继续派发"))
    control = ExecutionControl(cancellation=source.token)
    execution = Execution(
        Graph(nodes=(first, tail), edges=(Edge(first, tail),)),
        execution_control=control,
    )
    with pytest.raises(ExecutionCancelled):
        SerialGraphExecutor(execution_control=control).execute(execution.compile())


def test_cancelled_nodes_have_terminal_events_and_render_as_cancelled() -> None:
    from rivlet import NodeExecutionState, render_execution

    class Events:
        def __init__(self) -> None:
            self.states: list[NodeExecutionState] = []

        def emit(self, event: NodeExecutionEvent) -> None:
            self.states.append(event.state)

    source = CancellationSource()
    source.cancel()
    step = Step(lambda: None, label="work")
    events = Events()
    execution = Execution(step, cancellation=source.token, events=events)
    with ThreadPoolExecutor(1) as pool, pytest.raises(ExecutionCancelled):
        execution.run(pool=pool)
    assert events.states == [NodeExecutionState.CANCELLED]
    assert "cancelled" in render_execution(step, execution.execution_state)


def test_queued_blocking_actions_do_not_start_after_cancel() -> None:
    source = CancellationSource()
    started, release = Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    waiting = Step(lambda: pytest.fail("已排队但未开始的动作不能执行"))
    execution = Execution(Graph(nodes=(Step(hold), waiting)), cancellation=source.token)
    errors: list[BaseException] = []
    with ThreadPoolExecutor(1) as pool:

        def run() -> None:
            try:
                execution.run(pool=pool)
            except BaseException as error:
                errors.append(error)

        driver = Thread(target=run)
        driver.start()
        try:
            assert started.wait(3)
            source.cancel()
        finally:
            release.set()
            driver.join(5)
        assert not driver.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ExecutionCancelled)
    assert not execution.execution_state.node_failures
    assert len(execution.execution_state.completed_steps) == 1


def test_context_wait_also_settles_on_host_interruption_without_cancelling_shared_source() -> (
    None
):
    async def main() -> None:
        source = CancellationSource()
        started = asyncio.Event()
        settled = asyncio.Event()

        async def work(ctx: NodeRunContext) -> None:
            started.set()
            try:
                await ctx.control.wait_cancelled()
                ctx.control.require_running()
            finally:
                settled.set()

        execution = Execution(Step(work), cancellation=source.token)
        with ThreadPoolExecutor(1) as pool:
            task = asyncio.create_task(execution.arun(pool=pool))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        assert settled.is_set()
        assert not source.token.is_cancelled()
        assert not execution.execution_state.cancelled

    asyncio.run(main())
