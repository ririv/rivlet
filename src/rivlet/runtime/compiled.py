from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..compile import (
    CompiledComposite,
    CompiledPythonLeaf,
    CompiledRustLeaf,
    GraphCompilation,
)
from ..errors import GraphError
from ..events import (
    ActionFailed,
    BlockedByCondition,
    BlockedByConditionInput,
    BlockedByDependency,
    BlockedByParent,
    EdgeConditionSite,
    NodeConditionSite,
    SubgraphFailed,
)
from ..model import GraphStructure
from ..observe import GraphObservationEvent, GraphObservationPhase
from ..option import Nothing
from .control import ExecutionStopped

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .._native import EncodedExecutionEvent, _RustResult
    from ..model import NodePath
    from ..observe import GraphObserver
    from .control import ExecutionControl
    from .session_tree import BatchExecutionHost, LeafInvocation
    from .state import ExecutionState

logger = logging.getLogger(__name__)


class CompiledExecutionHost:
    """把 Rust execution session 的批量事件接回 Python 运行设施。"""

    def __init__(
        self,
        compilation: GraphCompilation,
        state: ExecutionState,
        *,
        observer: GraphObserver | None,
        run_leaf: Callable[[CompiledPythonLeaf], LeafInvocation],
        execution_control: ExecutionControl | None = None,
        record_composites_as_steps: bool = False,
    ) -> None:
        self.compilation = compilation
        self.state = state
        self._rust_import_sources = frozenset(
            compilation._rust_actions.python_sources()
        )
        # inputs 是显式的数据依赖；执行生产者前即开启记录，无需调用方另配 store。
        # 最多检查 C 个条件和 V 个步骤，O(C + V) 时间、O(1) 额外空间。
        if (
            self._rust_import_sources
            or any(condition.inputs for condition in compilation._execution_conditions)
            or any(
                isinstance(step, CompiledPythonLeaf) and step.inputs
                for step in compilation.steps
            )
        ):
            state._enable_results()
        self.observer = observer
        self.run_leaf_callback = run_leaf
        self.execution_control = execution_control
        self.record_composites_as_steps = record_composites_as_steps
        # 初始化一次遍历 V 个 native 节点，时间/空间均为 O(V)；之后每次
        # advance 只处理尚未转发的新增请求 ΔS，不再扫描整张图。
        self._execution_index_by_node_path = {
            node.path: index
            for index, node in enumerate(self.compilation._execution_nodes)
        }
        self._forwarded_skip_step_paths: set[NodePath] = set()
        self._failures: list[Exception] = []
        self._first_failure: NodePath | None = None
        self._dynamic_failure_sources: dict[int, NodePath] | None = None
        self.state._register_event_paths(
            (node.path, node.context.node_id) for node in compilation._execution_nodes
        )

    def skipped_nodes(self) -> list[int]:
        nodes: list[int] = []
        for node_path in (
            self.state.skip_request_ids() - self._forwarded_skip_step_paths
        ):
            index = self._execution_index_by_node_path.get(node_path)
            if index is None:
                continue
            self._forwarded_skip_step_paths.add(node_path)
            self.state.match_skip_request(node_path)
            nodes.append(index)
        return nodes

    def skip_revision(self) -> int:
        return self.state.skip_revision

    def resolve_conditions(
        self,
        requests: list[tuple[int, int]],
    ) -> list[tuple[int, bool | None]]:
        resolutions: list[tuple[int, bool | None]] = []
        for node, condition in requests:
            try:
                value = self.evaluate_condition(node, condition)
            except ExecutionStopped:
                raise
            except Exception as error:
                self.record_failure(node, error)
                resolutions.append((condition, None))
            else:
                resolutions.append((condition, value))
        return resolutions

    def record_failure(self, node: int, error: Exception) -> None:
        path = "/".join(self.compilation._execution_node(node).path.labels)
        error.add_note(f"GraphStructure node: {path}")
        logger.error("GraphStructure node failed: %s: %s", path, error)
        self._failures.append(error)

    def failure_step_path(self) -> NodePath:
        if self._first_failure is None:
            raise GraphError("failed child session has no failed execution node")
        return self._first_failure

    def record_subgraph_failure(
        self, node: int, child: BatchExecutionHost, error: Exception
    ) -> None:
        # 子 session 返回前已转发其实际失败事件；只保存直接来源，不扫描传递闭包。
        if self._dynamic_failure_sources is None:
            self._dynamic_failure_sources = {}
        self._dynamic_failure_sources[node] = child.failure_step_path()
        self.record_failure(node, error)

    def raise_failures(self) -> None:
        if len(self._failures) == 1:
            raise self._failures[0]
        if self._failures:
            raise ExceptionGroup("GraphStructure execution failed", self._failures)

    def rust_input_values(
        self, completed: list[tuple[int, bool]]
    ) -> list[tuple[int, object]]:
        if not self._rust_import_sources:
            return []
        results = self.state.results
        if results is None:
            raise GraphError("native inputs require an enabled result store")
        return [
            (node, results.require(self.compilation._execution_node(node).path).value)
            for node, success in completed
            if success and node in self._rust_import_sources
        ]

    def retains_rust_results(self) -> bool:
        return self.state.results is not None

    def apply_events(
        self,
        events: list[EncodedExecutionEvent],
        rust_results: Sequence[tuple[int, _RustResult]] = (),
    ) -> None:
        for node, value in rust_results:
            self.state.record_result(self._require_rust_leaf(node).path, value)
        for event in events:
            match event:
                case (0, node, value):
                    self.record_condition(node, value)
                case (1, node):
                    self.record_condition_skip(node)
                case (2, node):
                    self.enter_composite(node)
                case (3, node):
                    self.complete_composite(node)
                case (4, node):
                    node_path = self.compilation._execution_node(node).path
                    self.state.record_skipped_step(node_path)
                case (5, node):
                    step = self._require_rust_leaf(node)
                    self.state.record_step(step.path, step.path.labels)
                case (6, node):
                    step = self._require_rust_leaf(node)
                    self.state.complete_step(step.path)
                case (7, node):
                    node_path = self._failure_step(node)
                    source = (
                        None
                        if self._dynamic_failure_sources is None
                        else self._dynamic_failure_sources.pop(node, None)
                    )
                    self.state.record_failed_step(
                        node_path,
                        ActionFailed() if source is None else SubgraphFailed(source),
                    )
                case (8, node, source):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByDependency(
                            self.compilation._execution_node(source).path
                        ),
                    )
                case (9, node, condition):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByCondition(NodeConditionSite(condition)),
                    )
                case (10, node, condition, source):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByConditionInput(
                            NodeConditionSite(condition),
                            self.compilation._execution_node(source).path,
                        ),
                    )
                case (11, node, source):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByParent(self.compilation._execution_node(source).path),
                    )
                case (12, node, source):
                    self.state.record_failed_step(
                        self._failure_step(node),
                        SubgraphFailed(self.compilation._execution_node(source).path),
                    )
                case (13, node, condition, prerequisite):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByCondition(
                            EdgeConditionSite(
                                condition,
                                self.compilation._execution_node(prerequisite).path,
                            )
                        ),
                    )
                case (14, node, condition, prerequisite, source):
                    self.state.record_blocked_step(
                        self._failure_step(node),
                        BlockedByConditionInput(
                            EdgeConditionSite(
                                condition,
                                self.compilation._execution_node(prerequisite).path,
                            ),
                            self.compilation._execution_node(source).path,
                        ),
                    )
                case _:
                    raise GraphError(f"unknown native execution event: {event}")

    def _failure_step(self, node: int) -> NodePath:
        node_path = self.compilation._execution_node(node).path
        if self._first_failure is None:
            self._first_failure = node_path
        return node_path

    def evaluate_condition(self, node: int, condition: int) -> bool:
        compiled_node = self.compilation._execution_node(node)
        compiled_condition = self.compilation._execution_condition(condition)
        # 编译引用已经绑定到具体挂载；每个输入只查询一次，不创建条件上下文。
        results = self.state.results
        if compiled_condition.inputs and results is None:
            raise GraphError("runtime condition requires an initialized result store")
        inputs = (
            ()
            if results is None
            else tuple(results.get(ref.path) for ref in compiled_condition.inputs)
        )
        value = compiled_condition.when(
            *(Nothing() if result is None else result for result in inputs)
        )
        if not isinstance(value, bool):
            raise GraphError(
                f"compiled runtime condition must return bool: {compiled_node.path}"
            )
        return value

    def record_condition(self, node: int, value: bool) -> None:
        self.state.record_runtime_condition(
            self.compilation._execution_node(node).path,
            value,
        )

    def record_condition_skip(self, node: int) -> None:
        compiled_node = self.compilation._execution_node(node)
        condition = compiled_node.condition
        if condition is None:
            raise GraphError(
                f"disabled compiled step has no runtime condition: {compiled_node.path}"
            )
        inputs = ",".join(str(ref.path.segments) for ref in condition.inputs) or "-"
        logger.debug(
            "Condition: skip %s (inputs=%s; value=false)",
            ".".join(compiled_node.path.labels),
            inputs,
        )

    def enter_composite(self, node: int) -> None:
        composite = self._require_composite(node)
        if self.record_composites_as_steps:
            self.state.record_step(composite.path, composite.path.labels)
        self.state.enter_composite(composite.path)
        _emit_composite_observation(
            composite,
            state=self.state,
            observer=self.observer,
            phase=GraphObservationPhase.BEFORE,
        )

    def complete_composite(self, node: int) -> None:
        composite = self._require_composite(node)
        if self.record_composites_as_steps:
            self.state.complete_step(composite.path)
        self.state.complete_composite(composite.path)
        _emit_composite_observation(
            composite,
            state=self.state,
            observer=self.observer,
            phase=GraphObservationPhase.AFTER_SUCCESS,
        )

    def run_leaf(self, node: int) -> LeafInvocation:
        compiled_node = self.compilation._execution_node(node)
        if not isinstance(compiled_node, CompiledPythonLeaf):
            raise GraphError(
                f"native scheduler submitted non-leaf node: {compiled_node.path}"
            )
        return self.run_leaf_callback(compiled_node)

    def stop_requested(self) -> bool:
        return (
            False
            if self.execution_control is None
            else self.execution_control.stop_requested()
        )

    def request_stop(self) -> None:
        if self.execution_control is not None:
            self.execution_control.request_stop()

    def require_running(self) -> None:
        if self.execution_control is not None:
            self.execution_control.require_running()

    def _require_composite(self, node: int) -> CompiledComposite:
        compiled_node = self.compilation._execution_node(node)
        if not isinstance(compiled_node, CompiledComposite):
            raise GraphError(
                "native scheduler emitted composite lifecycle for leaf: "
                f"{compiled_node.path}"
            )
        return compiled_node

    def _require_rust_leaf(self, node: int) -> CompiledRustLeaf:
        compiled_node = self.compilation._execution_node(node)
        if not isinstance(compiled_node, CompiledRustLeaf):
            raise GraphError(
                f"native action lifecycle references non-native node: {compiled_node.path}"
            )
        return compiled_node


def _emit_composite_observation(
    composite: CompiledComposite,
    *,
    state: ExecutionState,
    observer: GraphObserver | None,
    phase: GraphObservationPhase,
) -> None:
    if observer is None:
        return
    message = (
        composite.observation.before
        if phase is GraphObservationPhase.BEFORE
        else composite.observation.after_success
    )
    if message is None:
        return
    if not isinstance(composite.source_graph, GraphStructure):
        raise GraphError("native graph must not declare Python observation messages")
    observer.emit(
        GraphObservationEvent(
            phase=phase,
            message=message,
            source_graph=composite.source_graph,
            graph_label=composite.graph_label,
            graph_label_path=composite.path.labels,
            graph_node_paths=composite.graph_node_paths,
            results=None if state.results is None else state.results,
        )
    )
