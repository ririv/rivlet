from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock

import pytest

from rivlet import (
    Edge,
    Execution,
    ExecutionStopped,
    Graph,
    Lease,
    Limit,
    NodePath,
    NodeRunContext,
    Step,
    arun,
    run,
)


class Slot:
    def __init__(self):
        self.lock = Lock()
        self.available = True
        self.wakes: dict[int, Callable[[], None]] = {}
        self.watching = Event()
        self.acquired = self.released = self.registrations = 0

    def try_acquire(self) -> Lease[str] | None:
        with self.lock:
            if not self.available:
                return None
            self.available = False
            self.acquired += 1
        return Lease("slot", self.release)

    def release(self):
        with self.lock:
            assert not self.available
            self.available = True
            self.released += 1
            callbacks = tuple(self.wakes.values())
            self.wakes.clear()
        for wake in callbacks:
            wake()

    def watch(self, wake: Callable[[], None]) -> Callable[[], None]:
        with self.lock:
            self.registrations += 1
            key = self.registrations
            ready = self.available
            if not ready:
                self.wakes[key] = wake
        if ready:
            wake()
        self.watching.set()

        def unregister():
            with self.lock:
                self.wakes.pop(key, None)

        return unregister


@pytest.mark.parametrize("asynchronous", [False, True])
def test_contended_source_uses_neither_worker_nor_local_quota(asynchronous: bool):
    slots = Slot()
    checked = Event()
    events = []

    def build_a(ctx: NodeRunContext):
        assert ctx.permit(slots) == "slot"
        assert checked.wait(3)
        events.append("a")

    def build_b(ctx: NodeRunContext):
        assert ctx.permit(slots) == "slot"
        events.append("b")

    def check_cache():
        assert slots.watching.wait(3)
        events.append("c")
        checked.set()

    # 同一轮条件通过后 B/C 才就绪，确保 A 已经独占名额。
    a = Step(build_a)
    b = Step(build_b, condition=lambda: True)
    c = Step(check_cache, condition=lambda: True)
    root = Graph(nodes=(a, b, c))
    limits = {root: 2, a: Limit(1, source=slots), b: Limit(1, source=slots)}
    if asynchronous:
        asyncio.run(arun(root, max_workers=2, max_concurrency=limits))
    else:
        run(root, max_workers=2, max_concurrency=limits)
    assert events == ["c", "a", "b"]
    assert slots.acquired == slots.released == 2
    assert slots.registrations == 1


def test_runtime_graph_releases_parent_and_inherits_source():
    slots = Slot()

    def child(ctx: NodeRunContext):
        assert ctx.permit(slots) == "slot"

    def expand(ctx: NodeRunContext):
        assert ctx.permit(slots) == "slot"
        return Graph(nodes=(Step(child),))

    branch = Step(expand)
    root = Graph(nodes=(branch,))
    limit = Limit(1, source=slots)
    run(root, max_concurrency={root: limit, branch: limit})
    assert slots.acquired == slots.released == 2


def test_source_error_blocks_consumer_but_not_independent_action():
    class Broken(Slot):
        def try_acquire(self):
            raise OSError("slot source broke")

    events = []
    a = Step(lambda: pytest.fail("failed admission must not run"))
    b = Step(lambda: pytest.fail("failed producer must block its consumer"))
    c = Step(lambda: events.append("c"))
    root = Graph(nodes=(a, b, c), edges=(Edge(a, b),))
    with pytest.raises(OSError, match="slot source broke"):
        run(root, max_concurrency={a: Limit(1, source=Broken())})
    assert events == ["c"]


def test_stop_unsubscribes_an_entire_waiting_source_without_polling():
    slots = Slot()
    held = slots.try_acquire()
    assert held is not None
    root = Graph(
        nodes=tuple(
            Step(lambda: pytest.fail("unadmitted action ran")) for _ in range(500)
        )
    )
    execution = Execution(root)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run, execution, max_concurrency={root: Limit(500, source=slots)}
        )
        try:
            assert slots.watching.wait(3)
            execution.execution_control.request_stop()
            with pytest.raises(ExecutionStopped):
                future.result(timeout=3)
            assert slots.registrations == 1
            assert not slots.wakes
            assert slots.acquired == 1 and slots.released == 0
        finally:
            held.release()


def test_skip_completes_an_unadmitted_step_without_waiting_for_resource():
    slots = Slot()
    held = slots.try_acquire()
    assert held is not None
    step = Step(lambda: pytest.fail("skipped step ran"))
    execution = Execution(step)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run, execution, max_concurrency={step: Limit(1, source=slots)}
        )
        try:
            assert slots.watching.wait(3)
            execution.execution_state.request_skip(NodePath((0,)))
            assert future.result(timeout=3).skipped_steps == {NodePath((0,))}
            assert not slots.wakes
        finally:
            held.release()


def test_submitting_to_a_closed_pool_returns_the_acquired_slot():
    slots = Slot()
    step = Step(lambda: pytest.fail("closed pool accepted an action"))
    pool = ThreadPoolExecutor(max_workers=1)
    pool.shutdown()
    with pytest.raises(RuntimeError, match="cannot schedule new futures"):
        Execution(step).run(pool=pool, max_concurrency={step: Limit(1, source=slots)})
    assert slots.acquired == slots.released == 1


def test_an_empty_subgraph_does_not_bind_a_second_source_to_an_action():
    outer_source, inner_source = Slot(), Slot()
    empty = Graph(nodes=())
    root = Graph(nodes=(empty,))
    run(
        root,
        max_concurrency={root: Limit(1, outer_source), empty: Limit(1, inner_source)},
    )
    assert outer_source.acquired == inner_source.acquired == 0


def test_async_cancellation_does_not_release_a_running_action():
    slots = Slot()
    began, finish = Event(), Event()

    def action():
        began.set()
        assert finish.wait(3)

    async def execute():
        root = Step(action)
        execution = Execution(root)
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_stop():
            loop.call_soon_threadsafe(stopping.set)

        execution.execution_control.register_stop_handler(on_stop)
        task = asyncio.create_task(
            arun(execution, max_concurrency={root: Limit(1, source=slots)})
        )
        try:
            assert await asyncio.to_thread(began.wait, 3)
            task.cancel()
            await asyncio.wait_for(stopping.wait(), 3)
            assert slots.try_acquire() is None
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert slots.acquired == slots.released == 1

    asyncio.run(execute())


def test_borrowed_pool_submission_window_applies_to_a_wide_graph():
    completed = 0
    pending: list[Future] = []

    class MeteredPool(ThreadPoolExecutor):
        def submit[T, **P](
            self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
        ) -> Future[T]:
            nonlocal pending
            pending = [future for future in pending if not future.done()]
            assert len(pending) < 3
            future = super().submit(fn, *args, **kwargs)
            pending.append(future)
            return future

    def action():
        nonlocal completed
        completed += 1

    root = Graph(nodes=tuple(Step(action) for _ in range(2000)))
    with MeteredPool(max_workers=1) as pool:
        Execution(root).run(pool=pool, max_submitted=3)
    assert completed == 2000
