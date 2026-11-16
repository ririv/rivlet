"""One public structure type, independent of its declaration language."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .python_structure import GraphMode, GraphObservationMessages, _PythonStructure

if TYPE_CHECKING:
    from .._native import _NativeStructure
    from ..interface import GraphInterfaceInput
    from ..static import StaticSelection
    from ..static_conditions import StaticConditionsInput
    from ..structure.schema import DeclarationDocument, EdgeDocument, NodeDocument
    from . import GraphEdge, GraphWiringInput, NodeKind


@dataclass(frozen=True, slots=True, init=False)
class GraphStructure:
    """An immutable reusable body with a language-independent structural snapshot."""

    _declaration: _PythonStructure | _NativeStructure = field(repr=False)

    def __init__(
        self,
        nodes: tuple[NodeKind, ...] = (),
        edges: tuple[GraphEdge, ...] = (),
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> None:
        object.__setattr__(
            self,
            "_declaration",
            _PythonStructure(
                nodes, edges, interface, observation, static, static_selections
            ),
        )

    @classmethod
    def _from_declaration(
        cls, declaration: _PythonStructure | _NativeStructure
    ) -> GraphStructure:
        structure = object.__new__(cls)
        object.__setattr__(structure, "_declaration", declaration)
        return structure

    @classmethod
    def from_wiring(
        cls,
        wiring: GraphWiringInput,
        *,
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> GraphStructure:
        structure = object.__new__(cls)
        object.__setattr__(
            structure,
            "_declaration",
            _PythonStructure.from_wiring(
                wiring,
                interface=interface,
                observation=observation,
                static=static,
                static_selections=static_selections,
            ),
        )
        return structure

    def describe(self) -> DeclarationDocument:
        """Return a detached snapshot without compiling or executing host code."""
        from ..structure import export_graph

        return export_graph(self)

    @property
    def observation(self) -> GraphObservationMessages:
        if isinstance(self._declaration, _PythonStructure):
            return self._declaration.observation
        return GraphObservationMessages()

    @property
    def nodes(self) -> tuple[NodeDocument, ...]:
        """Inspect descriptions rather than language-specific executable objects."""
        return tuple(self.describe()["nodes"])

    @property
    def edges(self) -> tuple[EdgeDocument, ...]:
        """Inspect descriptions using the common structural export schema."""
        return tuple(self.describe()["edges"])

    def append_edges(self, edges: tuple[GraphEdge, ...]) -> GraphStructure:
        """Compose edges without replacing existing declarations."""
        from dataclasses import replace

        if not edges:
            return self
        if not isinstance(self._declaration, _PythonStructure):
            raise TypeError(
                "opaque structures must be composed through their public ports"
            )
        structure = object.__new__(type(self))
        object.__setattr__(
            structure,
            "_declaration",
            replace(self._declaration, edges=(*self._declaration.edges, *edges)),
        )
        return structure


__all__ = ["GraphMode", "GraphObservationMessages", "GraphStructure"]
