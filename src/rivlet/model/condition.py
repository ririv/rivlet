"""Runtime predicate declarations and ordered source normalization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .._native import _NativeCondition
from ..errors import GraphError

if TYPE_CHECKING:
    from ..interface import OutputEndpoint
    from . import ConditionInput
    from .node import Node


# 参数按声明顺序接收 Option[object]，不按类型标注猜测调用约定。
type ConditionalEdgePredicate = Callable[..., bool | Awaitable[bool]]


@dataclass(frozen=True, slots=True, eq=False)
class _BoundNativeCondition:
    """Experimental: a Rust predicate with explicit, GC-visible source references."""

    _native: _NativeCondition
    inputs: tuple[OutputEndpoint, ...]

    @property
    def when(self) -> _NativeCondition:
        return self._native

    def as_input(self) -> _BoundNativeCondition:
        return self


def _bind_native_condition(
    condition: _NativeCondition | _RuntimeCondition | _BoundNativeCondition | None,
) -> _RuntimeCondition | _BoundNativeCondition | None:
    if isinstance(condition, _NativeCondition):
        return _BoundNativeCondition(condition, ())
    return condition


@dataclass(frozen=True, slots=True, eq=False)
class _RuntimeCondition:
    """Normalized predicate and its ordered, graph-local result sources."""

    when: ConditionalEdgePredicate
    inputs: tuple[Node, ...]

    def as_input(self) -> ConditionInput:
        if not self.inputs:
            return self.when
        return (self.when, self.inputs[0], *self.inputs[1:])


def _normalize_condition(
    condition: ConditionInput,
) -> _RuntimeCondition | _BoundNativeCondition:
    if isinstance(condition, _BoundNativeCondition):
        return condition
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
