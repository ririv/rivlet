"""Steps, mounted identities and action normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .._native import NativeGraph
from ..errors import GraphError
from .condition import _normalize_condition, _RuntimeCondition
from .graph import Graph
from .identity import StepId, StepPath
from .leaf import Leaf
from .wiring import (
    GraphWiring,
    Results,
    StepWiring,
    _parallel_wiring,
    _sequence_wiring,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from . import (
        ConditionInput,
        GraphWiringOperand,
        Node,
        NodeInput,
        StepPrerequisite,
    )


def step_paths_for_mount(
    graph: Graph,
    *,
    parent_step_path: StepPath | None = None,
) -> dict[Step, StepPath]:
    """为一张图在当前挂载位置分配内部 step 身份。"""

    return {
        step: (
            StepPath(
                (index,), labels=(str(index) if step.label is None else step.label,)
            )
            if parent_step_path is None
            else parent_step_path.child(index, label=step.label)
        )
        for index, step in enumerate(graph.steps)
    }


type StepLabelPath = tuple[str, ...]


@dataclass(eq=False, frozen=True, slots=True, init=False)
class Step:
    """One declared node with identity, presentation and activation metadata."""

    id: StepId
    label: str | None
    node: Node
    # `condition` 控制的是 step 本身是否参与这次图执行，而不是依赖边是否成立。
    # 约定：
    # 运行时求值为 False 时，该 step 进入 Skipped 终态。
    # 这条语义对 leaf step 和 graph-action step 完全一致。
    condition: _RuntimeCondition | None = None

    def __init__(
        self,
        node: NodeInput,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        if label == "":
            raise GraphError("step label must not be empty")

        normalized_node: Node
        if isinstance(node, (Leaf, Graph, NativeGraph)):
            normalized_node = node
        elif callable(node):
            normalized_node = Leaf(action=node)
        else:
            raise GraphError(
                f"step '{label}' has unsupported node type: {type(node)!r}"
            )

        object.__setattr__(self, "id", StepId())
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "node", normalized_node)
        object.__setattr__(
            self,
            "condition",
            None if condition is None else _normalize_condition(condition),
        )

    def wire(
        self,
        after: tuple[StepPrerequisite | Results, ...] = (),
    ) -> StepWiring:
        """为当前 step 创建一份新的图接线，不修改 step 本身。"""

        return StepWiring(step=self, after=after)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        return _parallel_wiring(self, other)


def resolve_step_label(step: Step, step_path: StepPath) -> str:
    """解析 step 在一次具体挂载中的展示名称。"""

    return str(step_path) if step.label is None else step.label


def step(action: NodeInput) -> Step:
    """把 action 声明为可组合 step；可用时以 action 自身名称作为 label。"""

    match action:
        case Graph() | NativeGraph():
            label = None
        case Leaf():
            label = _callable_step_label(action.action)
        case _:
            label = _callable_step_label(action)
    return Step(label=label, node=action)


def _callable_step_label(action: Callable[..., object]) -> str | None:
    label = getattr(action, "__name__", None)
    if not isinstance(label, str) or not label or label == "<lambda>":
        return None
    return label
