"""Adapt local declarations into shared Rust structural rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._native import render_graph_document
from ..compile import GraphCompilation
from ..errors import GraphError
from ..model import Graph, Step
from ..structure.compiled import export_compilation
from ..structure.declaration import index_declaration
from .formatting import condition_name, ensure_compilation

if TYPE_CHECKING:
    from ..model import GraphMode, NodeKind
    from ..structure.schema import GraphDocument


def render_structure(
    subject: NodeKind | GraphCompilation, *, root_label: str, mode: GraphMode, fmt: str
) -> str:
    if isinstance(subject, (Step, Graph)):
        subject = Graph(nodes=(subject,))
    labels: dict[str, str] = {}
    document: GraphDocument
    if mode == "compiled":
        compilation = ensure_compilation(subject, root_label=root_label)
        document = export_compilation(compilation)
        labels.update(
            (f"condition:{index}", condition_name(condition))
            for index, condition in enumerate(compilation._execution_conditions)
        )
    elif mode == "dynamic":
        graph = (
            subject.source_graph if isinstance(subject, GraphCompilation) else subject
        )
        index = index_declaration(graph, label=root_label)
        document = index.document
        for (_target, condition), identifier in index.condition_objects.items():
            labels[identifier] = condition_name(condition)
        for scope in index.scopes.values():
            for position, binding in enumerate(scope.graph._static_bindings.values()):
                labels[f"{scope.id}/static:{position}"] = binding.condition.description
    else:
        raise GraphError(f"unsupported graph render mode: {mode}")
    return render_graph_document(document, fmt, root_label, labels)
