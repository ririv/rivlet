"""Common node metadata, composed by concrete node declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GraphError
from .condition import _normalize_condition, _RuntimeCondition
from .identity import NodeId

if TYPE_CHECKING:
    from . import ConditionInput


@dataclass(frozen=True, slots=True)
class _NodeMeta:
    id: NodeId
    label: str | None
    condition: _RuntimeCondition | None

    @classmethod
    def create(cls, label: str | None, condition: ConditionInput | None) -> _NodeMeta:
        if label == "":
            raise GraphError("node label must not be empty")
        return cls(
            NodeId(),
            label,
            None if condition is None else _normalize_condition(condition),
        )
