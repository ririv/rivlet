from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import InitVar, dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from .compile import (
    GraphCompilation,
    compile_graph,
)
from .errors import GraphError
from .execution_output import (
    NO_EXECUTION_OUTPUT,
    ExecutionOutput,
)
from .runtime import (
    NO_ACTION_LOG,
    ActionLog,
    ExecutionControl,
    ExecutionState,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .events import NodeEventSink
    from .model import GraphMode, GraphStructure, Node, NodeKind, NodePath
    from .observe import GraphObserver
    from .option import Some


@dataclass(frozen=True, slots=True)
class Execution:
    """Bind one declared root Node to a single execution.

    根与内部 Node 使用相同的身份、条件和生命周期；根可承载 Graph、Step
    或原生图结构。这里只绑定运行状态，不重命名或替换声明。

    它持有：
    - 根 Node 声明
    - 这次执行共享的 `ExecutionState`
    - 这次执行使用的 `ExecutionControl`
    - 调用方显式提供、由各 Python action 共享的业务对象

    它负责的入口包括：
    - `compile()`：在内部 lower 条件边并生成 `GraphCompilation`
    - `run(pool=...)`：借用线程池阻塞等待这次执行
    - `arun(pool=...)`：借用线程池和当前事件循环，异步等待这次执行

    dynamic / compiled 仅控制展示记录，不是线程 / 异步模式。
    模块级 run/arun 提供创建及关闭 Python 线程池的高阶封装。
    四个入口均可显式配置 max_concurrency，额度按实际图祖先共同约束各语言动作。
    """

    root: NodeKind
    _declaration_graph: GraphStructure = field(init=False, repr=False, compare=False)
    shared: object | None = field(default=None, kw_only=True, repr=False)
    execution_state: ExecutionState = field(default_factory=ExecutionState)
    execution_control: ExecutionControl = field(
        default_factory=ExecutionControl,
    )
    action_log: ActionLog = NO_ACTION_LOG
    execution_output: ExecutionOutput = NO_EXECUTION_OUTPUT
    node_log_paths: InitVar[Mapping[NodeKind, Path] | None] = field(
        default=None, kw_only=True
    )
    events: InitVar[NodeEventSink | None] = field(default=None, kw_only=True)
    _compilation: GraphCompilation | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _compilation_lock: Lock = field(
        default_factory=Lock, init=False, repr=False, compare=False
    )

    @property
    def compilation(self) -> GraphCompilation | None:
        """Return this execution's successfully prepared root compilation.

        首次 compile/run/arun 成功编译后固定；执行失败不删除它。重新求值静态
        条件须创建新的 Execution，声明和宿主绑定不允许事后替换。
        """
        return self._compilation

    def __post_init__(
        self,
        node_log_paths: Mapping[NodeKind, Path] | None,
        events: NodeEventSink | None,
    ) -> None:
        from .model import Graph, GraphStructure, Step

        if not isinstance(self.root, (Step, Graph)):
            raise TypeError("Execution requires a root Node")
        # 根 Node 是唯一真实根节点。此 Graph 只是编译输入的结构作用域，
        # 不分配身份、不生成空 action，也不再次包装已有节点。
        object.__setattr__(
            self, "_declaration_graph", GraphStructure(nodes=(self.root,))
        )
        if not node_log_paths:
            if events is not None:
                self.execution_state._initialize_execution_inputs({}, events)
            return
        mounted_step_paths = self._index_mounted_step_paths()
        # 先完整校验再写入状态，避免某个引用无效时只绑定了一部分日志。
        resolved = {
            self._require_unique_mounted_step_path(
                step, mounted_step_paths=mounted_step_paths
            ): path
            for step, path in node_log_paths.items()
        }
        self.execution_state._initialize_execution_inputs(resolved, events)

    def compile(self) -> GraphCompilation:
        """Prepare once; subsequent compile/run/arun calls reuse the same structure."""
        if self._compilation is not None:
            return self._compilation
        # 只在首次准备时加锁，避免两个调用者重复求静态条件；完整编译成功才提交。
        # 每实例只承担一次既有编译成本，后续读取 O(1)，无跨 execution 的全局缓存。
        with self._compilation_lock:
            compilation = self._compilation
            if compilation is None:
                compilation = compile_graph(
                    self._declaration_graph, root_label=self._root_label
                )
                object.__setattr__(self, "_compilation", compilation)
            return compilation

    def require_result(self, step: Node) -> Some[object]:
        """按声明 step 引用读取本次图挂载中的执行结果。"""

        results = self.execution_state.results
        if results is None:
            raise RuntimeError("graph execution did not enable graph results")
        return results.require(self._require_unique_mounted_step_path(step))

    def _require_unique_mounted_step_path(
        self,
        step: Node,
        *,
        mounted_step_paths: Mapping[Node, tuple[NodePath, ...]] | None = None,
    ) -> NodePath:
        if mounted_step_paths is None:
            mounted_step_paths = self._index_mounted_step_paths()
        matches = mounted_step_paths.get(step)
        if not matches:
            raise RuntimeError(f"step '{step.label}' is not mounted in graph execution")
        if len(matches) > 1:
            raise RuntimeError(
                f"step '{step.label}' is mounted more than once in graph execution"
            )
        return next(iter(matches))

    def _index_mounted_step_paths(self) -> dict[Node, tuple[NodePath, ...]]:
        from .model import Graph, GraphStructure, node_paths_for_mount

        mounted_step_paths: dict[Node, list[NodePath]] = {}

        def _collect(graph: GraphStructure, parent_node_path: NodePath | None) -> None:
            for candidate, candidate_id in node_paths_for_mount(
                graph,
                parent_node_path=parent_node_path,
            ).items():
                mounted_step_paths.setdefault(candidate, []).append(candidate_id)
                if isinstance(candidate, Graph) and isinstance(
                    candidate.structure, GraphStructure
                ):
                    _collect(candidate.structure, candidate_id)

        # V 为静态挂载中的 step 出现次数。一次批量绑定以 O(V) 时间和空间建立索引，
        # 随后的 K 次查找均为 O(1)，总复杂度为 O(V + K)，避免退化为 O(K * V)。
        _collect(self._declaration_graph, None)
        return {
            candidate: tuple(step_paths)
            for candidate, step_paths in mounted_step_paths.items()
        }

    def _write_execution_output(
        self,
        *,
        mode: GraphMode,
        execution_error: BaseException | None = None,
    ) -> None:
        """写入最终执行输出，同时保证输出错误不覆盖执行错误。"""

        try:
            self.execution_output.write(
                self.root,
                self.execution_state,
                mode=mode,
            )
        except Exception as output_error:
            if execution_error is None:
                raise
            execution_error.add_note(
                f"failed to write graph execution output: {output_error}"
            )

    @property
    def _root_label(self) -> str:
        from .model import NodePath, resolve_node_label

        return resolve_node_label(self.root, NodePath((0,)))

    def _compile_for_run(self, mode: GraphMode) -> GraphCompilation:
        self.execution_control.require_running()
        if mode not in ("dynamic", "compiled"):
            raise GraphError(f"unsupported graph mode: {mode}")
        return self.compile()

    def _resolve_concurrency[LimitedNode: NodeKind](
        self, requested: Mapping[LimitedNode, int] | None
    ) -> dict[NodePath, int]:
        from .model import Graph, Step

        if not requested:
            return {}
        # 先复制请求，外部修改字典不会改变本次执行的额度。
        snapshot = dict(requested)
        for node, maximum in snapshot.items():
            if not isinstance(node, (Step, Graph)):
                raise GraphError("concurrency limits require declared Nodes")
            if type(maximum) is not int or maximum < 1:
                raise GraphError("concurrency limits must be positive integers")
        mounted_paths = self._index_mounted_step_paths()
        return {
            self._require_unique_mounted_step_path(
                node, mounted_step_paths=mounted_paths
            ): maximum
            for node, maximum in snapshot.items()
        }

    def run[LimitedNode: NodeKind](
        self,
        *,
        pool: ThreadPoolExecutor,
        max_concurrency: Mapping[LimitedNode, int] | None = None,
        observer: GraphObserver | None = None,
        mode: GraphMode = "dynamic",
    ) -> ExecutionState:
        """Block until completion, borrowing but never shutting down the supplied pool."""

        from .runtime import GraphExecutor

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise GraphError("use await execution.arun() inside a running event loop")
        target_graph = self._compile_for_run(mode)
        limits = self._resolve_concurrency(max_concurrency)
        try:
            result = GraphExecutor(
                self.execution_state,
                shared=self.shared,
                execution_control=self.execution_control,
                observer=observer,
                action_log=self.action_log,
                record_composites_as_steps=mode == "dynamic",
                max_concurrency=limits,
            ).execute(target_graph, pool=pool)
        except BaseException as execution_error:
            self._write_execution_output(mode=mode, execution_error=execution_error)
            raise
        self._write_execution_output(mode=mode)
        return result

    async def arun[LimitedNode: NodeKind](
        self,
        *,
        pool: ThreadPoolExecutor,
        max_concurrency: Mapping[LimitedNode, int] | None = None,
        observer: GraphObserver | None = None,
        mode: GraphMode = "dynamic",
    ) -> ExecutionState:
        """Await mixed actions using the caller's loop and a borrowed thread pool."""

        from .runtime import GraphExecutor

        target_graph = self._compile_for_run(mode)
        limits = self._resolve_concurrency(max_concurrency)
        try:
            result = await GraphExecutor(
                self.execution_state,
                shared=self.shared,
                execution_control=self.execution_control,
                observer=observer,
                action_log=self.action_log,
                record_composites_as_steps=mode == "dynamic",
                max_concurrency=limits,
            ).aexecute(target_graph, pool=pool)
        except BaseException as execution_error:
            self._write_execution_output(mode=mode, execution_error=execution_error)
            raise
        self._write_execution_output(mode=mode)
        return result


def run[LimitedNode: NodeKind](
    root: NodeKind | Execution,
    *,
    max_workers: int = 1,
    max_concurrency: Mapping[LimitedNode, int] | None = None,
    observer: GraphObserver | None = None,
    mode: GraphMode = "dynamic",
) -> ExecutionState:
    """Run a declaration or prepared execution using an owned Python thread pool."""

    execution = root if isinstance(root, Execution) else Execution(root)
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="GraphWorker"
    ) as pool:
        return execution.run(
            pool=pool, max_concurrency=max_concurrency, observer=observer, mode=mode
        )


async def arun[LimitedNode: NodeKind](
    root: NodeKind | Execution,
    *,
    max_workers: int = 1,
    max_concurrency: Mapping[LimitedNode, int] | None = None,
    observer: GraphObserver | None = None,
    mode: GraphMode = "dynamic",
) -> ExecutionState:
    """Await a declaration or prepared execution using an owned Python thread pool."""

    execution = root if isinstance(root, Execution) else Execution(root)
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="GraphWorker"
    ) as pool:
        return await execution.arun(
            pool=pool, max_concurrency=max_concurrency, observer=observer, mode=mode
        )
