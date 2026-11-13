"""Leaf behavior and callable invocation contracts."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from ..errors import GraphError
from .metadata import _NodeMeta

if TYPE_CHECKING:
    from ..option import Option
    from ..results import NodeRunContext
    from . import ConditionInput, GraphWiringOperand, NodePrerequisite
    from .condition import _RuntimeCondition
    from .identity import NodeId
    from .wiring import GraphWiring, NodeWiring, Results


type LeafCallable = Callable[..., object]


@dataclass(frozen=True, slots=True)
class _NoArgsCall:
    fn: Callable[[], object]


@dataclass(frozen=True, slots=True)
class _StepContextCall:
    fn: Callable[[NodeRunContext], object]


@dataclass(frozen=True, slots=True)
class _ResultCall:
    fn: LeafCallable


type _LeafCall = _NoArgsCall | _StepContextCall | _ResultCall


def _accepts_no_args(
    _fn: LeafCallable,
    signature: inspect.Signature,
) -> TypeGuard[Callable[[], object]]:
    try:
        signature.bind()
    except TypeError:
        return False
    return True


def _accepts_step_context(
    _fn: LeafCallable,
    signature: inspect.Signature,
) -> TypeGuard[Callable[[NodeRunContext], object]]:
    try:
        signature.bind(object())
    except TypeError:
        return False
    return True


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Leaf:
    """封装 action；数据参数由图的结果边显式绑定。

    无数据输入时调用 fn() 或 fn(ctx)，两者都可调用时固定选择零参，
    保留闭包默认参数。有输入时固定调用 fn(ctx, *inputs)，输入均为 Option。
    完整签名在图接线后校验，不按参数名或类型标注猜测调用方式。
    """

    _call: _LeafCall
    _meta: _NodeMeta

    def __init__(
        self,
        action: LeafCallable,
        *,
        label: str | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        fn = action
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError) as exc:
            raise GraphError(
                f"step action has no inspectable signature: {fn!r}: {exc}"
            ) from exc

        if _accepts_no_args(fn, signature):
            # 同时支持零参和单参的 callable 固定按零参调用，避免把
            # `lambda value=value: ...` 的闭包默认值误覆盖成运行上下文。
            call: _LeafCall = _NoArgsCall(fn=fn)
        elif _accepts_step_context(fn, signature):
            call = _StepContextCall(fn=fn)
        else:
            # 输入数量属于图的接线，在构图时校验完整调用签名。
            call = _ResultCall(fn)

        object.__setattr__(self, "_call", call)
        object.__setattr__(self, "_meta", _NodeMeta.create(label, condition))

    @property
    def id(self) -> NodeId:
        return self._meta.id

    @property
    def label(self) -> str | None:
        return self._meta.label

    @property
    def condition(self) -> _RuntimeCondition | None:
        return self._meta.condition

    @property
    def action(self) -> LeafCallable:
        return self._call.fn

    def wire(self, after: tuple[NodePrerequisite | Results, ...] = ()) -> NodeWiring:
        from .wiring import NodeWiring

        return NodeWiring(self, after)

    def __rshift__(self, other: GraphWiringOperand) -> GraphWiring:
        from .wiring import _sequence_wiring

        return _sequence_wiring(self, other)

    def __or__(self, other: GraphWiringOperand) -> GraphWiring:
        from .wiring import _parallel_wiring

        return _parallel_wiring(self, other)

    def validate_inputs(self, count: int) -> None:
        try:
            if count:
                inspect.signature(self.action).bind(object(), *([object()] * count))
            elif isinstance(self._call, _ResultCall):
                raise TypeError("action requires result inputs")
        except TypeError as exc:
            raise GraphError(
                f"action signature does not match {count} result inputs: {exc}"
            ) from exc

    def invoke(
        self, run_ctx: NodeRunContext, inputs: tuple[Option[object], ...] = ()
    ) -> object:
        if inputs:
            return self.action(run_ctx, *inputs)
        match self._call:
            case _NoArgsCall(fn=fn):
                return fn()
            case _StepContextCall(fn=fn):
                return fn(run_ctx)
            case _ResultCall():
                raise GraphError("action requires result inputs")


def leaf(action: LeafCallable) -> Leaf:
    """Declare a leaf, using a named callable's name as its presentation label."""

    name = getattr(action, "__name__", None)
    label = name if isinstance(name, str) and name != "<lambda>" else None
    return Leaf(action, label=label)
