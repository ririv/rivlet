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


@dataclass(frozen=True, order=True, slots=True)
class StepId:
    """step 在一次具体图挂载中的内部身份。"""

    segments: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise GraphError("step id must contain at least one segment")
        if any(segment < 0 for segment in self.segments):
            raise GraphError(f"step id segments must be non-negative: {self.segments}")

    def child(self, index: int) -> StepId:
        if index < 0:
            raise GraphError(f"step child index must be non-negative: {index}")
        return StepId((*self.segments, index))

    def __str__(self) -> str:
        return ".".join(str(segment) for segment in self.segments)

    @property
    def parent(self) -> StepId | None:
        if len(self.segments) == 1:
            return None
        return StepId(self.segments[:-1])


def step_ids_for_mount(
    graph: Graph,
    *,
    parent_step_id: StepId | None = None,
) -> dict[Step, StepId]:
    """为一张图在当前挂载位置分配内部 step 身份。"""

    return {
        step: (
            StepId((index,)) if parent_step_id is None else parent_step_id.child(index)
        )
        for index, step in enumerate(graph.steps)
    }


type StepLabelPath = tuple[str, ...]


@dataclass(eq=False, frozen=True, slots=True, init=False)
class Step:
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


def resolve_step_label(step: Step, step_id: StepId) -> str:
    """解析 step 在一次具体挂载中的展示名称。"""

    return str(step_id) if step.label is None else step.label


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
