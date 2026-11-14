"""Execution-only bindings from mounted nodes to shared admission scopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..compile import GraphCompilation
    from ..model import NodePath


@dataclass(frozen=True, slots=True)
class SessionLimits:
    maxima: tuple[int, ...]
    node_scopes: tuple[tuple[int, ...], ...]

    @classmethod
    def resolve(
        cls,
        compilation: GraphCompilation,
        requested: Mapping[NodePath, int],
    ) -> SessionLimits | None:
        if not requested:
            return None
        indexes = {path: index for index, path in enumerate(requested)}
        # V 为编译节点数，D 为节点祖先深度。O(V * D) 建立一次索引；
        # 只存受限祖先，不为每个限制分别扫描整图。无配置时不建立侧表。
        return cls(
            tuple(requested.values()),
            tuple(
                tuple(
                    indexes[path]
                    for path in (*node.context.ancestor_composite_ids, node.path)
                    if path in indexes
                )
                for node in compilation._execution_nodes
            ),
        )
