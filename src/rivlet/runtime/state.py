from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..events import EventDeliveryError, StepExecutionEvent, StepExecutionState

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from ..events import (
        StepBlockCause,
        StepEventSink,
        StepExecutionStateDetail,
        StepFailureCause,
    )
    from ..model import StepId, StepLabelPath, StepPath
    from ..results import ResultStore, ResultView


def _is_same_or_descendant(step_path: StepPath, ancestor: StepPath) -> bool:
    return step_path.segments[: len(ancestor.segments)] == ancestor.segments


class GraphNodeFailure(StrEnum):
    """区分自身失败和因前置失败而未执行的节点。"""

    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(slots=True)
class _StepEvents:
    sink: StepEventSink
    paths: dict[StepPath, tuple[StepId, StepPath]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass(slots=True, init=False)
class ExecutionState:
    """Own the mutable facts produced by exactly one Graph run.

    调用方只能提供初始 skip 请求，不能通过构造器伪造“已执行”“已完成”或
    “实际跳过”等运行事实。runtime 在状态转换发生时记录这些事实，并拒绝
    同一步同时进入互斥终态。
    """

    _skip_requests: set[StepPath]
    _matched_skip_requests: set[StepPath]
    _skipped_steps: set[StepPath]
    _executed_steps: list[StepPath]
    _completed_steps: set[StepPath]
    _entered_composites: set[StepPath]
    _completed_composites: set[StepPath]
    _step_label_paths: dict[StepPath, StepLabelPath]
    _results: ResultStore | None
    _step_log_paths: Mapping[StepPath, Path]
    _runtime_condition_values: dict[StepPath, bool]
    _node_failures: dict[StepPath, GraphNodeFailure]
    _lock: threading.Lock
    _started: bool
    _skip_revision: int
    _events: _StepEvents | None

    def __init__(
        self,
        *,
        initial_skip_requests: Iterable[StepPath] = (),
        results: ResultStore | None = None,
    ) -> None:
        self._skip_requests = set(initial_skip_requests)
        self._matched_skip_requests = set()
        self._skipped_steps = set()
        self._executed_steps = []
        self._completed_steps = set()
        self._entered_composites = set()
        self._completed_composites = set()
        self._step_label_paths = {}
        self._results = results
        self._step_log_paths = MappingProxyType({})
        self._runtime_condition_values = {}
        self._node_failures = {}
        self._lock = threading.Lock()
        self._started = False
        self._skip_revision = 0
        self._events = None

    def _register_event_paths(self, paths: Iterable[tuple[StepPath, StepId]]) -> None:
        if self._events is not None:
            with self._lock:
                self._events.paths.update(
                    (path, (identity, path)) for path, identity in paths
                )

    def _enable_results(self) -> None:
        """Enable recording before executing a graph with result edges."""
        from ..results import ResultStore

        with self._lock:
            if self._results is None:
                self._results = ResultStore()

    def _emit(
        self, step_path: StepPath, state_detail: StepExecutionStateDetail
    ) -> None:
        if self._events is None:
            return
        # 未订阅时只执行上面的 O(1) 分支，不构造事件或读取标签映射。
        with self._events.lock:
            with self._lock:
                identity, path = self._events.paths[step_path]
            try:
                self._events.sink.emit(StepExecutionEvent(identity, path, state_detail))
            except Exception as error:
                raise EventDeliveryError("step event delivery failed") from error

    @property
    def results(self) -> ResultView | None:
        return None if self._results is None else self._results.view

    @property
    def skip_revision(self) -> int:
        with self._lock:
            return self._skip_revision

    @property
    def skipped_steps(self) -> frozenset[StepPath]:
        with self._lock:
            return frozenset(self._skipped_steps)

    @property
    def executed_steps(self) -> list[StepPath]:
        with self._lock:
            return list(self._executed_steps)

    @property
    def completed_steps(self) -> frozenset[StepPath]:
        with self._lock:
            return frozenset(self._completed_steps)

    @property
    def entered_composites(self) -> frozenset[StepPath]:
        with self._lock:
            return frozenset(self._entered_composites)

    @property
    def completed_composites(self) -> frozenset[StepPath]:
        with self._lock:
            return frozenset(self._completed_composites)

    @property
    def node_failures(self) -> dict[StepPath, GraphNodeFailure]:
        with self._lock:
            return dict(self._node_failures)

    def begin_run(self) -> None:
        """声明本状态开始服务一次执行，并拒绝后续复用。"""

        with self._lock:
            if self._started:
                raise RuntimeError("graph execution state cannot be reused")
            self._started = True

    def snapshot(self) -> ExecutionSnapshot:
        """在同一把锁下复制一份内部一致的只读执行快照。"""

        with self._lock:
            return ExecutionSnapshot(
                skipped_steps=frozenset(self._skipped_steps),
                executed_steps=tuple(self._executed_steps),
                completed_steps=frozenset(self._completed_steps),
                entered_composites=frozenset(self._entered_composites),
                completed_composites=frozenset(self._completed_composites),
                step_label_paths=dict(self._step_label_paths),
                results=None if self._results is None else self._results.copy().view,
                runtime_condition_values=dict(self._runtime_condition_values),
                node_failures=dict(self._node_failures),
            )

    def request_skip(self, step_path: StepPath) -> None:
        """请求 runtime 跳过尚未开始的节点或组合边界。"""

        with self._lock:
            started = (
                *self._executed_steps,
                *self._entered_composites,
                *self._completed_composites,
            )
            if any(_is_same_or_descendant(item, step_path) for item in started):
                raise RuntimeError(f"cannot skip an already started step: {step_path}")
            if step_path not in self._skip_requests:
                self._skip_requests.add(step_path)
                self._skip_revision += 1

    def request_skips(self, step_paths: Iterable[StepPath]) -> None:
        for step_path in step_paths:
            self.request_skip(step_path)

    def is_skip_requested(self, step_path: StepPath) -> bool:
        with self._lock:
            current: StepPath | None = step_path
            while current is not None:
                if current in self._skip_requests:
                    self._matched_skip_requests.add(current)
                    return True
                current = current.parent
            return False

    def skip_request_ids(self) -> frozenset[StepPath]:
        """返回尚未限定到某张编译图的 skip 请求快照。"""

        with self._lock:
            return frozenset(self._skip_requests)

    def match_skip_request(self, step_path: StepPath) -> None:
        """记录某张编译图已经识别该 skip 请求。"""

        with self._lock:
            if step_path not in self._skip_requests:
                raise RuntimeError(f"unknown graph skip request: {step_path}")
            self._matched_skip_requests.add(step_path)

    def require_skip_requests_resolved(self) -> None:
        """拒绝没有对应到本次执行中任何节点的 skip 请求。"""

        with self._lock:
            unresolved = self._skip_requests - self._matched_skip_requests
        if unresolved:
            rendered = ", ".join(str(step_path) for step_path in sorted(unresolved))
            raise RuntimeError(f"unknown graph skip requests: {rendered}")

    def record_skipped_step(self, step_path: StepPath) -> None:
        with self._lock:
            self._require_not_failed(step_path)
            if (
                step_path in self._step_label_paths
                or step_path in self._completed_steps
            ):
                raise RuntimeError(f"cannot skip an already executed step: {step_path}")
            self._skipped_steps.add(step_path)
        if self._events is not None:
            self._emit(step_path, StepExecutionState.SKIPPED)

    def record_step(self, step_path: StepPath, label_path: StepLabelPath) -> None:
        """记录 step 已开始执行及其实际挂载路径。"""

        with self._lock:
            self._require_not_failed(step_path)
            if step_path in self._skipped_steps:
                raise RuntimeError(f"cannot execute a skipped step: {step_path}")
            existing_path = self._step_label_paths.get(step_path)
            if existing_path is not None:
                if existing_path != label_path:
                    raise RuntimeError(
                        f"step {step_path} was recorded with inconsistent label paths"
                    )
                raise RuntimeError(f"step was executed more than once: {step_path}")
            self._step_label_paths[step_path] = label_path
            self._executed_steps.append(step_path)
        if self._events is not None:
            self._emit(step_path, StepExecutionState.STARTED)

    def complete_step(self, step_path: StepPath) -> None:
        """记录 step 的 action 或完整子图已经成功完成。"""

        with self._lock:
            self._require_not_failed(step_path)
            if step_path not in self._step_label_paths:
                raise RuntimeError(
                    f"cannot complete a step that was not started: {step_path}"
                )
            if step_path in self._completed_steps:
                raise RuntimeError(f"step was completed more than once: {step_path}")
            self._completed_steps.add(step_path)
        if self._events is not None:
            self._emit(step_path, StepExecutionState.COMPLETED)

    def enter_composite(self, step_path: StepPath) -> None:
        """记录非执行组合边界已经激活。"""

        with self._lock:
            self._require_not_failed(step_path)
            if step_path in self._skipped_steps:
                raise RuntimeError(f"cannot enter a skipped composite: {step_path}")
            if step_path in self._entered_composites:
                raise RuntimeError(f"composite was entered more than once: {step_path}")
            self._entered_composites.add(step_path)
            emit = self._events is not None and step_path not in self._step_label_paths
        if emit:
            self._emit(step_path, StepExecutionState.STARTED)

    def complete_composite(self, step_path: StepPath) -> None:
        """记录组合边界成功完成；失败子图不会进入此状态。"""

        with self._lock:
            self._require_not_failed(step_path)
            if step_path not in self._entered_composites:
                raise RuntimeError(
                    f"cannot complete a composite that was not entered: {step_path}"
                )
            if step_path in self._completed_composites:
                raise RuntimeError(
                    f"composite was completed more than once: {step_path}"
                )
            self._completed_composites.add(step_path)
            emit = self._events is not None and step_path not in self._completed_steps
        if emit:
            self._emit(step_path, StepExecutionState.COMPLETED)

    def _require_not_failed(self, step_path: StepPath) -> None:
        if step_path in self._node_failures:
            raise RuntimeError(f"node already failed or blocked: {step_path}")

    def record_failed_step(self, step_path: StepPath, cause: StepFailureCause) -> None:
        self._record_node_failure(step_path, GraphNodeFailure.FAILED)
        if self._events is not None:
            self._emit(step_path, cause)

    def record_blocked_step(self, step_path: StepPath, cause: StepBlockCause) -> None:
        self._record_node_failure(step_path, GraphNodeFailure.BLOCKED)
        if self._events is not None:
            self._emit(step_path, cause)

    def _record_node_failure(
        self, step_path: StepPath, failure: GraphNodeFailure
    ) -> None:
        with self._lock:
            self._require_not_failed(step_path)
            if (
                step_path in self._completed_steps
                or step_path in self._completed_composites
                or step_path in self._skipped_steps
            ):
                raise RuntimeError(
                    f"node already reached a terminal state: {step_path}"
                )
            if failure is GraphNodeFailure.BLOCKED and (
                step_path in self._step_label_paths
                or step_path in self._entered_composites
            ):
                raise RuntimeError(f"cannot block an already started node: {step_path}")
            self._node_failures[step_path] = failure

    def record_result(self, step_path: StepPath, value: object | None) -> None:
        with self._lock:
            if step_path not in self._step_label_paths:
                raise RuntimeError(
                    f"cannot record a result before step start: {step_path}"
                )
            if self._results is not None:
                self._results.record(step_path, value)

    def record_runtime_condition(self, step_path: StepPath, value: bool) -> None:
        """保存 runtime 已使用的 step 条件值，供 render 只读展示。"""

        with self._lock:
            existing = self._runtime_condition_values.get(step_path)
            if existing is not None and existing is not value:
                raise RuntimeError(
                    f"runtime condition changed during one run: {step_path}"
                )
            self._runtime_condition_values[step_path] = value

    def _initialize_execution_inputs(
        self, paths: Mapping[StepPath, Path], events: StepEventSink | None
    ) -> None:
        """Atomically bind execution inputs after all declarations are validated."""

        with self._lock:
            if self._started:
                raise RuntimeError(
                    "cannot configure inputs after execution has started"
                )
            if paths and self._step_log_paths:
                raise RuntimeError("step log paths are already configured")
            if events is not None and self._events is not None:
                raise RuntimeError("step event sink is already bound")
            if paths:
                self._step_log_paths = MappingProxyType(dict(paths))
            if events is not None:
                self._events = _StepEvents(events)

    def get_step_log_path(self, step_path: StepPath) -> Path | None:
        with self._lock:
            return self._step_log_paths.get(step_path)


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """从执行状态原子复制出的渲染与观察快照。"""

    skipped_steps: frozenset[StepPath]
    executed_steps: tuple[StepPath, ...]
    completed_steps: frozenset[StepPath]
    entered_composites: frozenset[StepPath]
    completed_composites: frozenset[StepPath]
    step_label_paths: dict[StepPath, StepLabelPath]
    results: ResultView | None
    runtime_condition_values: dict[StepPath, bool]
    node_failures: dict[StepPath, GraphNodeFailure]
