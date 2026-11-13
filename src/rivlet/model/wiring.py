"""Explicit graph wiring and composition normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GraphError
from .edge import (
    ConditionalPrerequisite,
    Edge,
    _condition_uses,
    prerequisite_node,
)

if TYPE_CHECKING:
    from ..interface import GraphInterfaceInput, OutputEndpoint
    from ..static import StaticSelection
    from ..static_conditions import StaticConditions, StaticConditionsInput
    from . import ConditionInput, GraphWiringOperand, NodeKind, NodePrerequisite
    from .edge import GraphEdge
    from .graph import Graph, GraphObservationMessages
    from .node import Node


@dataclass(frozen=True, slots=True, init=False)
class Results:
    """Declare a nonempty ordered group of action-result prerequisites."""

    sources: tuple[OutputEndpoint, *tuple[OutputEndpoint, ...]]

    def __init__(self, source: OutputEndpoint, *sources: OutputEndpoint) -> None:
        object.__setattr__(self, "sources", (source, *sources))


@dataclass(frozen=True, slots=True)
class NodeWiring:
    """把 step 接入当前图时的接线输入。

    `after` 中的普通前置与 Results 只属于这次图声明；`Graph` 构造时会把它一次解析为
    引用真实 `Node` 对象的边，运行期不保留该包装器。
    """

    node: NodeKind
    after: tuple[NodePrerequisite | Results, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphWiring:
    """由组合 DSL 产生、尚未物化为 Graph 的规范化接线。"""

    nodes: tuple[NodeKind, ...]
    edges: tuple[GraphEdge, ...] = ()
    static: tuple[StaticConditions, ...] = ()
    static_selections: tuple[StaticSelection, ...] = ()

    def _boundary_nodes(self) -> tuple[set[Node], set[Node]]:
        sources: set[Node] = set()
        targets: set[Node] = set()

        # O(V + E + I) 推导组合边界。只计算当前接线内部的条件输入，
        # 否则子表达式引用外部 probe 会错误地抹掉其入口，丢失 >> 的顺序边。
        known_steps: set[Node] | None = None
        for edge in self.edges:
            sources.add(edge.source)
            targets.add(edge.target)
        for target, condition in _condition_uses(self.nodes, self.edges):
            for source in condition.inputs:
                if known_steps is None:
                    known_steps = set(self.nodes)
                if source in known_steps:
                    sources.add(source)
                    targets.add(target)
        return sources, targets

    @property
    def entries(self) -> tuple[Node, ...]:
        _, targets = self._boundary_nodes()
        return tuple(step for step in self.nodes if step not in targets)

    @property
    def exits(self) -> tuple[Node, ...]:
        sources, _ = self._boundary_nodes()
        return tuple(step for step in self.nodes if step not in sources)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        return _parallel_wiring(self, other)

    def graph(
        self,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> Graph:
        """将当前接线显式物化为一张 Graph。"""

        from .graph import Graph

        return Graph.from_wiring(
            self,
            label=label,
            condition=condition,
            interface=interface,
            observation=observation,
            static=static,
            static_selections=static_selections,
        )


def _as_graph_wiring(value: GraphWiringOperand) -> GraphWiring:
    from .graph import Graph
    from .step import Step

    match value:
        case GraphWiring():
            return value
        case Step() | Graph():
            return GraphWiring(nodes=(value,))
        case _:
            raise TypeError("graph wiring requires Node, Step, Graph or GraphWiring")


def _sequence_wiring(
    left: GraphWiringOperand,
    right: GraphWiringOperand,
) -> GraphWiring:
    left_wiring = _as_graph_wiring(left)
    right_wiring = _as_graph_wiring(right)
    connecting_edges = tuple(
        Edge(source=source, target=target)
        for source in left_wiring.exits
        for target in right_wiring.entries
    )
    return GraphWiring(
        nodes=(*left_wiring.nodes, *right_wiring.nodes),
        edges=(*left_wiring.edges, *right_wiring.edges, *connecting_edges),
        static=(*left_wiring.static, *right_wiring.static),
        static_selections=(
            *left_wiring.static_selections,
            *right_wiring.static_selections,
        ),
    )


def _parallel_wiring(
    left: GraphWiringOperand,
    right: GraphWiringOperand,
) -> GraphWiring:
    left_wiring = _as_graph_wiring(left)
    right_wiring = _as_graph_wiring(right)
    return GraphWiring(
        nodes=(*left_wiring.nodes, *right_wiring.nodes),
        edges=(*left_wiring.edges, *right_wiring.edges),
        static=(*left_wiring.static, *right_wiring.static),
        static_selections=(
            *left_wiring.static_selections,
            *right_wiring.static_selections,
        ),
    )


def _graph_wiring_from_node_wirings(
    wiring: tuple[NodeWiring, ...],
) -> GraphWiring:
    from .validation import _validated_nodes

    for item in wiring:
        if not isinstance(item, NodeWiring):
            raise GraphError(
                "Graph.from_wiring() accepts only NodeWiring or GraphWiring"
            )
    nodes = tuple(item.node for item in wiring)
    graph_label = "<structure>"
    known_steps = _validated_nodes(nodes, graph_label=graph_label)
    declared_edges: list[GraphEdge] = []
    for item in wiring:
        target = item.node
        result_sources: list[OutputEndpoint] = []
        execution_edges: list[GraphEdge] = []
        for prerequisite in item.after:
            if isinstance(prerequisite, Results):
                result_sources.extend(prerequisite.sources)
                continue
            source = prerequisite_node(prerequisite)
            if source not in known_steps:
                raise GraphError(
                    f"graph '{graph_label}' step '{target.label}' has prerequisite outside this graph: {source.label}"
                )
            execution_edges.append(
                Edge(
                    source=source,
                    target=target,
                    condition=prerequisite.condition.as_input()
                    if isinstance(prerequisite, ConditionalPrerequisite)
                    else None,
                )
            )
        # 当前接线的时间/临时空间为 O(P + R)：P 是 after 条目数，R 是结果来源总数。
        # 按出现顺序把所有结果来源组合成一个完整参数组。
        if result_sources:
            from .step import Step

            if not isinstance(target, Step):
                raise GraphError(
                    "wire Results require a Step receiver; use an input port for a graph"
                )
            declared_edges.extend(Edge.results(tuple(result_sources), target))
        declared_edges.extend(execution_edges)
    return GraphWiring(nodes=nodes, edges=tuple(declared_edges))
