from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

from .._native import _ConcurrencyLimits

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .._native import (
        CompiledExecutionGraph,
        EncodedExecutionEvent,
        MixedExecutionSession,
        RustActionBindings,
        _RustResult,
    )
    from ..model import NodePath
    from .concurrency import SessionLimits


type StepOutcome = DynamicSubgraph | None
type StepInvocation = StepOutcome | Awaitable[StepOutcome]


class BatchExecutionHost(Protocol):
    def skipped_nodes(self) -> list[int]: ...
    def skip_revision(self) -> int: ...
    def resolve_conditions(
        self, requests: list[tuple[int, int]]
    ) -> list[tuple[int, bool | None]]: ...
    def record_failure(self, node: int, error: Exception) -> None: ...
    def failure_step_path(self) -> NodePath: ...
    def record_subgraph_failure(
        self, node: int, child: BatchExecutionHost, error: Exception
    ) -> None: ...
    def raise_failures(self) -> None: ...
    def apply_events(
        self,
        events: list[EncodedExecutionEvent],
        rust_results: Sequence[tuple[int, _RustResult]] = (),
    ) -> None: ...
    def rust_input_values(
        self, completed: list[tuple[int, bool]]
    ) -> list[tuple[int, object]]: ...
    def retains_rust_results(self) -> bool: ...
    def run_step(self, node: int) -> StepInvocation: ...
    def stop_requested(self) -> bool: ...
    def request_stop(self) -> None: ...
    def require_running(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionDefinition:
    graph: CompiledExecutionGraph
    host: BatchExecutionHost
    rust_actions: RustActionBindings
    rust_contexts: list[tuple[int, str | None]]
    dispatch_limit: int
    async_nodes: frozenset[int] = frozenset()
    limits: SessionLimits | None = None


@dataclass(frozen=True, slots=True)
class DynamicSubgraph:
    """Suspend a parent action until this child session completes."""

    definition: SessionDefinition
    complete: Callable[[], None]


@dataclass(frozen=True, slots=True)
class RootSession:
    pass


@dataclass(frozen=True, slots=True)
class ChildSession:
    parent: SessionRun
    node: int
    complete: Callable[[], None]


class SessionPhase(Enum):
    NEW = auto()
    RUNNING = auto()


@dataclass(eq=False, slots=True)
class SessionRun:
    definition: SessionDefinition
    engine: MixedExecutionSession
    owner: RootSession | ChildSession
    phase: SessionPhase = SessionPhase.NEW
    rust_running: int = 0
    python_running: int = 0
    completed: list[tuple[int, bool]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PythonWork:
    session: SessionRun
    node: int


class SessionTree:
    """Advance related sessions with optional, shared subtree admission limits."""

    def __init__(self, root: SessionDefinition) -> None:
        self.host = root.host
        self.finished = False
        self._ready: deque[SessionRun] = deque()
        self._queued: set[SessionRun] = set()
        self._active: set[SessionRun] = set()
        self.rust_runs: dict[SessionRun, None] = {}
        self._skip_revision = self.host.skip_revision()
        self._limits = (
            None
            if root.limits is None
            else _ConcurrencyLimits(list(root.limits.maxima))
        )
        self._limit_revision = 0
        self._add(root, RootSession())

    def _add(
        self,
        definition: SessionDefinition,
        owner: RootSession | ChildSession,
        inherited_scopes: tuple[int, ...] = (),
    ) -> None:
        # 原生 session 的默认容量由 Rust 托管执行层决定，不读取 Python CPU API；
        # 这不是 Python 池大小；显式限额在真正派发前由公共调度器准入。
        concurrency = None
        if self._limits is not None:
            if definition.limits is None:
                raise RuntimeError("limited child session has no concurrency binding")
            concurrency = (
                self._limits,
                [
                    [*inherited_scopes, *scopes]
                    for scopes in definition.limits.node_scopes
                ],
            )
        run = SessionRun(
            definition,
            definition.graph.mixed_session(
                definition.host.skipped_nodes(),
                definition.rust_actions,
                definition.rust_contexts,
                retain_results=definition.host.retains_rust_results(),
                concurrency=concurrency,
            ),
            owner,
        )
        self._active.add(run)
        self.wake(run)

    def wake(self, run: SessionRun) -> None:
        # native 完成可能已被别的批次排空；稍后到达的 wait 通知不再推进终态 session。
        if run not in self._active:
            return
        if run not in self._queued:
            self._queued.add(run)
            self._ready.append(run)

    def complete(self, work: PythonWork, outcome: StepOutcome) -> None:
        run = work.session
        run.python_running -= 1
        if outcome is None:
            run.completed.append((work.node, True))
            self.wake(run)
        else:
            # 父动作已返回，不占用 worker；生命周期仍等待子图完成。
            self._add(
                outcome.definition,
                ChildSession(run, work.node, outcome.complete),
                inherited_scopes=(
                    ()
                    if self._limits is None
                    else tuple(run.engine.suspend_action(work.node))
                ),
            )

    def _wake_limit_waiters(self) -> None:
        if self._limits is None:
            return
        revision = self._limits.revision()
        if revision != self._limit_revision:
            self._limit_revision = revision
            # 只在实际归还额度后唤醒活跃 session；O(S)，不重扫节点或轮询。
            for run in self._active:
                self.wake(run)

    def fail(self, work: PythonWork, error: Exception) -> None:
        run = work.session
        run.python_running -= 1
        run.definition.host.record_failure(work.node, error)
        run.completed.append((work.node, False))
        self.wake(run)

    def poll(self) -> list[PythonWork]:
        self.host.require_running()
        if len(self._active) > 1:
            revision = self.host.skip_revision()
            if revision != self._skip_revision:
                self._skip_revision = revision
                # 只有出现新的跨作用域 skip 请求时扫描 S 个活跃 session，O(S)。
                for run in self._active:
                    self.wake(run)
        work: list[PythonWork] = []
        self._wake_limit_waiters()
        # 通常只处理收到完成通知的 session；静态图始终只有一个 session。
        # 每批成本 O(Q + A + C + T)，Q 为本批需推进的 session 数，
        # 其余分别为新动作、条件请求和状态转换数，不扫描图中的所有节点。
        while self._ready:
            run = self._ready.popleft()
            self._queued.remove(run)
            source = run.definition
            # 每个节点至多派发一次，节点数足以取出当前全部就绪动作。
            # 这是批量接口的容量，不是跨批次的在途动作上限。
            available = source.dispatch_limit
            if run.phase is SessionPhase.NEW:
                batch = run.engine.start(available)
                run.phase = SessionPhase.RUNNING
            else:
                batch = run.engine.advance(
                    run.completed,
                    [],
                    source.host.skipped_nodes(),
                    available,
                    values=source.host.rust_input_values(run.completed),
                )
                run.completed = []
            while True:
                (
                    actions,
                    conditions,
                    events,
                    finished,
                    rust_running,
                    failures,
                    results,
                ) = batch.parts()
                source.host.apply_events(events, results)
                for node, message in failures:
                    source.host.record_failure(node, RuntimeError(message))
                work.extend(PythonWork(run, node) for node in actions)
                run.python_running += len(actions)
                run.rust_running = rust_running
                if not conditions:
                    break
                resolutions = source.host.resolve_conditions(conditions)
                self.host.require_running()
                batch = run.engine.advance(
                    [],
                    resolutions,
                    source.host.skipped_nodes(),
                    available,
                )
            if run.rust_running:
                self.rust_runs[run] = None
            else:
                self.rust_runs.pop(run, None)
            if finished:
                self._finish(run)
            self._wake_limit_waiters()
        return work

    def _finish(self, run: SessionRun) -> None:
        self._active.remove(run)
        self.rust_runs.pop(run, None)
        match run.owner:
            case RootSession():
                self.finished = True
            case ChildSession(parent=parent, node=node, complete=complete):
                try:
                    run.definition.host.raise_failures()
                except Exception as error:
                    parent.definition.host.record_subgraph_failure(
                        node, run.definition.host, error
                    )
                    parent.completed.append((node, False))
                else:
                    complete()
                    parent.completed.append((node, True))
                self.wake(parent)
