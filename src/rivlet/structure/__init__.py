"""Public structural inspection, independent of rendering and runtime state."""

from __future__ import annotations

from typing import TYPE_CHECKING, overload

from .schema import CompilationDocument, DeclarationDocument, GraphDocument

if TYPE_CHECKING:
    from ..compile import GraphCompilation
    from ..model import Graph


@overload
def export_graph(subject: Graph) -> DeclarationDocument: ...


@overload
def export_graph(subject: GraphCompilation) -> CompilationDocument: ...


def export_graph(subject: Graph | GraphCompilation) -> GraphDocument:
    """Return detached JSON data without executing actions or runtime conditions.

    A Graph is inspected without evaluating static conditions. A GraphCompilation
    exposes decisions already made by compile_graph; it is never recompiled here.
    """
    from ..compile import GraphCompilation
    from ..model import Graph
    from .compiled import export_compilation
    from .declaration import index_declaration

    if isinstance(subject, Graph):
        return index_declaration(subject).document
    if isinstance(subject, GraphCompilation):
        return export_compilation(subject)
    raise TypeError("export_graph requires Graph or GraphCompilation")


__all__ = [
    "CompilationDocument",
    "DeclarationDocument",
    "GraphDocument",
    "export_graph",
]
