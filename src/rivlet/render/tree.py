"""Public tree entry points backed by the shared Rust renderer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .structure import render_structure

if TYPE_CHECKING:
    from ..compile import GraphCompilation
    from ..model import GraphMode, NodeKind


def render_graph(
    subject: NodeKind | GraphCompilation,
    *,
    root_label: str,
    mode: GraphMode = "dynamic",
) -> str:
    return render_structure(subject, root_label=root_label, mode=mode, fmt="tree")


def render_graph_compilation(compilation: GraphCompilation, *, root_label: str) -> str:
    return render_structure(
        compilation, root_label=root_label, mode="compiled", fmt="tree"
    )
