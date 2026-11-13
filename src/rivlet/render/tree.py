from __future__ import annotations

from .._native import NativeGraphStructure
from ..compile import GraphCompilation
from ..errors import GraphError
from ..model import (
    Graph as GraphNode,
)
from ..model import (
    GraphMode,
    GraphStructure,
    Leaf,
    Node,
    NodeKind,
    NodePath,
    node_paths_for_mount,
    resolve_node_label,
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
from .native import append_native_tree


def render_graph(
    subject: GraphStructure | NodeKind | GraphCompilation,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    if isinstance(subject, (Leaf, GraphNode)):
        subject = GraphStructure(nodes=(subject,))
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


def render_graph_structure(graph: GraphStructure, *, root_label: str) -> str:
    lines = [root_label]
    step_paths = node_paths_for_mount(graph)
    for index, step in enumerate(graph.nodes):
        append_tree_step(
            lines,
            graph,
            step,
            node_path=step_paths[step],
            step_paths=step_paths,
            is_last=index == len(graph.nodes) - 1,
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
    graph: GraphStructure,
    step: Node,
    *,
    node_path: NodePath,
    step_paths: dict[Node, NodePath],
    is_last: bool,
    prefix: str,
) -> None:
    branch = "└─ " if is_last else "├─ "
    child_prefix = prefix + ("   " if is_last else "│  ")
    action = step.structure if isinstance(step, GraphNode) else step
    step_label = resolve_node_label(step, node_path)
    if isinstance(action, (GraphStructure, NativeGraphStructure)):
        lines.append(
            f"{prefix}{branch}{step_label} [graph]{render_static_suffix(graph, step)}{render_condition_suffix(step)}"
            f"{render_graph_after_suffix(graph, step, step_paths=step_paths)}"
            f"{render_graph_port_suffix(graph, step)}"
        )
        if isinstance(action, NativeGraphStructure):
            append_native_tree(lines, action, prefix=child_prefix)
            return
        child_step_paths = node_paths_for_mount(action, parent_node_path=node_path)
        for index, child_step in enumerate(action.nodes):
            append_tree_step(
                lines,
                action,
                child_step,
                node_path=child_step_paths[child_step],
                step_paths=child_step_paths,
                is_last=index == len(action.nodes) - 1,
                prefix=child_prefix,
            )
        return
    lines.append(
        f"{prefix}{branch}{render_step_label(step, node_path=node_path)}"
        f"{render_static_suffix(graph, step)}"
        f"{render_condition_suffix(step)}"
        f"{render_graph_after_suffix(graph, step, step_paths=step_paths)}"
    )
