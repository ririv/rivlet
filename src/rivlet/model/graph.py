"""Concrete graph nodes with reusable structure and graph-owned interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..interface import GraphInterfaceView
from .graph_structure import GraphMode, GraphObservationMessages, GraphStructure
from .metadata import _NodeMeta

if TYPE_CHECKING:
    from collections.abc import Callable

    from .._native import RustGraphStructure
    from ..interface import GraphInterfaceInput
    from ..static import StaticSelection
    from ..static_conditions import StaticConditionsInput
    from . import ConditionInput, GraphWiringInput, GraphWiringOperand, NodePrerequisite
    from .condition import _RuntimeCondition
    from .identity import NodeId
    from .wiring import GraphWiring, NodeWiring


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Graph:
    """One graph node; metadata and exposed ports belong to this declaration."""

    structure: GraphStructure | RustGraphStructure = field(repr=False)
    _meta: _NodeMeta

    def __init__(
        self,
        structure: GraphStructure | RustGraphStructure,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        object.__setattr__(self, "structure", structure)
        object.__setattr__(self, "_meta", _NodeMeta.create(label, condition))

    @property
    def id(self) -> NodeId:
        return self._meta.id

    @property
    def label(self) -> str | None:
        return self._meta.label

    @property
    def condition(self) -> _RuntimeCondition | None:
        return self._meta.condition

    @property
    def interface(self) -> GraphInterfaceView:
        return GraphInterfaceView(self)

    @classmethod
    def from_wiring(
        cls,
        wiring: GraphWiringInput,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> Graph:
        return cls(
            GraphStructure.from_wiring(
                wiring,
                interface=interface,
                observation=observation,
                static=static,
                static_selections=static_selections,
            ),
            label=label,
            condition=condition,
        )

    def wire(self, after: tuple[NodePrerequisite, ...] = ()) -> NodeWiring:
        from .wiring import NodeWiring

        return NodeWiring(self, after)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        from .wiring import _sequence_wiring

        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        from .wiring import _parallel_wiring

        return _parallel_wiring(self, other)


def graph(declaration: Callable[[], GraphWiring]) -> Graph:
    """Build a graph immediately from a zero-argument wiring declaration.

    Only the declaration is called here; step actions remain deferred until
    execution. A named declaration supplies the graph's presentation label.
    """

    name = getattr(declaration, "__name__", None)
    label = name if isinstance(name, str) and name != "<lambda>" else None
    return Graph.from_wiring(declaration(), label=label)


__all__ = ["Graph", "GraphStructure", "GraphMode", "GraphObservationMessages", "graph"]
