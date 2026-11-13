from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, assert_never

if TYPE_CHECKING:
    from .model import NodeId, NodePath


class NodeExecutionState(StrEnum):
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

    source: NodePath


@dataclass(frozen=True, slots=True)
class BlockedByDependency:
    """One required predecessor failed or was blocked."""

    source: NodePath


@dataclass(frozen=True, slots=True)
class NodeConditionSite:
    """The runtime guard of the event's own mounted node."""

    # 所属执行 session 内的条件索引；与事件的挂载 node_path 一起定位，不用于跨运行恢复。
    condition_index: int


@dataclass(frozen=True, slots=True)
class EdgeConditionSite:
    """A runtime guard on an edge entering the event's node."""

    condition_index: int
    prerequisite: NodePath


type ConditionSite = NodeConditionSite | EdgeConditionSite


@dataclass(frozen=True, slots=True)
class BlockedByCondition:
    """A requested runtime predicate raised an error."""

    condition: ConditionSite


@dataclass(frozen=True, slots=True)
class BlockedByConditionInput:
    """A predicate could not run because one of its inputs failed."""

    condition: ConditionSite
    source: NodePath


@dataclass(frozen=True, slots=True)
class BlockedByParent:
    """An unentered parent subgraph was blocked."""

    source: NodePath


type NodeFailureCause = ActionFailed | SubgraphFailed
type NodeBlockCause = (
    BlockedByDependency | BlockedByCondition | BlockedByConditionInput | BlockedByParent
)
type StepLifecycleState = Literal[
    NodeExecutionState.STARTED, NodeExecutionState.COMPLETED, NodeExecutionState.SKIPPED
]
type NodeExecutionStateDetail = StepLifecycleState | NodeFailureCause | NodeBlockCause


@dataclass(frozen=True, slots=True)
class NodeExecutionEvent:
    """One committed step execution state with its typed details."""

    node_id: NodeId
    node_path: NodePath
    state_detail: NodeExecutionStateDetail

    @property
    def state(self) -> NodeExecutionState:
        """Derive this event's state, not a live snapshot of the step."""
        match self.state_detail:
            case (
                NodeExecutionState.STARTED
                | NodeExecutionState.COMPLETED
                | NodeExecutionState.SKIPPED
            ):
                return self.state_detail
            case ActionFailed() | SubgraphFailed():
                return NodeExecutionState.FAILED
            case (
                BlockedByDependency()
                | BlockedByCondition()
                | BlockedByConditionInput()
                | BlockedByParent()
            ):
                return NodeExecutionState.BLOCKED
            case _:
                assert_never(self.state_detail)


class NodeEventSink(Protocol):
    def emit(self, event: NodeExecutionEvent) -> None: ...


class EventDeliveryError(RuntimeError):
    """An event consumer failed after a runtime transition was committed."""
