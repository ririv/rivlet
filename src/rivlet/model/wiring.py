"""Explicit graph wiring and composition normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GraphError
from .edge import (
    ConditionalPrerequisite,
    Edge,
    _condition_uses,
    prerequisite_step,
)

if TYPE_CHECKING:
    from ..interface import GraphInterfaceInput
    from ..static import StaticSelection
    from ..static_conditions import StaticConditions, StaticConditionsInput
    from . import ConditionInput, GraphWiringOperand, StepPrerequisite
    from .edge import GraphEdge
    from .graph import Graph, GraphObservationMessages
    from .step import Step


@dataclass(frozen=True, slots=True, init=False)
class Results:
    """Declare a nonempty ordered group of action-result prerequisites."""

    sources: tuple[Step, *tuple[Step, ...]]

    def __init__(self, source: Step, *sources: Step) -> None:
        object.__setattr__(self, "sources", (source, *sources))


@dataclass(frozen=True, slots=True)
class StepWiring:
    """把 step 接入当前图时的接线输入。

    `after` 中的普通前置与 Results 只属于这次图声明；`Graph` 构造时会把它一次解析为
    引用真实 `Step` 对象的边，运行期不保留该包装器。
    """

    step: Step
    after: tuple[StepPrerequisite | Results, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphWiring:
    """由组合 DSL 产生、尚未物化为 Graph 的规范化接线。"""

    steps: tuple[Step, ...]
    edges: tuple[GraphEdge, ...] = ()
    static: tuple[StaticConditions, ...] = ()
    static_selections: tuple[StaticSelection, ...] = ()

    def _boundary_nodes(self) -> tuple[set[Step], set[Step]]:
        sources: set[Step] = set()
        targets: set[Step] = set()

        # O(V + E + I) 推导组合边界。只计算当前接线内部的条件输入，
        # 否则子表达式引用外部 probe 会错误地抹掉其入口，丢失 >> 的顺序边。
        known_steps: set[Step] | None = None
        for edge in self.edges:
            sources.add(edge.source)
            targets.add(edge.target)
        for target, condition in _condition_uses(self.steps, self.edges):
            for source in condition.inputs:
                if known_steps is None:
                    known_steps = set(self.steps)
                if source in known_steps:
                    sources.add(source)
                    targets.add(target)
        return sources, targets

    @property
    def entries(self) -> tuple[Step, ...]:
        _, targets = self._boundary_nodes()
        return tuple(step for step in self.steps if step not in targets)

    @property
    def exits(self) -> tuple[Step, ...]:
        sources, _ = self._boundary_nodes()
        return tuple(step for step in self.steps if step not in sources)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        return _parallel_wiring(self, other)

    def graph(
        self,
        *,
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> Graph:
        """将当前接线显式物化为一张 Graph。"""

        from .graph import Graph

        return Graph.from_wiring(
            self,
            interface=interface,
            observation=observation,
            static=static,
            static_selections=static_selections,
        )

    def step(
        self,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> Step:
        """Materialize this wiring and mount the whole graph as one Step."""

        return self.graph().step(label=label, condition=condition)


def _as_graph_wiring(value: GraphWiringOperand) -> GraphWiring:
    from .graph import Graph
    from .leaf import Leaf
    from .step import Step

    match value:
        case GraphWiring():
            return value
        case Step():
            return GraphWiring(steps=(value,))
        case Leaf() | Graph():
            # 每次出现裸节点都创建独立挂载；复用已归一化接线时保留原 Step。
            return GraphWiring(steps=(Step(value),))
        case _:
            raise TypeError("graph wiring requires Step, Leaf, Graph or GraphWiring")


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
        steps=(*left_wiring.steps, *right_wiring.steps),
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
        steps=(*left_wiring.steps, *right_wiring.steps),
        edges=(*left_wiring.edges, *right_wiring.edges),
        static=(*left_wiring.static, *right_wiring.static),
        static_selections=(
            *left_wiring.static_selections,
            *right_wiring.static_selections,
        ),
    )


def _graph_wiring_from_step_wirings(
    wiring: tuple[StepWiring, ...],
) -> GraphWiring:
    from .validation import _validated_steps

    for item in wiring:
        if not isinstance(item, StepWiring):
            raise GraphError(
                "Graph.from_wiring() accepts only StepWiring or GraphWiring"
            )
    steps = tuple(item.step for item in wiring)
    graph_label = "<structure>"
    known_steps = _validated_steps(steps, graph_label=graph_label)
    declared_edges: list[GraphEdge] = []
    for item in wiring:
        target = item.step
        result_sources: list[Step] = []
        execution_edges: list[GraphEdge] = []
        for prerequisite in item.after:
            if isinstance(prerequisite, Results):
                result_sources.extend(prerequisite.sources)
                continue
            source = prerequisite_step(prerequisite)
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
            declared_edges.extend(Edge.results(tuple(result_sources), target))
        declared_edges.extend(execution_edges)
    return GraphWiring(steps=steps, edges=tuple(declared_edges))
