"""Execution dependencies and ordered result connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, overload

from ..errors import GraphError
from ..interface import InputPort, OutputEndpoint, OutputPort, _validate_port_name
from .condition import _normalize_condition, _RuntimeCondition
from .leaf import Leaf

if TYPE_CHECKING:
    from collections.abc import Iterator

    from . import ConditionInput, NodePrerequisite
    from .graph import Graph
    from .node import Node


@dataclass(frozen=True, slots=True, init=False)
class ConditionalPrerequisite:
    node: Node
    condition: _RuntimeCondition

    def __init__(self, node: Node, *, condition: ConditionInput) -> None:
        object.__setattr__(self, "node", node)
        object.__setattr__(self, "condition", _normalize_condition(condition))


def prerequisite_node(dep: NodePrerequisite) -> Node:
    if isinstance(dep, ConditionalPrerequisite):
        return dep.node
    return dep


def _condition_uses(
    steps: tuple[Node, ...], edges: tuple[GraphEdge, ...]
) -> Iterator[tuple[Node, _RuntimeCondition]]:
    for step in steps:
        if step.condition is not None:
            yield step, step.condition
    for edge in edges:
        if isinstance(edge, Edge) and edge.condition is not None:
            yield edge.target, edge.condition


@dataclass(frozen=True, slots=True, init=False)
class Edge:
    """An execution dependency, optionally carrying an action result."""

    _source: OutputEndpoint
    _target: Node | InputPort
    _connection: _ExecutionConnection | _ResultConnection

    @overload
    def __init__(
        self, source: Node, target: Node, *, condition: ConditionInput | None = None
    ) -> None: ...

    @overload
    def __init__(
        self,
        source: Graph,
        target: Graph,
        *,
        ports: str | tuple[str, str],
        condition: ConditionInput | None = None,
    ) -> None: ...

    def __init__(
        self,
        source: Node,
        target: Node,
        *,
        ports: str | tuple[str, str] | None = None,
        condition: ConditionInput | None = None,
    ) -> None:
        names = (ports, ports) if isinstance(ports, str) else ports
        if names is not None:
            output_name, input_name = names
            _validate_port_name(output_name)
            _validate_port_name(input_name)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_target", target)
        object.__setattr__(
            self,
            "_connection",
            _ExecutionConnection(
                names, None if condition is None else _normalize_condition(condition)
            ),
        )

    @staticmethod
    def results(
        sources: tuple[OutputEndpoint, ...], target: Leaf | InputPort
    ) -> tuple[Edge, ...]:
        """Connect node or graph-port results to an action in source order."""

        if not isinstance(target, (Leaf, InputPort)):
            raise GraphError("result target must have a callable action")
        if not sources:
            raise GraphError("result connections require at least one source")
        # O(S) 时间/空间生成连续参数位置。共享组身份与长度，使图构造能发现
        # 切片丢边或多组绑定，不允许这些操作悄悄改变 action 的参数含义。
        group = _ResultGroup(len(sources))
        edges: list[Edge] = []
        for index, source in enumerate(sources):
            edge = object.__new__(Edge)
            object.__setattr__(edge, "_source", source)
            object.__setattr__(edge, "_target", target)
            object.__setattr__(edge, "_connection", _ResultConnection(group, index))
            edges.append(edge)
        return tuple(edges)

    @property
    def source(self) -> Node:
        return (
            self._source.graph if isinstance(self._source, OutputPort) else self._source
        )

    @property
    def target(self) -> Node:
        return (
            self._target.graph if isinstance(self._target, InputPort) else self._target
        )

    @property
    def result_source(self) -> OutputEndpoint:
        return self._source

    @property
    def result_target(self) -> Node | InputPort:
        return self._target

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
