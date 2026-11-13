"""Execution dependencies and ordered result connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GraphError
from ..interface import _validate_port_name
from .action import LeafAction
from .condition import _normalize_condition, _RuntimeCondition

if TYPE_CHECKING:
    from collections.abc import Iterator

    from . import ConditionInput, StepPrerequisite
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
    """An execution dependency, optionally carrying an action result."""

    source: Step
    target: Step
    _connection: _ExecutionConnection | _ResultConnection

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
        object.__setattr__(
            self,
            "_connection",
            _ExecutionConnection(
                names, None if condition is None else _normalize_condition(condition)
            ),
        )

    @staticmethod
    def results(sources: tuple[Step, ...], target: Step) -> tuple[Edge, ...]:
        """Connect local producer results to the target action in source order."""

        if not isinstance(target.action, LeafAction):
            raise GraphError("result target must have a callable action")
        if not sources:
            raise GraphError("result connections require at least one source")
        # O(S) 时间/空间生成连续参数位置。共享组身份与长度，使图构造能发现
        # 切片丢边或多组绑定，不允许这些操作悄悄改变 action 的参数含义。
        group = _ResultGroup(len(sources))
        edges: list[Edge] = []
        for index, source in enumerate(sources):
            edge = object.__new__(Edge)
            object.__setattr__(edge, "source", source)
            object.__setattr__(edge, "target", target)
            object.__setattr__(edge, "_connection", _ResultConnection(group, index))
            edges.append(edge)
        return tuple(edges)

    @property
    def ports(self) -> tuple[str, str] | None:
        match self._connection:
            case _ExecutionConnection(ports=ports):
                return ports
            case _ResultConnection():
                return None

    @property
    def condition(self) -> _RuntimeCondition | None:
        match self._connection:
            case _ExecutionConnection(condition=condition):
                return condition
            case _ResultConnection():
                return None

    @property
    def _result_index(self) -> int | None:
        match self._connection:
            case _ResultConnection(index=index):
                return index
            case _ExecutionConnection():
                return None


@dataclass(frozen=True, slots=True)
class _ExecutionConnection:
    ports: tuple[str, str] | None
    condition: _RuntimeCondition | None


@dataclass(eq=False, frozen=True, slots=True)
class _ResultGroup:
    size: int


@dataclass(frozen=True, slots=True)
class _ResultConnection:
    group: _ResultGroup
    index: int


type GraphEdge = Edge
