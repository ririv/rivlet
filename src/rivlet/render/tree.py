from __future__ import annotations

from ..compile import GraphCompilation
from ..errors import GraphError
from ..model import (
    Graph,
    GraphMode,
    Step,
    StepPath,
    resolve_step_label,
    step_paths_for_mount,
)
from .formatting import (
    ensure_compilation,
    render_after_suffix,
    render_compiled_step_label,
    render_condition_suffix,
    render_graph_after_suffix,
    render_graph_port_suffix,
    render_static_suffix,
    render_step_label,
    render_step_path,
)


def render_graph(
    subject: Graph | GraphCompilation,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    match mode:
        case "compiled":
            return render_graph_compilation(
                ensure_compilation(subject, root_label=root_label),
                root_label=root_label,
            )
        case "dynamic":
            if isinstance(subject, GraphCompilation):
                return render_graph_structure(
                    subject.source_graph, root_label=root_label
                )
            return render_graph_structure(subject, root_label=root_label)
        case _:
            raise GraphError(f"unsupported graph render mode: {mode}")


def render_graph_structure(graph: Graph, *, root_label: str) -> str:
    lines = [root_label]
    step_paths = step_paths_for_mount(graph)
    for index, step in enumerate(graph.steps):
        append_tree_step(
            lines,
            graph,
            step,
            step_path=step_paths[step],
            step_paths=step_paths,
            is_last=index == len(graph.steps) - 1,
            prefix="",
        )
    return "\n".join(lines)


def render_graph_compilation(
    compilation: GraphCompilation,
    *,
    root_label: str,
) -> str:
    node_labels = {
        node.path: render_step_path(node.path.labels)
        for node in (*compilation.steps, *compilation.composites)
    }
    lines = [
        f"compilation: {root_label}",
        "compiled_steps",
    ]
    for step in compilation.steps:
        label = render_compiled_step_label(step, step.path.labels)
        lines.append(
            f"- {label}{render_condition_suffix(step)}"
            f"{render_after_suffix(step, step_labels=node_labels)}"
        )
    if compilation.composites:
        node_paths = {
            node.path: node.path.labels
            for node in (*compilation.steps, *compilation.composites)
        }
        lines.append("compiled_composites")
        for composite in compilation.composites:
            children = ", ".join(
                render_step_path(node_paths[child.path]) for child in composite.children
            )
            lines.append(
                f"- {render_step_path(composite.path.labels)}"
                f" [children: {children or '-'}]"
                f"{render_condition_suffix(composite)}"
                f"{render_after_suffix(composite, step_labels=node_labels)}"
            )
    return "\n".join(lines)


def append_tree_step(
    lines: list[str],
    graph: Graph,
    step: Step,
    *,
    step_path: StepPath,
    step_paths: dict[Step, StepPath],
    is_last: bool,
    prefix: str,
) -> None:
    branch = "└─ " if is_last else "├─ "
    child_prefix = prefix + ("   " if is_last else "│  ")
    action = step.action
    step_label = resolve_step_label(step, step_path)
    if isinstance(action, Graph):
        lines.append(
            f"{prefix}{branch}{step_label} [graph]{render_static_suffix(graph, step)}{render_condition_suffix(step)}"
            f"{render_graph_after_suffix(graph, step, step_paths=step_paths)}"
            f"{render_graph_port_suffix(graph, step)}"
        )
        child_step_paths = step_paths_for_mount(action, parent_step_path=step_path)
        for index, child_step in enumerate(action.steps):
            append_tree_step(
                lines,
                action,
                child_step,
                step_path=child_step_paths[child_step],
                step_paths=child_step_paths,
                is_last=index == len(action.steps) - 1,
                prefix=child_prefix,
            )
        return
    lines.append(
        f"{prefix}{branch}{render_step_label(step, step_path=step_path)}"
        f"{render_static_suffix(graph, step)}"
        f"{render_condition_suffix(step)}"
        f"{render_graph_after_suffix(graph, step, step_paths=step_paths)}"
    )
