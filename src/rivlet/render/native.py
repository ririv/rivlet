"""Render opaque Rust declarations without manufacturing Python nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .formatting import escape_label

if TYPE_CHECKING:
    from .._native import NativeGraphStructure
    from ..model import NodePath


def append_native_tree(
    lines: list[str], body: NativeGraphStructure, *, prefix: str
) -> None:
    nodes = body.nodes()
    incoming: dict[int, list[str]] = {}
    # O(V+E)：预索引入边，避免每个节点再次扫描整层边。
    for edge in body.edges():
        source, target = edge[:2]
        text = nodes[source][1]
        if len(edge) == 4:
            text += f" ({edge[2]} -> {edge[3]})"
        incoming.setdefault(target, []).append(text)
    for index, (_, label, child) in enumerate(nodes):
        last = index == len(nodes) - 1
        branch = "└─ " if last else "├─ "
        suffix = " [native]" if child is None else " [graph]"
        if index in incoming:
            suffix += f" [after: {', '.join(incoming[index])}]"
        lines.append(f"{prefix}{branch}{label}{suffix}")
        if child is not None:
            append_native_tree(lines, child, prefix=prefix + ("   " if last else "│  "))


def append_native_mermaid(
    lines: list[str],
    body: NativeGraphStructure,
    *,
    owner: NodePath,
    label: str,
    indent: str,
) -> None:
    def node_id(path: NodePath) -> str:
        return "native_" + "_".join(map(str, path.segments))

    lines.append(f'{indent}subgraph {node_id(owner)}_body["{escape_label(label)}"]')
    nodes = body.nodes()
    for index, (_, child_label, child) in enumerate(nodes):
        path = owner.child(index)
        lines.append(f'{indent}    {node_id(path)}["{escape_label(child_label)}"]')
        if child is not None:
            append_native_mermaid(
                lines, child, owner=path, label=child_label, indent=indent + "    "
            )
    for edge in body.edges():
        source, target = edge[:2]
        link = " --> "
        if len(edge) == 4:
            label = edge[2] if edge[2] == edge[3] else f"{edge[2]} -> {edge[3]}"
            link = f' -->|"{escape_label(label)}"| '
        lines.append(
            f"{indent}    {node_id(owner.child(source))}{link}{node_id(owner.child(target))}"
        )
    lines.append(f"{indent}end")
