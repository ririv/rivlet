from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ..model import (
    Graph as GraphNode,
)
from ..model import (
    GraphMode,
    GraphStructure,
    Node,
    NodeKind,
    NodePath,
    node_paths_for_mount,
    resolve_node_label,
)
from .formatting import (
    render_execution_result_suffix,
    render_graph_after_suffix,
    render_static_suffix,
    render_step_label,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..runtime import ExecutionSnapshot, ExecutionState


class ExecutionRenderStatus(StrEnum):
    """根据执行快照派生的展示状态，不作为 runtime 状态机事实来源。"""

    RUNNING = "running"
    EXECUTED = "executed"
    ENTERED = "entered"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    SKIPPED_BY_ANCESTOR = "skipped_by_ancestor"
    NOT_REACHED = "not_reached"
    FAILED = "failed"
    BLOCKED = "blocked"


def render_execution(
    root: NodeKind,
    state: ExecutionState,
    *,
    mode: GraphMode = "dynamic",
) -> str:
    graph = GraphStructure(nodes=(root,))
    return _render_execution_impl(
        graph,
        state,
        root_label=resolve_node_label(root, NodePath((0,))),
        mode=mode,
        include_not_reached=True,
        title="execution",
    )


def render_live_execution(
    root: NodeKind,
    state: ExecutionState,
    *,
    mode: GraphMode = "dynamic",
) -> str:
    graph = GraphStructure(nodes=(root,))
    root_label = resolve_node_label(root, NodePath((0,)))
    snapshot = state.snapshot()
    leaf_steps = collect_leaf_steps(graph)
    running_step_paths = deepest_running_step_paths(snapshot)
    running_labels = [
        snapshot.step_label_paths[node_path] for node_path in running_step_paths
    ]
    remaining_count = sum(
        1
        for node_path, _label_path in leaf_steps
        if node_path not in snapshot.completed_steps
        and node_path not in snapshot.skipped_steps
        and node_path not in snapshot.node_failures
    )
    last_step_path = snapshot.executed_steps[-1] if snapshot.executed_steps else None
    running_text = _summarize_paths(running_labels, fallback="idle")
    last_text = (
        _format_step_path(snapshot.step_label_paths[last_step_path])
        if last_step_path is not None
        else "-"
    )
    return (
        f"graph={root_label} mode={mode} "
        f"running={running_text} "
        f"last={last_text} "
        f"remaining={remaining_count}"
    )


def _render_execution_impl(
    graph: GraphStructure,
    state: ExecutionState,
    *,
    root_label: str,
    mode: GraphMode,
    include_not_reached: bool,
    title: str,
) -> str:
    snapshot = state.snapshot()
    lines = [f"{title}: {root_label} (mode={mode})"]
    active_step_paths = active_execution_step_paths(snapshot)
    if not active_step_paths and not include_not_reached:
        return "\n".join(lines)
    append_execution_graph(
        lines,
        graph,
        snapshot=snapshot,
        active_step_paths=active_step_paths,
        label_path=(),
        parent_node_path=None,
        ancestor_skipped=False,
        include_not_reached=include_not_reached,
        prefix="",
    )
    return "\n".join(lines)


def append_execution_graph(
    lines: list[str],
    graph: GraphStructure,
    *,
    snapshot: ExecutionSnapshot,
    active_step_paths: set[NodePath],
    label_path: tuple[str, ...],
    parent_node_path: NodePath | None,
    ancestor_skipped: bool,
    include_not_reached: bool,
    prefix: str,
) -> None:
    graph_node_paths = node_paths_for_mount(graph, parent_node_path=parent_node_path)
    for index, step in enumerate(graph.nodes):
        append_execution_step(
            lines,
            graph,
            step,
            snapshot=snapshot,
            active_step_paths=active_step_paths,
            label_path=label_path,
            graph_node_paths=graph_node_paths,
            ancestor_skipped=ancestor_skipped,
            include_not_reached=include_not_reached,
            is_last=index == len(graph.nodes) - 1,
            prefix=prefix,
        )


def append_execution_step(
    lines: list[str],
    graph: GraphStructure,
    step: Node,
    *,
    snapshot: ExecutionSnapshot,
    active_step_paths: set[NodePath],
    label_path: tuple[str, ...],
    graph_node_paths: Mapping[Node, NodePath],
    ancestor_skipped: bool,
    include_not_reached: bool,
    is_last: bool,
    prefix: str,
) -> None:
    node_path = graph_node_paths[step]
    step_label = resolve_node_label(step, node_path)
    step_label_path = (*label_path, step_label)
    status = resolve_execution_status(
        step,
        node_path=node_path,
        snapshot=snapshot,
        active_step_paths=active_step_paths,
        ancestor_skipped=ancestor_skipped,
    )
    if status is ExecutionRenderStatus.NOT_REACHED and not include_not_reached:
        return
    branch = "└─ " if is_last else "├─ "
    child_prefix = prefix + ("   " if is_last else "│  ")
    action = step.structure if isinstance(step, GraphNode) else step
    base = (
        f"{step_label} [graph]"
        if isinstance(action, GraphStructure)
        else render_step_label(step, node_path=node_path)
    )
    lines.append(
        f"{prefix}{branch}{base}"
        f"{render_static_suffix(graph, step)}"
        f"{render_execution_condition_suffix(step, node_path=node_path, condition_values=snapshot.runtime_condition_values)}"
        f" [{status.value}]"
        f"{render_execution_result_suffix(node_path, snapshot.results)}"
        f"{render_graph_after_suffix(graph, step, step_paths=graph_node_paths)}"
    )
    if not isinstance(action, GraphStructure):
        return
    append_execution_graph(
        lines,
        action,
        snapshot=snapshot,
        active_step_paths=active_step_paths,
        label_path=step_label_path,
        parent_node_path=node_path,
        ancestor_skipped=(ancestor_skipped or status is ExecutionRenderStatus.SKIPPED),
        include_not_reached=include_not_reached,
        prefix=child_prefix,
    )


def resolve_execution_status(
    step: Node,
    *,
    node_path: NodePath,
    snapshot: ExecutionSnapshot,
    active_step_paths: set[NodePath],
    ancestor_skipped: bool,
) -> ExecutionRenderStatus:
    if failure := snapshot.node_failures.get(node_path):
        return ExecutionRenderStatus(failure.value)
    if ancestor_skipped:
        return ExecutionRenderStatus.SKIPPED_BY_ANCESTOR
    if node_path in snapshot.skipped_steps:
        return ExecutionRenderStatus.SKIPPED
    if isinstance(step, GraphNode) and isinstance(step.structure, GraphStructure):
        if (
            node_path in snapshot.completed_composites
            or node_path in snapshot.completed_steps
        ):
            return ExecutionRenderStatus.COMPLETED
        if node_path in snapshot.entered_composites or node_path in active_step_paths:
            return ExecutionRenderStatus.ENTERED
        return ExecutionRenderStatus.NOT_REACHED
    if node_path in active_step_paths:
        if node_path in snapshot.completed_steps:
            return ExecutionRenderStatus.EXECUTED
        return ExecutionRenderStatus.RUNNING
    return ExecutionRenderStatus.NOT_REACHED


def render_execution_condition_suffix(
    step: Node,
    *,
    node_path: NodePath,
    condition_values: Mapping[NodePath, bool],
) -> str:
    """渲染 runtime 已记录的条件值，不在渲染阶段重新执行 predicate。"""

    if step.condition is None:
        return ""
    value = condition_values.get(node_path)
    if value is None:
        return " [condition=pending]"
    return f" [condition={str(value).lower()}]"


def active_execution_step_paths(
    snapshot: ExecutionSnapshot,
) -> set[NodePath]:
    active: set[NodePath] = set()
    for node_path in [
        *snapshot.executed_steps,
        *snapshot.entered_composites,
        *snapshot.skipped_steps,
        *snapshot.node_failures,
    ]:
        current: NodePath | None = node_path
        while current is not None:
            active.add(current)
            current = current.parent
    return active


def deepest_running_step_paths(
    snapshot: ExecutionSnapshot,
) -> tuple[NodePath, ...]:
    """返回当前未完成路径中最深的 step，避免同时展示其执行中祖先。"""

    running = [
        node_path
        for node_path in snapshot.executed_steps
        if node_path not in snapshot.completed_steps
        and node_path not in snapshot.skipped_steps
        and node_path not in snapshot.node_failures
    ]
    running_set = set(running)
    ancestors_with_running_children: set[NodePath] = set()
    for node_path in running:
        current = node_path.parent
        while current is not None:
            if current in running_set:
                ancestors_with_running_children.add(current)
            current = current.parent
    return tuple(
        node_path
        for node_path in running
        if node_path not in ancestors_with_running_children
    )


def collect_step_labels(
    graph: GraphStructure,
    *,
    label_path: tuple[str, ...] = (),
    parent_node_path: NodePath | None = None,
) -> tuple[tuple[NodePath, tuple[str, ...]], ...]:
    out: list[tuple[NodePath, tuple[str, ...]]] = []
    for step, node_path in node_paths_for_mount(
        graph,
        parent_node_path=parent_node_path,
    ).items():
        step_label_path = (*label_path, resolve_node_label(step, node_path))
        out.append((node_path, step_label_path))
        if isinstance(step, GraphNode) and isinstance(step.structure, GraphStructure):
            out.extend(
                collect_step_labels(
                    step.structure,
                    label_path=step_label_path,
                    parent_node_path=node_path,
                )
            )
    return tuple(out)


def collect_leaf_steps(
    graph: GraphStructure,
    *,
    label_path: tuple[str, ...] = (),
    parent_node_path: NodePath | None = None,
) -> tuple[tuple[NodePath, tuple[str, ...]], ...]:
    out: list[tuple[NodePath, tuple[str, ...]]] = []
    for step, node_path in node_paths_for_mount(
        graph,
        parent_node_path=parent_node_path,
    ).items():
        step_label_path = (*label_path, resolve_node_label(step, node_path))
        if isinstance(step, GraphNode) and isinstance(step.structure, GraphStructure):
            out.extend(
                collect_leaf_steps(
                    step.structure,
                    label_path=step_label_path,
                    parent_node_path=node_path,
                )
            )
            continue
        out.append((node_path, step_label_path))
    return tuple(out)


def _format_step_path(path: tuple[str, ...] | None) -> str:
    if path is None or not path:
        return "-"
    return "/".join(path)


def _summarize_paths(paths: list[tuple[str, ...]], *, fallback: str) -> str:
    if not paths:
        return fallback
    labels = [_format_step_path(path) for path in paths[:2]]
    remaining = len(paths) - len(labels)
    if remaining > 0:
        labels.append(f"+{remaining}")
    return ",".join(labels)
