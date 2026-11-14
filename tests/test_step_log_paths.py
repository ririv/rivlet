import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path

import pytest
from execution_support import Invoke, run_async_execution

from rivlet import (
    Execution,
    ExecutionState,
    Graph,
    GraphMode,
    GraphStructure,
    Node,
    NodePath,
    Step,
)
from rivlet import run as run_execution


class _RecordingLog:
    def __init__(self) -> None:
        self.path: ContextVar[Path | None] = ContextVar("test_log_path", default=None)
        self.appended: list[tuple[Path, str]] = []

    @contextmanager
    def scope(self, path: Path) -> Iterator[None]:
        token = self.path.set(path)
        try:
            yield
        finally:
            self.path.reset(token)

    def append(self, path: Path, text: str) -> None:
        self.appended.append((path, text))


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        partial(run_execution, max_workers=2),
        partial(run_async_execution, max_workers=2),
    ],
)
def test_step_uses_constructor_log_path_snapshot(
    tmp_path: Path,
    mode: GraphMode,
    options: Invoke,
) -> None:
    observed: list[Path | None] = []
    log = _RecordingLog()

    def action() -> None:
        observed.append(log.path.get())

    step = Step(action, label=("leaf"))
    path = tmp_path / "leaf.log"
    bindings = {step: path}
    execution = Execution(
        root=Graph(structure=GraphStructure(nodes=(step,))),
        node_log_paths=bindings,
        action_log=log,
    )
    bindings[step] = tmp_path / "changed.log"

    (options)(execution, mode=mode)

    assert observed == [path]
    assert log.path.get() is None


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        partial(run_execution, max_workers=2),
        partial(run_async_execution, max_workers=2),
    ],
)
def test_parallel_actions_keep_their_own_log_scope(tmp_path, mode, options):
    log = _RecordingLog()
    barrier = threading.Barrier(2)
    observed: list[tuple[str, Path | None, Path | None]] = []

    def record(name: str) -> None:
        before = log.path.get()
        barrier.wait(timeout=5)
        observed.append((name, before, log.path.get()))

    first = Step(lambda: record("first"))
    second = Step(lambda: record("second"))
    first_log, second_log = tmp_path / "first.log", tmp_path / "second.log"
    (options)(
        Execution(
            Graph(structure=GraphStructure(nodes=(first, second))),
            action_log=log,
            node_log_paths={first: first_log, second: second_log},
        ),
        mode=mode,
    )
    assert set(observed) == {
        ("first", first_log, first_log),
        ("second", second_log, second_log),
    }
    assert log.path.get() is None


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        partial(run_execution, max_workers=2),
        partial(run_async_execution, max_workers=2),
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_failed_action_appends_exception_to_its_bound_log(
    tmp_path, mode, options, asynchronous
):
    log = _RecordingLog()
    path = tmp_path / "failure.log"

    def fail() -> None:
        assert log.path.get() == path
        raise RuntimeError("action failed")

    async def fail_async() -> None:
        await asyncio.sleep(0)
        fail()

    step = Step(fail_async if asynchronous else fail)
    with pytest.raises(RuntimeError, match="action failed"):
        (options)(
            Execution(
                Graph(structure=GraphStructure(nodes=(step,))),
                action_log=log,
                node_log_paths={step: path},
            ),
            mode=mode,
        )

    assert len(log.appended) == 1
    assert log.appended[0][0] == path
    assert "RuntimeError: action failed" in log.appended[0][1]
    assert log.path.get() is None


def test_constructor_resolves_all_bindings_in_one_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Step(lambda: None)
    second = Step(lambda: None)
    traversals: list[Graph] = []
    original = Execution._index_mounted_step_paths

    def index(execution: Execution) -> dict[Node, tuple[NodePath, ...]]:
        assert isinstance(execution.root, Graph)
        traversals.append(execution.root)
        return original(execution)

    monkeypatch.setattr(Execution, "_index_mounted_step_paths", index)
    graph = Graph(structure=GraphStructure(nodes=(first, second)))
    Execution(root=graph)
    assert traversals == []

    Execution(
        root=graph,
        node_log_paths={first: tmp_path / "first.log", second: tmp_path / "second.log"},
    )
    assert traversals == [graph]


def test_unmounted_binding_does_not_partially_configure_state(tmp_path: Path) -> None:
    mounted = Step(lambda: None)
    outside = Step(lambda: None)
    state = ExecutionState()

    with pytest.raises(RuntimeError, match="is not mounted"):
        Execution(
            root=Graph(structure=GraphStructure(nodes=(mounted,))),
            execution_state=state,
            node_log_paths={
                mounted: tmp_path / "first.log",
                outside: tmp_path / "bad.log",
            },
        )

    assert (
        state.get_step_log_path(
            NodePath(
                (
                    0,
                    0,
                )
            )
        )
        is None
    )


def test_repeated_step_requires_an_unambiguous_binding(tmp_path: Path) -> None:
    shared = Step(lambda: None)
    first = Graph(structure=GraphStructure(nodes=(shared,)))
    second = Graph(structure=GraphStructure(nodes=(shared,)))

    with pytest.raises(RuntimeError, match="mounted more than once"):
        Execution(
            root=Graph(structure=GraphStructure(nodes=(first, second))),
            node_log_paths={shared: tmp_path / "ambiguous.log"},
        )
