"""Execution edges, result edges and conditional prerequisites."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..interface import _validate_port_name
from .condition import _normalize_condition, _RuntimeCondition

if TYPE_CHECKING:
    from collections.abc import Iterator

    from . import ConditionInput, ResultReceiver, StepPrerequisite
    from .step import Step


@dataclass(frozen=True, slots=True, init=False)
class ConditionalPrerequisite:
    step: Step
    condition: _RuntimeCondition

    def __init__(self, step: Step, *, condition: ConditionInput) -> None:
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "condition", _normalize_condition(condition))


def prerequisite_step(dep: StepPrerequisite) -> Step:
    if isinstance(dep, ConditionalPrerequisite):
        return dep.step
    return dep


def _condition_uses(
    steps: tuple[Step, ...], edges: tuple[GraphEdge, ...]
) -> Iterator[tuple[Step, _RuntimeCondition]]:
    for step in steps:
        if step.condition is not None:
            yield step, step.condition
    for edge in edges:
        if isinstance(edge, Edge) and edge.condition is not None:
            yield edge.target, edge.condition


@dataclass(frozen=True, slots=True, init=False)
class Edge:
    """An execution dependency with independent port routing and condition."""

    source: Step
    target: Step
    ports: tuple[str, str] | None
    condition: _RuntimeCondition | None

    def __init__(
        self,
        source: Step,
        target: Step,
        *,
        ports: str | tuple[str, str] | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        names = (ports, ports) if isinstance(ports, str) else ports
        if names is not None:
            output_name, input_name = names
            _validate_port_name(output_name)
            _validate_port_name(input_name)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "ports", names)
        object.__setattr__(
            self,
            "condition",
            None if condition is None else _normalize_condition(condition),
        )


@dataclass(frozen=True, slots=True)
class ResultEdge:
    """将来源结果绑定到目标步骤中指定声明对象的位置参数。"""

    source: Step
    target: Step
    argument_index: int
    receiver: ResultReceiver


type GraphEdge = Edge | ResultEdge
