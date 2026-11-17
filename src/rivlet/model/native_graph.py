"""Experimental: lazy declaration references for Rust-owned graph contents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .._native import _NativeGraph, _NativeStep
from ..interface import GraphInterface, InputPort, OutputPort
from .condition import _BoundNativeCondition

if TYPE_CHECKING:
    from ..interface import InputEndpoint, OutputEndpoint, PortName
    from . import GraphEdge, NodeKind
    from .identity import NodePath
    from .node import Node

type _Endpoint = tuple[int, tuple[PortName] | None]


@dataclass(slots=True)
class _NativeReferences:
    """Keep one stable reference per native occurrence; never execute callbacks."""

    handle: _NativeGraph
    _nodes: dict[int, NodeKind] = field(default_factory=dict)
    _indexes: dict[Node, int] = field(default_factory=dict)
    _conditions: dict[int, _BoundNativeCondition] = field(default_factory=dict)

    def remember(self, index: int, node: NodeKind) -> None:
        self._nodes[index] = node
        self._indexes[node] = index

    def node(self, index: int) -> NodeKind:
        if index in self._nodes:
            return self._nodes[index]
        from .graph import Graph
        from .metadata import _NodeMeta
        from .step import Step

        identity, label, body, condition = self.handle.node_descriptor(index)
        node = (
            Step._from_native(body, identity, label, None)
            if isinstance(body, _NativeStep)
            else Graph._from_native(body, identity, label, None, self)
        )
        self.remember(index, node)
        if condition is not None:
            object.__setattr__(
                node, "_meta", _NodeMeta(node.id, node.label, self.condition(condition))
            )
        return node

    def output(self, endpoint: _Endpoint) -> OutputEndpoint:
        node, port = endpoint
        reference = self.node(node)
        if port is None:
            return reference
        from .graph import Graph

        if not isinstance(reference, Graph):
            raise TypeError("native output port must belong to a Graph")
        return OutputPort(reference, port[0])

    def input(self, endpoint: _Endpoint) -> InputEndpoint:
        node, port = endpoint
        reference = self.node(node)
        if port is None:
            return reference
        from .graph import Graph

        if not isinstance(reference, Graph):
            raise TypeError("native input port must belong to a Graph")
        return InputPort(reference, port[0])

    def condition(self, index: int) -> _BoundNativeCondition:
        if index not in self._conditions:
            callback, sources = self.handle.condition_descriptor(index)
            self._conditions[index] = _BoundNativeCondition(
                callback, tuple(self.output(source) for source in sources)
            )
        return self._conditions[index]


@dataclass(slots=True)
class _NativeContents:
    """Only explicit inspection creates Python views; compilation uses the handle."""

    handle: _NativeGraph
    references: _NativeReferences
    _nodes: tuple[NodeKind, ...] | None = None
    _edges: tuple[GraphEdge, ...] | None = None
    _interface: GraphInterface | None = None

    def mounted_paths(self, parent: NodePath) -> dict[Node, NodePath]:
        """Locate existing views without creating unreferenced native declarations."""
        from .identity import NodePath

        candidates = tuple(self.references._indexes.items())
        paths = self.handle.mounted_paths([index for _, index in candidates])
        return {
            reference: NodePath(
                (*parent.segments, *path[0]), labels=(*parent.labels, *path[1])
            )
            for (reference, _), path in zip(candidates, paths, strict=True)
            if path is not None
        }

    def declared_members(self, references: set[Node]) -> tuple[Node, ...]:
        """Validate already-referenced descendants without expanding their siblings."""
        candidates = [
            (reference, self.references._indexes[reference])
            for reference in references
            if reference in self.references._indexes
        ]
        included = self.handle.descendants([index for _, index in candidates])
        return tuple(
            reference
            for (reference, _), present in zip(candidates, included, strict=True)
            if present
        )

    @property
    def nodes(self) -> tuple[NodeKind, ...]:
        if self._nodes is None:
            self._nodes = tuple(
                self.references.node(index) for index in self.handle.node_indexes()
            )
        return self._nodes

    @property
    def edges(self) -> tuple[GraphEdge, ...]:
        if self._edges is None:
            self._edges = self._resolve_edges()
        return self._edges

    def _resolve_edges(self) -> tuple[GraphEdge, ...]:
        from .edge import Edge
        from .graph import Graph
        from .step import Step

        nodes = self.nodes
        edges: list[GraphEdge] = []
        specifications = self.handle.edge_descriptors()
        for specification in specifications:
            match specification:
                case (0, source, target, condition):
                    edges.append(
                        Edge(
                            nodes[source],
                            nodes[target],
                            condition=None
                            if condition is None
                            else self.references.condition(condition),
                        )
                    )
                case (1, source, target, output, input_, condition):
                    provider, consumer = nodes[source], nodes[target]
                    if not isinstance(provider, Graph) or not isinstance(
                        consumer, Graph
                    ):
                        raise TypeError(
                            "native port connection requires Graph endpoints"
                        )
                    edges.append(
                        Edge(
                            provider,
                            consumer,
                            ports=(output, input_),
                            condition=None
                            if condition is None
                            else self.references.condition(condition),
                        )
                    )
                case (2, sources, target):
                    receiver = self.references.input(target)
                    if not isinstance(receiver, (Step, InputPort)):
                        raise TypeError(
                            "native result receiver must be a Step or input port"
                        )
                    edges.extend(
                        Edge.results(
                            tuple(self.references.output(source) for source in sources),
                            receiver,
                        )
                    )
                case _:
                    raise TypeError("unsupported native edge descriptor")
        return tuple(edges)

    @property
    def interface(self) -> GraphInterface:
        if self._interface is None:
            inputs, outputs = self.handle.interface_descriptor()
            self._interface = GraphInterface(
                inputs={
                    name: tuple(
                        self.references.input(endpoint) for endpoint in endpoints
                    )
                    for name, endpoints in inputs
                },
                outputs={
                    name: self.references.output(endpoint) for name, endpoint in outputs
                },
            )
        return self._interface
