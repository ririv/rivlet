"""Compiler-owned provenance, not a second graph representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import StepId


@dataclass(frozen=True, slots=True)
class NodeConditionOrigin:
    target: StepId


@dataclass(frozen=True, slots=True)
class EdgeConditionOrigin:
    target: StepId
    edge_index: int


@dataclass(frozen=True, slots=True)
class CompilationOrigins:
    parent_step_id: StepId | None
    disabled_edges: tuple[tuple[StepId | None, int], ...]
    conditions: tuple[NodeConditionOrigin | EdgeConditionOrigin, ...]
