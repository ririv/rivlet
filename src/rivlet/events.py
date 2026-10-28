from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, assert_never

if TYPE_CHECKING:
    from .model import StepId, StepLabelPath


class StepExecutionState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ActionFailed:
    """The node's own Python or native action failed."""


@dataclass(frozen=True, slots=True)
class SubgraphFailed:
    """An actual child failure prevented this subgraph from completing."""

    source: StepId


@dataclass(frozen=True, slots=True)
class BlockedByDependency:
    """One required predecessor failed or was blocked."""

    source: StepId


@dataclass(frozen=True, slots=True)
class NodeConditionSite:
    """The runtime guard of the event's own mounted node."""

    # 所属执行 session 内的条件索引；与事件的挂载 step_id 一起定位，不用于跨运行恢复。
    condition_index: int


@dataclass(frozen=True, slots=True)
class EdgeConditionSite:
    """A runtime guard on an edge entering the event's node."""

    condition_index: int
    prerequisite: StepId


type ConditionSite = NodeConditionSite | EdgeConditionSite


@dataclass(frozen=True, slots=True)
class BlockedByCondition:
    """A requested runtime predicate raised an error."""

    condition: ConditionSite


@dataclass(frozen=True, slots=True)
class BlockedByConditionInput:
    """A predicate could not run because one of its inputs failed."""

    condition: ConditionSite
    source: StepId


@dataclass(frozen=True, slots=True)
class BlockedByParent:
    """An unentered parent subgraph was blocked."""

    source: StepId


type StepFailureCause = ActionFailed | SubgraphFailed
type StepBlockCause = (
    BlockedByDependency | BlockedByCondition | BlockedByConditionInput | BlockedByParent
)
type StepLifecycleState = Literal[
    StepExecutionState.STARTED, StepExecutionState.COMPLETED, StepExecutionState.SKIPPED
]
type StepExecutionStateDetail = StepLifecycleState | StepFailureCause | StepBlockCause


@dataclass(frozen=True, slots=True)
class StepExecutionEvent:
    """One committed step execution state with its typed details."""

    step_id: StepId
    label_path: StepLabelPath
    state_detail: StepExecutionStateDetail

    @property
    def state(self) -> StepExecutionState:
        """Derive this event's state, not a live snapshot of the step."""
        match self.state_detail:
            case (
                StepExecutionState.STARTED
                | StepExecutionState.COMPLETED
                | StepExecutionState.SKIPPED
            ):
                return self.state_detail
            case ActionFailed() | SubgraphFailed():
                return StepExecutionState.FAILED
            case (
                BlockedByDependency()
                | BlockedByCondition()
                | BlockedByConditionInput()
                | BlockedByParent()
            ):
                return StepExecutionState.BLOCKED
            case _:
                assert_never(self.state_detail)


class StepEventSink(Protocol):
    def emit(self, event: StepExecutionEvent) -> None: ...


class GraphEventDeliveryError(RuntimeError):
    """An event consumer failed after a runtime transition was committed."""
