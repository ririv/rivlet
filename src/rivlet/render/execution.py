from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ..model import (
    Graph,
    GraphMode,
    Step,
    StepPath,
    resolve_step_label,
    step_paths_for_mount,
)
from .formatting import (
    render_execution_result_suffix,
    render_graph_after_suffix,
    render_static_suffix,
    render_step_label,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..runtime import GraphExecutionSnapshot, GraphExecutionState


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


def render_graph_execution(
    graph: Graph,
    state: GraphExecutionState,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    return _render_graph_execution_impl(
        graph,
        state,
        root_label=root_label,
        mode=mode,
        include_not_reached=True,
        title="execution",
    )


def render_live_graph_execution(
    graph: Graph,
    state: GraphExecutionState,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    snapshot = state.snapshot()
    leaf_steps = collect_leaf_steps(graph)
    running_step_paths = deepest_running_step_paths(snapshot)
    running_labels = [
        snapshot.step_label_paths[step_path] for step_path in running_step_paths
    ]
    remaining_count = sum(
        1
        for step_path, _label_path in leaf_steps
        if step_path not in snapshot.completed_steps
        and step_path not in snapshot.skipped_steps
        and step_path not in snapshot.node_failures
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


def _render_graph_execution_impl(
    graph: Graph,
    state: GraphExecutionState,
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
        parent_step_path=None,
        ancestor_skipped=False,
        include_not_reached=include_not_reached,
        prefix="",
    )
    return "\n".join(lines)


def append_execution_graph(
    lines: list[str],
    graph: Graph,
    *,
    snapshot: GraphExecutionSnapshot,
    active_step_paths: set[StepPath],
    label_path: tuple[str, ...],
    parent_step_path: StepPath | None,
    ancestor_skipped: bool,
    include_not_reached: bool,
    prefix: str,
) -> None:
    graph_step_paths = step_paths_for_mount(graph, parent_step_path=parent_step_path)
    for index, step in enumerate(graph.steps):
        append_execution_step(
            lines,
            graph,
            step,
            snapshot=snapshot,
            active_step_paths=active_step_paths,
            label_path=label_path,
            graph_step_paths=graph_step_paths,
            ancestor_skipped=ancestor_skipped,
            include_not_reached=include_not_reached,
            is_last=index == len(graph.steps) - 1,
            prefix=prefix,
        )


def append_execution_step(
    lines: list[str],
    graph: Graph,
    step: Step,
    *,
    snapshot: GraphExecutionSnapshot,
    active_step_paths: set[StepPath],
    label_path: tuple[str, ...],
    graph_step_paths: Mapping[Step, StepPath],
    ancestor_skipped: bool,
    include_not_reached: bool,
    is_last: bool,
    prefix: str,
) -> None:
    step_path = graph_step_paths[step]
    step_label = resolve_step_label(step, step_path)
    step_label_path = (*label_path, step_label)
    status = resolve_execution_status(
        step,
        step_path=step_path,
        snapshot=snapshot,
        active_step_paths=active_step_paths,
        ancestor_skipped=ancestor_skipped,
    )
    if status is ExecutionRenderStatus.NOT_REACHED and not include_not_reached:
        return
    branch = "└─ " if is_last else "├─ "
    child_prefix = prefix + ("   " if is_last else "│  ")
    action = step.action
    base = (
        f"{step_label} [graph]"
        if isinstance(action, Graph)
        else render_step_label(step, step_path=step_path)
    )
    lines.append(
        f"{prefix}{branch}{base}"
        f"{render_static_suffix(graph, step)}"
        f"{render_execution_condition_suffix(step, step_path=step_path, condition_values=snapshot.runtime_condition_values)}"
        f" [{status.value}]"
        f"{render_execution_result_suffix(step_path, snapshot.results)}"
        f"{render_graph_after_suffix(graph, step, step_paths=graph_step_paths)}"
    )
    if not isinstance(action, Graph):
        return
    append_execution_graph(
        lines,
        action,
        snapshot=snapshot,
        active_step_paths=active_step_paths,
        label_path=step_label_path,
        parent_step_path=step_path,
        ancestor_skipped=(ancestor_skipped or status is ExecutionRenderStatus.SKIPPED),
        include_not_reached=include_not_reached,
        prefix=child_prefix,
    )


def resolve_execution_status(
    step: Step,
    *,
    step_path: StepPath,
    snapshot: GraphExecutionSnapshot,
    active_step_paths: set[StepPath],
    ancestor_skipped: bool,
) -> ExecutionRenderStatus:
    if failure := snapshot.node_failures.get(step_path):
        return ExecutionRenderStatus(failure.value)
    if ancestor_skipped:
        return ExecutionRenderStatus.SKIPPED_BY_ANCESTOR
    if step_path in snapshot.skipped_steps:
        return ExecutionRenderStatus.SKIPPED
    if isinstance(step.action, Graph):
        if (
            step_path in snapshot.completed_composites
            or step_path in snapshot.completed_steps
        ):
            return ExecutionRenderStatus.COMPLETED
        if step_path in snapshot.entered_composites or step_path in active_step_paths:
            return ExecutionRenderStatus.ENTERED
        return ExecutionRenderStatus.NOT_REACHED
    if step_path in active_step_paths:
        if step_path in snapshot.completed_steps:
            return ExecutionRenderStatus.EXECUTED
        return ExecutionRenderStatus.RUNNING
    return ExecutionRenderStatus.NOT_REACHED


def render_execution_condition_suffix(
    step: Step,
    *,
    step_path: StepPath,
    condition_values: Mapping[StepPath, bool],
) -> str:
    """渲染 runtime 已记录的条件值，不在渲染阶段重新执行 predicate。"""

    if step.condition is None:
        return ""
    value = condition_values.get(step_path)
    if value is None:
        return " [condition=pending]"
    return f" [condition={str(value).lower()}]"


def active_execution_step_paths(
    snapshot: GraphExecutionSnapshot,
) -> set[StepPath]:
    active: set[StepPath] = set()
    for step_path in [
        *snapshot.executed_steps,
        *snapshot.entered_composites,
        *snapshot.skipped_steps,
        *snapshot.node_failures,
    ]:
        current: StepPath | None = step_path
        while current is not None:
            active.add(current)
            current = current.parent
    return active


def deepest_running_step_paths(
    snapshot: GraphExecutionSnapshot,
) -> tuple[StepPath, ...]:
    """返回当前未完成路径中最深的 step，避免同时展示其执行中祖先。"""

    running = [
        step_path
        for step_path in snapshot.executed_steps
        if step_path not in snapshot.completed_steps
        and step_path not in snapshot.skipped_steps
        and step_path not in snapshot.node_failures
    ]
    running_set = set(running)
    ancestors_with_running_children: set[StepPath] = set()
    for step_path in running:
        current = step_path.parent
        while current is not None:
            if current in running_set:
                ancestors_with_running_children.add(current)
            current = current.parent
    return tuple(
        step_path
        for step_path in running
        if step_path not in ancestors_with_running_children
    )


def collect_step_labels(
    graph: Graph,
    *,
    label_path: tuple[str, ...] = (),
    parent_step_path: StepPath | None = None,
) -> tuple[tuple[StepPath, tuple[str, ...]], ...]:
    out: list[tuple[StepPath, tuple[str, ...]]] = []
    for step, step_path in step_paths_for_mount(
        graph,
        parent_step_path=parent_step_path,
    ).items():
        step_label_path = (*label_path, resolve_step_label(step, step_path))
        out.append((step_path, step_label_path))
        if isinstance(step.action, Graph):
            out.extend(
                collect_step_labels(
                    step.action,
                    label_path=step_label_path,
                    parent_step_path=step_path,
                )
            )
    return tuple(out)


def collect_leaf_steps(
    graph: Graph,
    *,
    label_path: tuple[str, ...] = (),
    parent_step_path: StepPath | None = None,
) -> tuple[tuple[StepPath, tuple[str, ...]], ...]:
    out: list[tuple[StepPath, tuple[str, ...]]] = []
    for step, step_path in step_paths_for_mount(
        graph,
        parent_step_path=parent_step_path,
    ).items():
        step_label_path = (*label_path, resolve_step_label(step, step_path))
        if isinstance(step.action, Graph):
            out.extend(
                collect_leaf_steps(
                    step.action,
                    label_path=step_label_path,
                    parent_step_path=step_path,
                )
            )
            continue
        out.append((step_path, step_label_path))
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
