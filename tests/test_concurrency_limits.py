from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from rivlet import (
    Edge,
    Execution,
    Graph,
    GraphError,
    GraphInterface,
    GraphStructure,
    StaticCondition,
    StaticConditions,
    Step,
    arun,
    run,
)


class Activity:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = Counter()
        self.peak = Counter()
        self.calls = Counter()

    @contextmanager
    def enter(self, *scopes):
        with self.lock:
            self.calls.update(scopes)
            self.active.update(scopes)
            for scope in scopes:
                self.peak[scope] = max(self.peak[scope], self.active[scope])
        try:
            yield
        finally:
            with self.lock:
                self.active.subtract(scopes)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_nested_limits_cover_mixed_actions_with_one_pool(asynchronous):
    activity = Activity()

    def synchronous():
        with activity.enter("root", "left"):
            time.sleep(0.01)

    async def asynchronous_action():
        with activity.enter("root", "right"):
            await asyncio.sleep(0.01)

    left = Graph(GraphStructure(nodes=tuple(Step(synchronous) for _ in range(12))))
    right = Graph(
        GraphStructure(nodes=tuple(Step(asynchronous_action) for _ in range(12)))
    )
    root = (left | right).graph()
    execution = Execution(root)
    compilation = execution.compile()
    with ThreadPoolExecutor(max_workers=8) as pool:
        limits = {root: 3, left: 1, right: 2}
        if asynchronous:
            asyncio.run(execution.arun(pool=pool, max_concurrency=limits))
        else:
            execution.run(pool=pool, max_concurrency=limits)
        assert pool.submit(lambda: 42).result() == 42
    assert execution.compilation is compilation
    assert activity.calls == {"root": 24, "left": 12, "right": 12}
    assert activity.peak["root"] <= 3
    assert activity.peak["left"] == 1
    assert activity.peak["right"] == 2
    assert not +activity.active


