"""Concrete graph nodes with reusable structure and graph-owned interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ..interface import (
    GraphInterface,
    GraphInterfaceInput,
    GraphInterfaceView,
    OutputPort,
)
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
from .native_graph import _NativeContents

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .._native import _NativeCondition, _NativeGraph
    from . import (
        ConditionInput,
        GraphWiringInput,
        GraphWiringOperand,
        NodeKind,
        NodePrerequisite,
    )
    from .condition import _BoundNativeCondition, _RuntimeCondition
    from .identity import NodeId
    from .native_graph import _NativeReferences
    from .node import Node
    from .wiring import GraphWiring, NodeWiring


type GraphMode = Literal["dynamic", "compiled"]


@dataclass(frozen=True, slots=True)
class GraphObservationMessages:
    before: str | None = None
    after_success: str | None = None


@dataclass(frozen=True, slots=True)
class _PythonContents:
    nodes: tuple[NodeKind, ...]
    edges: tuple[GraphEdge, ...]
    interface: GraphInterface


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Graph:
    """One graph node; metadata and exposed ports belong to this declaration."""

    _contents: _PythonContents | _NativeContents = field(repr=False)
    observation: GraphObservationMessages
    static: tuple[StaticConditions, ...]
    static_selections: tuple[StaticSelection, ...]
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
        object.__setattr__(
            self,
            "_contents",
            _PythonContents(
                tuple(nodes),
                tuple(edges),
                GraphInterface()
                if interface is None
                else GraphInterface.resolve(interface),
            ),
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
                        (
                            source.graph if isinstance(source, OutputPort) else source,
                            target,
                        )
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
    def nodes(self) -> tuple[NodeKind, ...]:
        return self._contents.nodes

    @property
    def edges(self) -> tuple[GraphEdge, ...]:
        return self._contents.edges

    @property
    def _interface(self) -> GraphInterface:
        return self._contents.interface

    @property
    def _native(self) -> _NativeGraph | None:
        return (
            self._contents.handle
            if isinstance(self._contents, _NativeContents)
            else None
        )

    @classmethod
    def _from_native(
        cls,
        handle: _NativeGraph,
        identity: int,
        label: str,
        condition: _NativeCondition | None,
        references: _NativeReferences | None = None,
    ) -> Graph:
        from types import MappingProxyType

        from .condition import _bind_native_condition
        from .identity import NodeId
        from .native_graph import _NativeReferences

        references = _NativeReferences(handle) if references is None else references
        node = object.__new__(cls)
        object.__setattr__(node, "_contents", _NativeContents(handle, references))
        object.__setattr__(
            node,
            "_meta",
            _NodeMeta(NodeId(identity), label, _bind_native_condition(condition)),
        )
        object.__setattr__(node, "observation", GraphObservationMessages())
        object.__setattr__(node, "static", ())
        object.__setattr__(node, "static_selections", ())
        object.__setattr__(node, "_static_bindings", MappingProxyType({}))
        object.__setattr__(node, "_selection_bindings", MappingProxyType({}))
        owner = handle.owner_index()
        if owner is not None:
            references.remember(owner, node)
        return node

    @property
    def label(self) -> str | None:
        return self._meta.label

    @property
    def condition(self) -> _RuntimeCondition | _BoundNativeCondition | None:
        if self._meta.condition is None and isinstance(self._contents, _NativeContents):
            owner = self._contents.handle.owner_index()
            if owner is not None:
                condition = self._contents.handle.node_condition(owner)
                if condition is not None:
                    return self._contents.references.condition(condition)
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
