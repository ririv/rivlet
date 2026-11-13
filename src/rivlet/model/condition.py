"""Runtime predicate declarations and ordered source normalization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GraphError

if TYPE_CHECKING:
    from . import ConditionInput
    from .node import Node


# 参数按声明顺序接收 Option[object]，不按类型标注猜测调用约定。
type ConditionalEdgePredicate = Callable[..., bool]


@dataclass(frozen=True, slots=True, eq=False)
class _RuntimeCondition:
    """Normalized predicate and its ordered, graph-local result sources."""

    when: ConditionalEdgePredicate
    inputs: tuple[Node, ...]

    def as_input(self) -> ConditionInput:
        if not self.inputs:
            return self.when
        return (self.when, self.inputs[0], *self.inputs[1:])


def _normalize_condition(condition: ConditionInput) -> _RuntimeCondition:
    if callable(condition):
        return _RuntimeCondition(when=condition, inputs=())
    if not isinstance(condition, tuple) or len(condition) < 2:
        raise GraphError("condition must be a predicate or (predicate, step, ...)")
    from .graph import Graph
    from .step import Step

    predicate, *inputs = condition
    if not callable(predicate) or any(
        not isinstance(source, (Step, Graph)) for source in inputs
    ):
        raise GraphError("condition must be a predicate or (predicate, step, ...)")
    return _RuntimeCondition(when=predicate, inputs=tuple(inputs))
