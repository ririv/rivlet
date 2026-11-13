from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .option import Some
from .runtime.control import ExecutionControl

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .model import Node, NodeId, NodePath


@dataclass(frozen=True, slots=True)
class GraphScope:
    node_paths: Mapping[Node, NodePath]


@dataclass(frozen=True, slots=True)
class NodeControl:
    # action 只读取协作停止状态，不修改其他步骤的调度。
    _execution_control: ExecutionControl = field(
        default_factory=ExecutionControl, repr=False, kw_only=True
    )

    def stop_requested(self) -> bool:
        return self._execution_control.stop_requested()

    def require_running(self) -> None:
        self._execution_control.require_running()


@dataclass(frozen=True, slots=True)
class ResultView:
    """Read-only access to step result records, without copying their values."""

    _store: ResultStore = field(repr=False)

    def get(self, node_path: NodePath) -> Some[object] | None:
        return self._store.get(node_path)

    def require(self, node_path: NodePath) -> Some[object]:
        return self._store.require(node_path)

    def has(self, node_path: NodePath) -> bool:
        return self._store.has(node_path)


@dataclass(slots=True)
class ResultStore:
    """以本次图挂载分配的 `NodePath` 保存执行结果的线程安全存储。

    runtime worker 与 observer/render 可能并发读写结果，因此所有访问都由
    存储自身的锁保护；调用方不应通过执行状态的锁推断这里已经同步。
    """

    _results: dict[NodePath, Some[object]] = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _view: ResultView = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # 每个 store 只创建一个 O(1) 视图；各步骤和观察者共享，不复制结果表。
        self._view = ResultView(self)

    @property
    def view(self) -> ResultView:
        return self._view

    def record(self, node_path: NodePath, value: object | None) -> None:
        with self._lock:
            if node_path in self._results:
                raise RuntimeError(f"step result already recorded for id: {node_path}")
            self._results[node_path] = Some(value=value)

    def get(self, node_path: NodePath) -> Some[object] | None:
        with self._lock:
            return self._results.get(node_path)

    def require(self, node_path: NodePath) -> Some[object]:
        result = self.get(node_path)
        if result is None:
            raise RuntimeError(f"missing step result for path: {node_path}")
        return result

    def has(self, node_path: NodePath) -> bool:
        with self._lock:
            return node_path in self._results

    def copy(self) -> ResultStore:
        with self._lock:
            copied = ResultStore(_results=dict(self._results))
        return copied


@dataclass(frozen=True, slots=True)
class NodeRunContext:
    # action 的运行能力入口；节点结果只通过显式结果边传参。
    node_id: NodeId
    node_path: NodePath
    control: NodeControl
    _shared: object | None = field(default=None, repr=False, kw_only=True)

    def shared[T](self, expected: type[T]) -> T:
        """Return the explicitly injected shared object with a checked type."""

        if self._shared is None:
            raise RuntimeError("no shared state was provided for this graph run")
        if not isinstance(self._shared, expected):
            raise TypeError(
                "graph shared state type mismatch: "
                f"expected {expected.__qualname__}, "
                f"got {type(self._shared).__qualname__}"
            )
        return self._shared
