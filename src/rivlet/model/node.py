"""Shared node contracts without a public wrapper or implementation inheritance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .identity import NodeId, NodePath

if TYPE_CHECKING:
    from . import GraphWiringOperand, Node, NodePrerequisite
    from .condition import _RuntimeCondition
    from .graph import Graph
    from .wiring import GraphWiring, NodeWiring


class NodeLike(Protocol):
    """Common node shape for helpers that do not require graph membership."""

    @property
    def id(self) -> NodeId: ...

    @property
    def label(self) -> str | None: ...

    @property
    def condition(self) -> _RuntimeCondition | None: ...

    def wire(self, after: tuple[NodePrerequisite, ...] = ()) -> NodeWiring: ...

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring: ...

    def __or__(self, other: GraphWiringOperand) -> GraphWiring: ...


type NodeLabelPath = tuple[str, ...]


def node_paths_for_mount(
    graph: Graph,
    *,
    parent_node_path: NodePath | None = None,
) -> dict[Node, NodePath]:
    return {
        node: (
            NodePath(
                (index,), labels=(str(index) if node.label is None else node.label,)
            )
            if parent_node_path is None
            else parent_node_path.child(index, label=node.label)
        )
        for index, node in enumerate(graph.nodes)
    }


def resolve_node_label(node: NodeLike, path: NodePath) -> str:
    return str(path) if node.label is None else node.label
