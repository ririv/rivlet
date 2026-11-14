from __future__ import annotations

from .._native import RustGraphStructure
from ..compile import GraphCompilation, compiled_prerequisite_node
from ..errors import GraphError
from ..model import (
    Edge,
    GraphMode,
    GraphStructure,
    Node,
    NodeKind,
    NodePath,
    Step,
    _condition_uses,
    node_paths_for_mount,
    resolve_node_label,
)
from ..model import (
    Graph as GraphNode,
)
from .formatting import (
    ensure_compilation,
    escape_label,
    mermaid_graph_node_id,
    mermaid_step_node_id,
    port_label,
    render_compiled_step_label,
    render_selection_suffix,
    render_step_label,
    render_step_path,
    result_edge_label,
)
from .rust_graph import append_rust_mermaid


def render_graph_mermaid(
    subject: GraphStructure | NodeKind | GraphCompilation,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    if isinstance(subject, (Step, GraphNode)):
        subject = GraphStructure(nodes=(subject,))
    match mode:
        case "compiled":
            return render_graph_compilation_mermaid(
                ensure_compilation(subject, root_label=root_label),
                root_label=root_label,
            )
        case "dynamic":
            if isinstance(subject, GraphCompilation):
                return render_graph_structure_mermaid(
                    subject.source_graph,
                    root_label=root_label,
                )
            return render_graph_structure_mermaid(subject, root_label=root_label)
        case _:
            raise GraphError(f"unsupported graph render mode: {mode}")


def render_graph_structure_mermaid(graph: GraphStructure, *, root_label: str) -> str:
    lines = ["flowchart TD"]
    append_graph_mermaid(
        lines,
        graph,
        graph_label=root_label,
        graph_path=(root_label,),
        parent_node_path=None,
        indent="    ",
    )
    return "\n".join(lines)


def render_graph_compilation_mermaid(
    compilation: GraphCompilation,
    *,
    root_label: str,
) -> str:
    node_ids = {
        node.path: f"compiled_{index}"
        for index, node in enumerate((*compilation.steps, *compilation.composites))
    }
    lines = [
        "flowchart TD",
        f'    subgraph compiled_graph["{escape_label(root_label)}"]',
    ]
    for step in compilation.steps:
        label = render_compiled_step_label(step, step.path.labels)
        lines.append(f'        {node_ids[step.path]}["{escape_label(label)}"]')
    lines.append("    end")
    if compilation.composites:
        lines.append("    subgraph compiled_composites")
        for composite in compilation.composites:
            label = f"{render_step_path(composite.path.labels)} [composite]"
            lines.append(f'        {node_ids[composite.path]}["{escape_label(label)}"]')
        lines.append("    end")
    for step in compilation.steps:
        target_id = node_ids[step.path]
        for dep in step.after:
            dep_id = compiled_prerequisite_node(dep).path
            lines.append(f"        {node_ids[dep_id]} --> {target_id}")
    for composite in compilation.composites:
        target_id = node_ids[composite.path]
        for dep in composite.after:
            dep_id = compiled_prerequisite_node(dep).path
            lines.append(f"        {node_ids[dep_id]} --> {target_id}")
        for child in composite.children:
            lines.append(f"        {target_id} -.-> {node_ids[child.path]}")
    return "\n".join(lines)


def append_graph_mermaid(
    lines: list[str],
    graph: GraphStructure,
    *,
    graph_label: str,
    graph_path: tuple[str, ...],
    parent_node_path: NodePath | None,
    indent: str,
) -> None:
    lines.append(
        f'{indent}subgraph {mermaid_graph_node_id(graph_path)}["{escape_label(graph_label)}"]'
    )
    node_ids: dict[Node, str] = {}
    step_paths = node_paths_for_mount(graph, parent_node_path=parent_node_path)
    for index, step in enumerate(graph.nodes):
        node_path = step_paths[step]
        step_label = resolve_node_label(step, node_path)
        selection_suffix = render_selection_suffix(graph, step)
        node_id = mermaid_step_node_id(graph_path, step_label, index)
        node_ids[step] = node_id
        action = step.structure if isinstance(step, GraphNode) else step
        if isinstance(action, (GraphStructure, RustGraphStructure)):
            lines.append(
                f'{indent}    {node_id}["{escape_label(step_label + selection_suffix)}"]'
            )
            if isinstance(action, RustGraphStructure):
                append_rust_mermaid(
                    lines,
                    action,
                    label=step_label,
                    owner=node_path,
                    indent=indent + "    ",
                )
            else:
                append_graph_mermaid(
                    lines,
                    action,
                    graph_label=step_label,
                    graph_path=(*graph_path, step_label),
                    parent_node_path=node_path,
                    indent=indent + "    ",
                )
        else:
            lines.append(
                f'{indent}    {node_id}["{escape_label(render_step_label(step, node_path=node_path) + selection_suffix)}"]'
            )
    for edge in graph.edges:
        if edge._result_index is not None:
            label = result_edge_label(edge).replace("action input:", "action input")
            lines.append(
                f'{indent}    {node_ids[edge.source]} -->|"{escape_label(label)}"| {node_ids[edge.target]}'
            )
        elif isinstance(edge, Edge) and edge.ports is not None:
            label = (
                port_label(edge.ports[0])
                if edge.ports[0] == edge.ports[1]
                else f"{port_label(edge.ports[0])} -> {port_label(edge.ports[1])}"
            )
            if edge.condition is not None:
                label += " (conditional)"
            lines.append(
                f'{indent}    {node_ids[edge.source]} -->|"{escape_label(label)}"| {node_ids[edge.target]}'
            )
        else:
            lines.append(
                f"{indent}    {node_ids[edge.source]} --> {node_ids[edge.target]}"
            )
    for target, condition in _condition_uses(graph.nodes, graph.edges):
        receiver = "condition" if condition is target.condition else "edge condition"
        for index, source in enumerate(condition.inputs):
            lines.append(
                f'{indent}    {node_ids[source]} -.->|"{receiver} input {index}"| {node_ids[target]}'
            )
    lines.append(f"{indent}end")
