"""Compiler-owned provenance, not a second graph representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import StepPath


@dataclass(frozen=True, slots=True)
class NodeConditionOrigin:
    target: StepPath


@dataclass(frozen=True, slots=True)
class EdgeConditionOrigin:
    target: StepPath
    edge_index: int


@dataclass(frozen=True, slots=True)
class CompilationOrigins:
    parent_step_path: StepPath | None
    disabled_edges: tuple[tuple[StepPath | None, int], ...]
    conditions: tuple[NodeConditionOrigin | EdgeConditionOrigin, ...]
