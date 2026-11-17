"""Public structural inspection, independent of rendering and runtime state."""

from __future__ import annotations

from typing import TYPE_CHECKING, overload

from .schema import CompilationDocument, DeclarationDocument, GraphDocument

if TYPE_CHECKING:
    from ..compile import GraphCompilation
    from ..model import Graph, Node


@overload
def export_graph(subject: Graph | Node) -> DeclarationDocument: ...


@overload
def export_graph(subject: GraphCompilation) -> CompilationDocument: ...


def export_graph(subject: Graph | Node | GraphCompilation) -> GraphDocument:
    """Return detached JSON data without executing actions or runtime conditions.

    A Graph is inspected without evaluating static conditions. A GraphCompilation
    exposes decisions already made by compile_graph; it is never recompiled here.
    """
    from typing import cast

    from .._native import validate_graph_document
    from ..compile import GraphCompilation
    from ..model import Graph, NodePath, Step, resolve_node_label
    from .compiled import export_compilation
    from .declaration import index_declaration

    if isinstance(subject, (Step, Graph)):
        document = index_declaration(
            Graph(nodes=(subject,)),
            label=resolve_node_label(subject, NodePath((0,))),
        ).document
        return cast("DeclarationDocument", validate_graph_document(document))
    if isinstance(subject, GraphCompilation):
        return cast(
            "CompilationDocument", validate_graph_document(export_compilation(subject))
        )
    raise TypeError("export_graph requires Graph, Node or GraphCompilation")


__all__ = [
    "CompilationDocument",
    "DeclarationDocument",
    "GraphDocument",
    "export_graph",
]