@pytest.mark.parametrize("asynchronous", [False, True])
def test_dynamic_sessions_release_parent_and_inherit_all_scopes(asynchronous):
    activity = Activity()

    async def leaf():
        with activity.enter("root"):
            await asyncio.sleep(0.001)

    def expand():
        with activity.enter("root"):
            return Graph(GraphStructure(nodes=tuple(Step(leaf) for _ in range(4))))

    branch = Step(expand)
    root = Graph(GraphStructure(nodes=(branch, Step(expand), Step(expand))))
    limits = {root: 1, branch: 1}
    if asynchronous:
        asyncio.run(arun(root, max_workers=4, max_concurrency=limits))
    else:
        run(root, max_workers=4, max_concurrency=limits)
    assert activity.calls["root"] == 15
    assert activity.peak["root"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sync_returned_awaitable_keeps_its_admission(asynchronous):
    activity = Activity()

    def action():
        activity.active["factory"] += 1

        async def finish():
            assert activity.active["factory"] == 1
            await asyncio.sleep(0.001)
            activity.active["factory"] -= 1
            activity.calls["done"] += 1

        return finish()

    root = Graph(GraphStructure(nodes=tuple(Step(action) for _ in range(8))))
    if asynchronous:
        asyncio.run(arun(root, max_workers=4, max_concurrency={root: 1}))
    else:
        run(root, max_workers=4, max_concurrency={root: 1})
    assert activity.calls["done"] == 8


def test_request_mapping_is_snapshotted_before_actions_start():
    activity = Activity()
    limits = {}

    async def action():
        limits.clear()
        with activity.enter("root"):
            await asyncio.sleep(0.001)

    root = Graph(GraphStructure(nodes=tuple(Step(action) for _ in range(6))))
    limits[root] = 1
    run(root, max_workers=4, max_concurrency=limits)
    assert activity.calls["root"] == 6
    assert activity.peak["root"] == 1


def test_deep_dynamic_sessions_inherit_limits_through_unbounded_children():
    calls = []

    def expand(depth):
        if depth == 0:
            return GraphStructure(nodes=(Step(lambda: calls.append("leaf")),))
        return GraphStructure(nodes=(Step(lambda: expand(depth - 1)),))

    root = Step(lambda: expand(8))
    run(root, max_workers=1, max_concurrency={root: 1})
    assert calls == ["leaf"]


def test_limited_branch_does_not_block_independent_branch():
    released = threading.Event()
    calls = []

    def blocking():
        assert released.wait(3)

    limited = Graph(
        GraphStructure(nodes=(Step(blocking), Step(lambda: calls.append("later"))))
    )
    root = (limited | Step(released.set)).graph()
    run(root, max_workers=2, max_concurrency={limited: 1})
    assert calls == ["later"]


def test_failed_action_releases_capacity_without_running_failed_dependents():
    calls = []

    def fail():
        raise ValueError("limited action failed")

    failed = Step(fail)
    blocked = Step(lambda: pytest.fail("failed dependent cannot run"))
    independent = Step(lambda: calls.append("independent"))
    root = Graph(
        GraphStructure(
            nodes=(failed, blocked, independent), edges=(Edge(failed, blocked),)
        )
    )
    with pytest.raises(ValueError, match="limited action failed"):
        run(root, max_workers=3, max_concurrency={root: 1})
    assert calls == ["independent"]


def test_ports_and_condition_inputs_keep_their_normal_dependencies():
    plan = Step(lambda: 42)
    producer = Graph(
        GraphStructure(nodes=(plan,), interface=GraphInterface(outputs={"plan": plan}))
    )
    received = []
    relay = Step(lambda _ctx, value: value.unwrap())
    work = Step(
        lambda _ctx, value: received.append(value.unwrap()),
        condition=(lambda value: value.unwrap() == 42, relay),
    )
    root = Graph(
        GraphStructure(
            nodes=(producer, relay, work),
            edges=(
                *Edge.results((producer.interface.outputs["plan"],), relay),
                *Edge.results((relay,), work),
            ),
        )
    )
    run(root, max_workers=2, max_concurrency={root: 1, producer: 1})
    assert received == [42]


def test_limit_for_statically_pruned_node_is_valid_but_has_no_work():
    hidden = Step(lambda: pytest.fail("pruned action cannot run"))
    root = (
        StaticConditions(evaluator=lambda _expr: False)
        .when(StaticCondition(True, "hidden"), hidden)
        .graph()
    )
    run(root, max_concurrency={root: 1, hidden: 1})


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, "2"])
def test_invalid_limit_fails_before_any_action(maximum):
    root = Step(lambda: pytest.fail("invalid configuration cannot run"))
    with pytest.raises(GraphError, match="positive integers"):
        run(root, max_concurrency={root: maximum})


def test_unmounted_or_ambiguous_node_is_not_silently_ignored():
    child = Step(lambda: pytest.fail("invalid configuration cannot run"))
    left = Graph(GraphStructure(nodes=(child,)))
    right = Graph(left.structure)
    with pytest.raises(RuntimeError, match="mounted more than once"):
        run((left | right).graph(), max_concurrency={child: 1})
    with pytest.raises(RuntimeError, match="not mounted"):
        run(left, max_concurrency={right: 1})


def test_distinct_mounts_of_one_structure_have_independent_limits():
    activity = Activity()

    async def action():
        with activity.enter("all"):
            await asyncio.sleep(0.01)

    structure = GraphStructure(nodes=tuple(Step(action) for _ in range(4)))
    left, right = Graph(structure), Graph(structure)
    run((left | right).graph(), max_concurrency={left: 1, right: 1})
    assert activity.calls["all"] == 8
    assert activity.peak["all"] == 2


def test_cancellation_does_not_start_waiting_actions_or_close_borrowed_pool():
    async def scenario():
        started = asyncio.Event()
        calls = []

        async def action():
            calls.append("started")
            started.set()
            await asyncio.sleep(0.02)

        root = Graph(GraphStructure(nodes=tuple(Step(action) for _ in range(8))))
        with ThreadPoolExecutor(max_workers=2) as pool:
            task = asyncio.create_task(
                Execution(root).arun(pool=pool, max_concurrency={root: 1})
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert calls == ["started"]
            assert pool.submit(lambda: 42).result() == 42

    asyncio.run(scenario())
