"""运行时谓词的异步完成、输入归属、失败隔离与资源生命周期。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Generator
from typing import Literal, cast

import pytest

from rivlet import (
    Edge,
    Execution,
    ExecutionState,
    ExecutionStopped,
    Graph,
    GraphError,
    GraphInterface,
    GraphStructure,
    Option,
    ResultStore,
    SerialGraphExecutor,
    Step,
    arun,
    run,
)

type Backend = Literal["run", "arun", "serial"]


def execute(root: Step | Graph, backend: Backend) -> Execution:
    execution = Execution(root, execution_state=ExecutionState(results=ResultStore()))
    match backend:
        case "run":
            run(execution, max_workers=1)
        case "arun":
            asyncio.run(arun(execution, max_workers=1))
        case "serial":
            SerialGraphExecutor(execution.execution_state).execute(execution.compile())
    return execution


@pytest.mark.parametrize("backend", ["run", "arun", "serial"])
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize(
    "kind", ["coroutine", "returned-awaitable", "custom-awaitable"]
)
def test_node_predicates_await_the_actual_return_value(backend, selected, kind):
    calls = []
    source = Step(lambda: selected)

    async def predicate(value: Option[object]) -> bool:
        await asyncio.sleep(0)
        calls.append("predicate")
        return value.unwrap() is True

    class Decision:
        def __init__(self, value: Option[object]) -> None:
            self.value = value

        def __await__(self) -> Generator[object, None, bool]:
            return predicate(self.value).__await__()

    def returned(value: Option[object]) -> Awaitable[bool]:
        return predicate(value)

    callback = {
        "coroutine": predicate,
        "returned-awaitable": returned,
        "custom-awaitable": Decision,
    }[kind]
    consumer = Step(lambda: calls.append("action"), condition=(callback, source))
    execution = execute(Graph(GraphStructure(nodes=(source, consumer))), backend)
    assert calls == (["predicate", "action"] if selected else ["predicate"])
    assert (
        execution.execution_state.results.get(execution.compile().steps[-1].path)
        is not None
    ) is selected


@pytest.mark.parametrize("backend", ["run", "arun"])
def test_predicates_share_the_loop_without_using_action_slots(backend, monkeypatch):
    original = asyncio.Runner
    runners = []
    loops = []
    barrier = asyncio.Barrier(8)

    def make_runner(*args, **kwargs):
        runner = original(*args, **kwargs)
        runners.append(runner)
        return runner

    monkeypatch.setattr(asyncio, "Runner", make_runner)

    async def predicate() -> bool:
        loops.append(asyncio.get_running_loop())
        async with asyncio.timeout(3):
            await barrier.wait()
        return True

    async def action() -> None:
        loops.append(asyncio.get_running_loop())

    root = Graph(
        GraphStructure(nodes=tuple(Step(action, condition=predicate) for _ in range(8)))
    )
    if backend == "run":
        run(root, max_workers=1, max_concurrency={root: 1})
    else:
        asyncio.run(arun(root, max_workers=1, max_concurrency={root: 1}))
    assert len(runners) == (1 if backend == "run" else 0)
    assert len(loops) == 16
    assert all(loop is loops[0] for loop in loops)


@pytest.mark.parametrize("backend", ["run", "arun"])
def test_pending_condition_survives_the_last_unrelated_action(backend):
    release = asyncio.Event()
    calls = []

    async def predicate() -> bool:
        async with asyncio.timeout(3):
            await release.wait()
        return True

    async def independent() -> None:
        await asyncio.sleep(0)
        calls.append("independent")
        release.set()

    guarded = Step(lambda: calls.append("guarded"), condition=predicate)
    execute(Graph(GraphStructure(nodes=(guarded, Step(independent)))), backend)
    assert calls == ["independent", "guarded"]


@pytest.mark.parametrize("backend", ["run", "arun"])
def test_false_async_edge_condition_removes_waiting_not_actions(backend):
    release = asyncio.Event()
    calls = []
    decision = Step(lambda: False)

    async def source() -> None:
        async with asyncio.timeout(3):
            await release.wait()
        calls.append("source")

    async def predicate(value: Option[object]) -> bool:
        await asyncio.sleep(0)
        return value.unwrap() is True

    async def publish() -> None:
        calls.append("publish")
        release.set()

    a, b = Step(source), Step(publish)
    root = Graph(
        GraphStructure(
            nodes=(decision, a, b), edges=(Edge(a, b, condition=(predicate, decision)),)
        )
    )
    execute(root, backend)
    assert calls == ["publish", "source"]


@pytest.mark.parametrize("backend", ["run", "arun", "serial"])
def test_async_predicate_failure_only_blocks_its_dependents(backend):
    calls = []

    async def predicate() -> bool:
        await asyncio.sleep(0)
        raise ValueError("predicate failed")

    guarded = Step(
        lambda: pytest.fail("guarded action must not run"), condition=predicate
    )
    downstream = Step(lambda: pytest.fail("dependent action must not run"))
    independent = Step(lambda: calls.append("independent"))
    root = Graph(
        GraphStructure(
            nodes=(guarded, downstream, independent), edges=(Edge(guarded, downstream),)
        )
    )
    with pytest.raises(ValueError, match="predicate failed"):
        execute(root, backend)
    assert calls == ["independent"]


@pytest.mark.parametrize("backend", ["run", "arun", "serial"])
def test_awaited_predicate_must_still_return_a_bool(backend):
    async def wrong() -> bool:
        await asyncio.sleep(0)
        return cast("bool", 1)

    with pytest.raises(GraphError, match="condition must return bool"):
        execute(Step(lambda: pytest.fail("must not run"), condition=wrong), backend)


@pytest.mark.parametrize("backend", ["run", "arun", "serial"])
def test_async_conditions_keep_repeated_subgraph_inputs_separate(backend):
    source = Step(lambda ctx: ctx.node_path.labels[-2])

    async def predicate(value: Option[object]) -> bool:
        await asyncio.sleep(0)
        return value.unwrap() == "left"

    child = Step(lambda: 42, condition=(predicate, source))
    body = GraphStructure(
        nodes=(source, child), interface=GraphInterface(outputs=child)
    )
    left, right = Graph(body, label="left"), Graph(body, label="right")
    seen = []
    inspect = Step(lambda ctx, a, b: seen.append((a.unwrap(), b.unwrap_or(None))))
    root = Graph(
        GraphStructure(
            nodes=(left, right, inspect),
            edges=Edge.results(
                (left.interface.output, right.interface.output), inspect
            ),
        )
    )
    execute(root, backend)
    assert seen == [(42, None)]


@pytest.mark.parametrize("backend", ["run", "arun", "serial"])
def test_runtime_returned_subgraph_can_have_async_graph_conditions(backend):
    calls = []

    async def predicate() -> bool:
        await asyncio.sleep(0)
        calls.append("predicate")
        return True

    child = Graph(
        GraphStructure(nodes=(Step(lambda: calls.append("child")),)),
        condition=predicate,
    )
    execute(Step(lambda: child), backend)
    assert calls == ["predicate", "child"]


@pytest.mark.parametrize("backend", ["run", "serial"])
def test_synchronous_conditions_do_not_create_a_loop(backend, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("synchronous predicates must not create a loop")

    monkeypatch.setattr(asyncio, "Runner", unexpected)
    calls = []
    execute(Step(lambda: calls.append("ran"), condition=lambda: True), backend)
    assert calls == ["ran"]


def test_explicit_serial_executor_reuses_its_predicate_runner(monkeypatch):
    original = asyncio.Runner
    runners, loops = [], []

    def make_runner(*args, **kwargs):
        runner = original(*args, **kwargs)
        runners.append(runner)
        return runner

    monkeypatch.setattr(asyncio, "Runner", make_runner)

    async def predicate() -> bool:
        await asyncio.sleep(0)
        loops.append(asyncio.get_running_loop())
        return True

    root = Graph(
        GraphStructure(
            nodes=tuple(Step(lambda: None, condition=predicate) for _ in range(3))
        )
    )
    execute(root, "serial")
    assert len(runners) == 1
    assert len(loops) == 3
    assert all(loop is loops[0] for loop in loops)


@pytest.mark.parametrize("backend", ["run", "arun"])
def test_cancelled_condition_requests_global_stop_and_drains_started_work(backend):
    calls = []

    async def cancelled() -> bool:
        await asyncio.sleep(0)
        raise asyncio.CancelledError

    async def settling() -> bool:
        try:
            await asyncio.sleep(0)
            return True
        finally:
            calls.append("settled")

    root = Graph(
        GraphStructure(
            nodes=(
                Step(lambda: None, condition=cancelled),
                Step(lambda: None, condition=settling),
            )
        )
    )
    execution = Execution(root)
    with pytest.raises(ExecutionStopped, match="condition was cancelled"):
        if backend == "run":
            run(execution)
        else:
            asyncio.run(arun(execution))
    assert execution.execution_control.stop_requested()
    assert calls == ["settled"]


def test_caller_cancellation_settles_an_inflight_predicate():
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def predicate() -> bool:
            started.set()
            try:
                async with asyncio.timeout(3):
                    await release.wait()
                return True
            finally:
                calls.append("settled")

        execution = Execution(
            Step(lambda: pytest.fail("must not dispatch"), condition=predicate)
        )
        execution.execution_control.register_stop_handler(release.set)
        task = asyncio.create_task(arun(execution))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["settled"]
        assert execution.execution_control.stop_requested()

    asyncio.run(scenario())


def test_private_callback_experiment_keeps_its_synchronous_bridge():
    from rivlet._native import _experimental_run_tokio_callbacks
    from rivlet.runtime.compiled import CompiledExecutionHost

    calls = []

    async def predicate() -> bool:
        await asyncio.sleep(0)
        calls.append("predicate")
        return True

    compilation = Execution(Step(lambda: None, condition=predicate)).compile()
    host = CompiledExecutionHost(
        compilation,
        ExecutionState(),
        observer=None,
        run_step=lambda _step: calls.append("action"),
    )
    _experimental_run_tokio_callbacks(compilation._execution_graph, host, 1)
    assert calls == ["predicate", "action"]
