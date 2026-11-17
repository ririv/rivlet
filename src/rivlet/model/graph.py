"""Concrete graph nodes with reusable structure and graph-owned interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ..interface import GraphInterface, GraphInterfaceInput, GraphInterfaceView
from ..static import Case, StaticSelection, resolve_selection_bindings
from ..static_conditions import (
    BoundStaticCondition,
    StaticConditions,
    StaticConditionsInput,
    StaticTarget,
    resolve_static_bindings,
    static_groups,
)
from .edge import GraphEdge, _condition_uses
from .metadata import _NodeMeta

if TYPE_CHECKING:
    from collections.abc import Mapping

    from . import (
        ConditionInput,
        GraphWiringInput,
        GraphWiringOperand,
        NodeKind,
        NodePrerequisite,
    )
    from .condition import _RuntimeCondition
    from .identity import NodeId
    from .node import Node
    from .wiring import GraphWiring, NodeWiring


type GraphMode = Literal["dynamic", "compiled"]


@dataclass(frozen=True, slots=True)
class GraphObservationMessages:
    before: str | None = None
    after_success: str | None = None


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Graph:
    """One graph node; metadata and exposed ports belong to this declaration."""

    nodes: tuple[NodeKind, ...]
    edges: tuple[GraphEdge, ...]
    observation: GraphObservationMessages
    static: tuple[StaticConditions, ...]
    static_selections: tuple[StaticSelection, ...]
    _interface: GraphInterface = field(repr=False)
    _meta: _NodeMeta = field(repr=False)
    _selection_bindings: Mapping[Node, tuple[StaticSelection, Case]] = field(repr=False)
    _static_bindings: Mapping[StaticTarget, BoundStaticCondition] = field(repr=False)

    def __init__(
        self,
        *,
        nodes: tuple[NodeKind, ...] = (),
        edges: tuple[GraphEdge, ...] = (),
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        object.__setattr__(self, "nodes", tuple(nodes))
        object.__setattr__(self, "edges", tuple(edges))
        object.__setattr__(
            self,
            "_interface",
            GraphInterface()
            if interface is None
            else GraphInterface.resolve(interface),
        )
        object.__setattr__(
            self,
            "observation",
            GraphObservationMessages() if observation is None else observation,
        )
        object.__setattr__(self, "static", static_groups(static))
        object.__setattr__(self, "static_selections", tuple(static_selections))
        object.__setattr__(self, "_meta", _NodeMeta.create(label, condition))
        from .validation import validate_graph

        validate_graph(self)
        object.__setattr__(
            self,
            "_selection_bindings",
            resolve_selection_bindings(self.static_selections, nodes=self.nodes),
        )
        object.__setattr__(
            self,
            "_static_bindings",
            resolve_static_bindings(
                self.static,
                nodes=self.nodes,
                edge_pairs={
                    *((edge.source, edge.target) for edge in self.edges),
                    *(
                        (source, target)
                        for target, condition in _condition_uses(self.nodes, self.edges)
                        for source in condition.inputs
                    ),
                }
                if self.static
                else set(),
            ),
        )

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
        from .wiring import GraphWiring, _graph_wiring_from_node_wirings

        normalized = (
            wiring
            if isinstance(wiring, GraphWiring)
            else _graph_wiring_from_node_wirings(wiring)
        )
        return cls(
            nodes=normalized.nodes,
            edges=normalized.edges,
            interface=interface,
            observation=observation,
            static=(*normalized.static, *static_groups(static)),
            static_selections=(*normalized.static_selections, *static_selections),
            label=label,
            condition=condition,
        )

    def _resolve_static_selections(self) -> tuple[Node, ...]:
        inactive: list[Node] = []
        for declaration in self.static_selections:
            selected = declaration.selected_node()
            inactive.extend(
                case.node for case in declaration.cases if case.node is not selected
            )
        return tuple(inactive)

    def incoming_edges(self, node: Node) -> tuple[GraphEdge, ...]:
        return tuple(
            edge for edge in self.edges if edge.target is node and edge.ports is None
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


__all__ = ["Graph", "GraphMode", "GraphObservationMessages"]
