from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .model import Graph, Step, StepLabelPath, StepPath
    from .option import Some
    from .results import GraphResultView


class GraphObservationPhase(StrEnum):
    BEFORE = "before"
    AFTER_SUCCESS = "after_success"


@dataclass(frozen=True, slots=True)
class GraphObservationEvent:
    phase: GraphObservationPhase
    message: str
    source_graph: Graph
    graph_label: str
    graph_label_path: StepLabelPath
    graph_step_paths: Mapping[Step, StepPath]
    results: GraphResultView | None = None

    def result(self, step: Step) -> Some[object] | None:
        """按声明 step 引用读取当前观察图的实时结果。"""

        step_path = self.graph_step_paths.get(step)
        if step_path is None:
            raise RuntimeError(f"step '{step.label}' is not declared in observed graph")
        if self.results is None:
            return None
        return self.results.get(step_path)


class GraphObserver(Protocol):
    def emit(self, event: GraphObservationEvent) -> None: ...
