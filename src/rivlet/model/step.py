"""Steps, mounted identities and action normalization."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .._native import NativeGraph
from ..errors import GraphError
from .action import LeafAction
from .condition import _normalize_condition, _RuntimeCondition
from .graph import Graph
from .identity import StepId, StepPath
from .wiring import (
    GraphWiring,
    StepWiring,
    _parallel_wiring,
    _sequence_wiring,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from . import (
        ConditionInput,
        GraphWiringOperand,
        ResultBindings,
        StepAction,
        StepActionInput,
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
    id: StepId
    label: str | None
    action: StepAction
    # `condition` 控制的是 step 本身是否参与这次图执行，而不是依赖边是否成立。
    # 约定：
    # 运行时求值为 False 时，该 step 进入 Skipped 终态。
    # 这条语义对 leaf step 和 graph-action step 完全一致。
    condition: _RuntimeCondition | None = None

    def __init__(
        self,
        action: StepActionInput,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        if label == "":
            raise GraphError("step label must not be empty")

        normalized_action: StepAction
        if isinstance(action, (LeafAction, Graph, NativeGraph)):
            normalized_action = action
        elif callable(action):
            normalized_action = LeafAction(fn=action)
        else:
            raise GraphError(
                f"step '{label}' has unsupported action type: {type(action)!r}"
            )

        object.__setattr__(self, "id", StepId())
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "action", normalized_action)
        object.__setattr__(
            self,
            "condition",
            None if condition is None else _normalize_condition(condition),
        )

    def wire(
        self,
        after: tuple[StepPrerequisite, ...] = (),
        *,
        inputs: ResultBindings = MappingProxyType({}),
    ) -> StepWiring:
        """为当前 step 创建一份新的图接线，不修改 step 本身。"""

        return StepWiring(step=self, after=after, inputs=inputs)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        return _parallel_wiring(self, other)


def resolve_step_label(step: Step, step_path: StepPath) -> str:
    """解析 step 在一次具体挂载中的展示名称。"""

    return str(step_path) if step.label is None else step.label


def step(action: StepActionInput) -> Step:
    """把 action 声明为可组合 step；可用时以 action 自身名称作为 label。"""

    match action:
        case Graph():
            label = action.label
        case LeafAction():
            label = _callable_step_label(action.fn)
        case NativeGraph():
            label = action.label
        case _:
            label = _callable_step_label(action)
    return Step(label=label, action=action)


def _callable_step_label(action: Callable[..., object]) -> str | None:
    label = getattr(action, "__name__", None)
    if not isinstance(label, str) or not label or label == "<lambda>":
        return None
    return label
