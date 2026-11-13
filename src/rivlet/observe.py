from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .model import GraphStructure, Node, NodeLabelPath, NodePath
    from .option import Some
    from .results import ResultView


class GraphObservationPhase(StrEnum):
    BEFORE = "before"
    AFTER_SUCCESS = "after_success"


@dataclass(frozen=True, slots=True)
class GraphObservationEvent:
    phase: GraphObservationPhase
    message: str
    source_graph: GraphStructure
    graph_label: str
    graph_label_path: NodeLabelPath
    graph_node_paths: Mapping[Node, NodePath]
    results: ResultView | None = None

    def result(self, step: Node) -> Some[object] | None:
        """按声明 step 引用读取当前观察图的实时结果。"""

        node_path = self.graph_node_paths.get(step)
        if node_path is None:
            raise RuntimeError(f"step '{step.label}' is not declared in observed graph")
        if self.results is None:
            return None
        return self.results.get(node_path)


class GraphObserver(Protocol):
    def emit(self, event: GraphObservationEvent) -> None: ...
