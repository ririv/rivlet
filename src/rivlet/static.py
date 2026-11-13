from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .selection import (
    MultipleStaticBranchesSelectedError,
    NoStaticBranchSelectedError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import GraphWiring, Node, NodeKind


@dataclass(frozen=True, slots=True, init=False)
class Case:
    """Bind one complete static boolean condition to a mounted Node."""

    condition: bool
    node: NodeKind

    def __init__(
        self,
        condition: bool,
        component: NodeKind,
    ) -> None:
        if not isinstance(condition, bool):
            raise TypeError("Case condition must be a bool")
        object.__setattr__(self, "condition", condition)
        object.__setattr__(
            self,
            "node",
            component,
        )


@dataclass(frozen=True, slots=True)
class StaticSelection:
    """Retain every case until compilation checks for exactly one true condition."""

    cases: tuple[Case, ...]

    def selected_node(self) -> Node:
        # C 个分支时间 O(C)、额外空间 O(1)，不把声明顺序当作优先级。
        selected: Node | None = None
        for case in self.cases:
            if not case.condition:
                continue
            if selected is not None:
                raise MultipleStaticBranchesSelectedError(
                    "static selection requires exactly one active case; multiple are active"
                )
            selected = case.node
        if selected is None:
            raise NoStaticBranchSelectedError(
                "static selection requires exactly one active case; none are active"
            )
        return selected


def select(*, cases: Sequence[Case]) -> GraphWiring:
    """Declare an exact-one static selection without choosing or executing a case.

    Each Case carries its complete boolean condition. Python evaluates condition
    expressions before the call; compilation checks for exactly one active case.
    """

    from .model import GraphWiring

    declaration = StaticSelection(cases=tuple(cases))
    return GraphWiring(
        nodes=tuple(case.node for case in declaration.cases),
        static_selections=(declaration,),
    )


def resolve_selection_bindings(
    selections: tuple[StaticSelection, ...], *, nodes: tuple[Node, ...]
) -> Mapping[Node, tuple[StaticSelection, Case]]:
    from .errors import GraphError

    # 索引与成员校验 O(V + C)，不求值、不选择，也不重复扫描每个节点的分支。
    bindings: dict[Node, tuple[StaticSelection, Case]] = {}
    if selections:
        known = set(nodes)
        for declaration in selections:
            for case in declaration.cases:
                if case.node not in known:
                    raise GraphError(
                        "static selection references a step outside this graph"
                    )
                if case.node in bindings:
                    raise GraphError(
                        "static selection is already declared for this step"
                    )
                bindings[case.node] = (declaration, case)
    return MappingProxyType(bindings)


__all__ = ["Case", "StaticSelection", "select"]
