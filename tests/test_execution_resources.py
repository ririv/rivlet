from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from rivlet import Edge, Execution, Graph, GraphStructure, Step, arun, run
from rivlet._native import _ResultFixtureKind, _rust_result_fixture


@pytest.mark.skipif(
    sys.version_info < (3, 14), reason="interpreter pools require Python 3.14"
)
def test_isolated_interpreter_pool_is_not_a_shared_thread_pool() -> None:
    if sys.version_info >= (3, 14):
        from concurrent.futures import InterpreterPoolExecutor

        with InterpreterPoolExecutor(max_workers=1) as pool:
            with pytest.raises(TypeError, match="isolated interpreter"):
                Execution(Step(lambda: pytest.fail("action must not start"))).run(
                    pool=pool
                )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_low_level_execution_never_closes_borrowed_pool(asynchronous, fails) -> None:
    def action() -> None:
        if fails:
            raise ValueError("action failed")

    execution = Execution(Step(action))
    with ThreadPoolExecutor(max_workers=2) as pool:

        def invoke() -> None:
            if asynchronous:
                asyncio.run(execution.arun(pool=pool))
            else:
                execution.run(pool=pool)

        if fails:
            with pytest.raises(ValueError, match="action failed"):
                invoke()
        else:
            invoke()
        assert pool.submit(lambda: 42).result() == 42


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_managed_entry_closes_its_pool(monkeypatch, asynchronous, fails) -> None:
    pool = ThreadPoolExecutor(max_workers=2)
    module = importlib.import_module("rivlet.execution")
    monkeypatch.setattr(module, "ThreadPoolExecutor", lambda **_kwargs: pool)

    def action() -> None:
        if fails:
            raise ValueError("action failed")

    def invoke() -> None:
        if asynchronous:
            asyncio.run(arun(Step(action), max_workers=2))
        else:
            run(Step(action), max_workers=2)

    try:
        if fails:
            with pytest.raises(ValueError, match="action failed"):
                invoke()
        else:
            invoke()
        with pytest.raises(RuntimeError, match="shutdown"):
            pool.submit(lambda: None)
    finally:
        pool.shutdown()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_coroutines_are_not_limited_by_thread_pool_size(asynchronous) -> None:
    async def scenario() -> None:
        barrier = asyncio.Barrier(8)
        loops = []

        async def action() -> None:
            loops.append(asyncio.get_running_loop())
            async with asyncio.timeout(3):
                await barrier.wait()

        graph = Graph(GraphStructure(nodes=tuple(Step(action) for _ in range(8))))
        with ThreadPoolExecutor(max_workers=1) as pool:
            await Execution(graph).arun(pool=pool)
        assert len(loops) == 8
        assert all(loop is asyncio.get_running_loop() for loop in loops)

    if asynchronous:
        asyncio.run(scenario())
    else:
        # 阻塞入口同样不能把 8 个协程限制在线程池的一个槽位中。
        barrier = asyncio.Barrier(8)
        calls = []

        async def action() -> None:
            async with asyncio.timeout(3):
                await barrier.wait()
            calls.append("done")

        root = Graph(GraphStructure(nodes=tuple(Step(action) for _ in range(8))))
        run(root, max_workers=1)
        assert calls == ["done"] * 8


@pytest.mark.parametrize("asynchronous", [False, True])
def test_coroutine_can_release_a_blocked_thread(asynchronous) -> None:
    ready = threading.Event()

    def blocking() -> None:
        assert ready.wait(3), "coroutine must not queue behind the blocking action"

    async def release() -> None:
        await asyncio.sleep(0)
        ready.set()

    graph = Graph(GraphStructure(nodes=(Step(blocking), Step(release))))
    if asynchronous:
        asyncio.run(arun(graph, max_workers=1))
    else:
        run(graph, max_workers=1)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_rust_completion_does_not_require_a_python_action_worker(asynchronous) -> None:
    ready = threading.Event()

    def blocking() -> None:
        assert ready.wait(3), (
            "Rust completion notification must not occupy the action pool"
        )

    async def release(_ctx, value) -> None:
        assert value.unwrap() == 42
        ready.set()

    source, _ = _rust_result_fixture(_ResultFixtureKind.I64)
    provider = Graph(source)
    consumer = Step(release)
    root = Graph(
        GraphStructure(
            nodes=(Step(blocking), provider, consumer),
            edges=Edge.results((provider.interface.outputs["value"],), consumer),
        )
    )
    if asynchronous:
        asyncio.run(arun(root, max_workers=1))
    else:
        run(root, max_workers=1)


@pytest.mark.parametrize("fails", [False, True])
def test_sync_only_execution_never_creates_an_event_loop(monkeypatch, fails) -> None:
    def unexpected_runner(*_args, **_kwargs):
        pytest.fail("synchronous execution must not create an event loop")

    monkeypatch.setattr(asyncio, "Runner", unexpected_runner)

    def action() -> None:
        if fails:
            raise ValueError("sync failed")

    if fails:
        with pytest.raises(ValueError, match="sync failed"):
            run(Step(action))
    else:
        run(Step(action))


def test_blocking_mixed_execution_creates_one_loop(monkeypatch) -> None:
    original = asyncio.Runner
    runners = []
    loops = []

    def make_runner(*args, **kwargs):
        runner = original(*args, **kwargs)
        runners.append(runner)
        return runner

    monkeypatch.setattr(asyncio, "Runner", make_runner)

    async def action() -> None:
        loops.append(asyncio.get_running_loop())
        await asyncio.sleep(0)

    def returned_awaitable():
        return action()

    child = (Step(returned_awaitable) >> Step(action)).graph()
    root = (Step(action) >> Step(lambda: child)).graph()
    run(root, max_workers=2)

    assert len(runners) == 1
    assert len(loops) == 3
    assert all(loop is loops[0] for loop in loops)
