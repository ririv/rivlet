"""Compiler-owned provenance, not a second graph representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import NodePath


@dataclass(frozen=True, slots=True)
class NodeConditionOrigin:
    target: NodePath


@dataclass(frozen=True, slots=True)
class EdgeConditionOrigin:
    target: NodePath
    edge_index: int


@dataclass(frozen=True, slots=True)
class CompilationOrigins:
    parent_node_path: NodePath | None
    disabled_edges: tuple[tuple[NodePath | None, int], ...]
    conditions: tuple[NodeConditionOrigin | EdgeConditionOrigin, ...]
