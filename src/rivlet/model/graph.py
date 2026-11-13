"""Graph declaration boundaries and graph-scoped interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ..interface import GraphInterface, GraphInterfaceInput
from ..static import Case, StaticSelection, resolve_selection_bindings
from ..static_conditions import (
    BoundStaticCondition,
    StaticConditions,
    StaticConditionsInput,
    StaticTarget,
    resolve_static_bindings,
    static_groups,
)
from .edge import Edge, GraphEdge, _condition_uses
from .wiring import (
    GraphWiring,
    _graph_wiring_from_step_wirings,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from . import ConditionInput, GraphWiringInput
    from .step import Step


type GraphMode = Literal["dynamic", "compiled"]


@dataclass(frozen=True, slots=True)
class GraphObservationMessages:
    before: str | None = None
    after_success: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class Graph:
    """DAG 图结构本体。

    `Graph` 表达的是一张有向无环图（DAG），描述图结构本体，
    以及这张图的静态条件与选择声明。

    它表达的是：
    - 这张图有哪些 step
    - step 之间有哪些依赖边
    - 哪些 step 挂子图
    - 图级 observation
    - 这张图的节点和边在哪些静态条件下参与
    - 哪些候选分支在编译时必须恰好一个条件成立

    它不表达的是：
    - 图挂载后的结构身份
    - 这次执行的 results / skip 状态
    - Execution 的展示与 composite 记录模式
    """

    steps: tuple[Step, ...] = field(default_factory=tuple)
    edges: tuple[GraphEdge, ...] = field(default_factory=tuple)
    interface: GraphInterface = field(default_factory=GraphInterface)
    observation: GraphObservationMessages = field(
        default_factory=GraphObservationMessages
    )
    static: tuple[StaticConditions, ...] = ()
    static_selections: tuple[StaticSelection, ...] = ()
    _selection_bindings: Mapping[Step, tuple[StaticSelection, Case]] = field(
        init=False, repr=False, compare=False
    )
    _static_bindings: Mapping[StaticTarget, BoundStaticCondition] = field(
        init=False, repr=False, compare=False
    )

    def __init__(
        self,
        steps: tuple[Step, ...] = (),
        edges: tuple[GraphEdge, ...] = (),
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> None:
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(
            self,
            "interface",
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
        object.__setattr__(self, "static_selections", static_selections)
        # 完整声明建好后直接调用校验器，不再增加一层转发方法。
        from .validation import validate_graph

        validate_graph(self)
        object.__setattr__(
            self,
            "_selection_bindings",
            resolve_selection_bindings(self.static_selections, steps=self.steps),
        )
        object.__setattr__(
            self,
            "_static_bindings",
            resolve_static_bindings(
                self.static,
                steps=self.steps,
                edge_pairs={
                    *((edge.source, edge.target) for edge in self.edges),
                    *(
                        (source, target)
                        for target, condition in _condition_uses(self.steps, self.edges)
                        for source in condition.inputs
                    ),
                }
                if self.static
                else set(),
            ),
        )

    @classmethod
    def from_wiring(
        cls,
        wiring: GraphWiringInput,
        *,
        interface: GraphInterfaceInput | None = None,
        observation: GraphObservationMessages | None = None,
        static: StaticConditionsInput = (),
        static_selections: tuple[StaticSelection, ...] = (),
    ) -> Graph:
        normalized = (
            wiring
            if isinstance(wiring, GraphWiring)
            else _graph_wiring_from_step_wirings(wiring)
        )
        return cls(
            steps=normalized.steps,
            edges=normalized.edges,
            interface=interface,
            observation=(
                GraphObservationMessages() if observation is None else observation
            ),
            static=(*normalized.static, *static_groups(static)),
            static_selections=(*normalized.static_selections, *static_selections),
        )

    def _resolve_static_selections(self) -> tuple[Step, ...]:
        """Return inactive cases once per active graph mount during compilation."""

        # 全部分支扫描两次：时间 O(C)，返回的未选节点占 O(C) 空间。
        inactive: list[Step] = []
        for declaration in self.static_selections:
            selected = declaration.selected_step()
            inactive.extend(
                case.step for case in declaration.cases if case.step is not selected
            )
        return tuple(inactive)

    def step(
        self,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> Step:
        """把当前图包装成一个可挂载 step。"""

        from .step import Step

        return Step(
            self,
            label=label,
            condition=condition,
        )

    def incoming_edges(self, step: Step) -> tuple[GraphEdge, ...]:
        return tuple(
            edge
            for edge in self.edges
            if edge.target is step
            and not (isinstance(edge, Edge) and edge.ports is not None)
        )
