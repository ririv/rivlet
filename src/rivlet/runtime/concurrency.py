"""Execution-only bindings from mounted nodes to shared admission scopes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..compile import CompiledPythonStep

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..admission import Limit, Source
    from ..compile import GraphCompilation
    from ..model import NodePath


@dataclass(frozen=True, slots=True)
class SessionLimits:
    maxima: tuple[int, ...]
    node_scopes: tuple[tuple[int, ...], ...]
    sources: dict[int, Source[object]]
    action_nodes: tuple[int, ...]

    @classmethod
    def resolve(
        cls,
        compilation: GraphCompilation,
        requested: Mapping[NodePath, Limit],
    ) -> SessionLimits | None:
        if not requested:
            return None
        indexes = {path: index for index, path in enumerate(requested)}
        sources = {
            index: limit.source
            for index, limit in enumerate(requested.values())
            if limit.source is not None
        }
        # V 为编译节点数，D 为节点祖先深度。O(V * D) 建立一次索引；
        # 只存受限祖先，不为每个限制分别扫描整图。无配置时不建立侧表。
        return cls(
            tuple(limit.maximum for limit in requested.values()),
            tuple(
                tuple(
                    indexes[path]
                    for path in (*node.context.ancestor_composite_ids, node.path)
                    if path in indexes
                )
                for node in compilation._execution_nodes
            ),
            sources,
            tuple(
                index
                for index, node in enumerate(compilation._execution_nodes)
                if isinstance(node, CompiledPythonStep)
            )
            if sources
            else (),
        )
