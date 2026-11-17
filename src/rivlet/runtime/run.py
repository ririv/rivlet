from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..compile import (
    CompiledPythonStep,
    CompiledStep,
    GraphCompilation,
)
from ..results import GraphScope, NodeControl, NodeRunContext

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ..model import (
        Graph,
        GraphObservationMessages,
        Node,
        NodeLabelPath,
        NodePath,
    )
    from .control import ExecutionControl
    from .state import ExecutionState


type ExecutableGraph = GraphCompilation


@dataclass(frozen=True, slots=True)
class RuntimeStep:
    """当前可执行 step 及其内部挂载作用域。"""

    step: CompiledPythonStep
    label_path: NodeLabelPath
    graph_node_paths: Mapping[Node, NodePath]
    ancestor_graph_scopes: tuple[GraphScope, ...]


@dataclass(frozen=True, slots=True)
class CompiledGraphRun:
    """使用编译路径和原始声明作用域执行 `GraphCompilation` 的视图。

    编译节点使用全局编码 id 调度；声明作用域只供观察和动态子图物化使用，
    不再作为 action 查询任意步骤结果的入口。
    """

    compilation: GraphCompilation
    step_log_path: Path | None = None

    @property
    def label(self) -> str:
        return self.compilation.label

    @property
    def observation(self) -> GraphObservationMessages:
        return self.compilation.observation

    @property
    def source_graph(self) -> Graph:
        return self.compilation.source_graph

    @property
    def steps(self) -> tuple[CompiledStep, ...]:
        return self.compilation.steps

    @property
    def observation_step_paths(self) -> Mapping[Node, NodePath]:
        return self.compilation.root_graph_node_paths

    def make_run_context(
        self,
        runtime_step: RuntimeStep,
        *,
        shared: object | None = None,
        execution_control: ExecutionControl,
    ) -> NodeRunContext:
        step = runtime_step.step
        context = step.context
        return NodeRunContext(
            node_id=context.node_id,
            node_path=step.path,
            control=NodeControl(
                _execution_control=execution_control,
            ),
            _shared=shared,
        )

    def step_log_path_for(
        self,
        state: ExecutionState,
        step: CompiledPythonStep,
    ) -> Path | None:
        """Resolve a step's explicit or inherited log path without creating a run context."""

        context = step.context
        step_log_path = state.get_step_log_path(step.path)
        if step_log_path is not None:
            return step_log_path
        step_log_path = self.step_log_path
        for composite_id in context.ancestor_composite_ids:
            bound_path = state.get_step_log_path(composite_id)
            if bound_path is not None:
                step_log_path = bound_path
                break
        return step_log_path

    def child_graph_scope(
        self,
        step: CompiledPythonStep,
        *,
        graph_node_paths: Mapping[Node, NodePath],
    ) -> GraphScope:
        del graph_node_paths
        return GraphScope(
            node_paths=step.context.graph_node_paths,
        )
