# Failure events

English | [简体中文](./zh-CN/failure_events.md)

Rivlet reports a direct reason that actually made a node fail or become blocked.
It does not traverse the declaration after execution to guess causes. Python and
native actions, port edges, static subgraphs, and returned graphs share
this model.

## Event model

`NodeExecutionEvent` contains declaration `node_id`, mounted `node_path`, and `state_detail`.
Display labels are available through `node_path.labels`; repeated mounts share
their declaration ID but not their event location.
The read-only `state` property is derived from `state_detail`, not stored as a
second independent fact. It describes the state recorded by this event, not a
live snapshot of the step. `NodeExecutionStateDetail` is the union of lifecycle
states and typed failure/blockage causes.

| State detail | State | Meaning |
| --- | --- | --- |
| `NodeExecutionState.STARTED` | STARTED | The node started |
| `NodeExecutionState.COMPLETED` | COMPLETED | The node completed |
| `NodeExecutionState.SKIPPED` | SKIPPED | The node was skipped |
| `ActionFailed()` | FAILED | The node's own Python or native action failed |
| `SubgraphFailed(source)` | FAILED | An actual child failed or was blocked |
| `BlockedByDependency(source)` | BLOCKED | A required predecessor failed or was blocked |
| `BlockedByCondition(condition)` | BLOCKED | A runtime predicate raised an error |
| `BlockedByConditionInput(condition, source)` | BLOCKED | An input failed, so the predicate did not run |
| `BlockedByParent(source)` | BLOCKED | The enclosing subgraph could not be entered |

`condition` is either `NodeConditionSite(condition_index)` for the node's guard or
`EdgeConditionSite(condition_index, prerequisite)` for an incoming edge's guard.
The index belongs to the current execution session, not a durable predicate ID.
The event's mounted `node_path` identifies its consumer. Every related `source` and
`prerequisite` is also a mounted `NodePath`; repeated mounts remain independent.

```python
from rivlet import (
    ActionFailed,
    BlockedByCondition,
    BlockedByConditionInput,
    BlockedByDependency,
    BlockedByParent,
    Execution,
    Graph,
    NodeExecutionEvent,
    SubgraphFailed,
    run,
)


class Events:
    def emit(self, event: NodeExecutionEvent) -> None:
        match event.state_detail:
            case ActionFailed():
                print(event.node_path, "action failed")
            case SubgraphFailed(source=source):
                print(event.node_path, "child failed:", source)
            case BlockedByDependency(source=source):
                print(event.node_path, "blocked by predecessor:", source)
            case BlockedByCondition(condition=site):
                print(event.node_path, "predicate failed:", site)
            case BlockedByConditionInput(condition=site, source=source):
                print(event.node_path, "predicate input failed:", site, source)
            case BlockedByParent(source=source):
                print(event.node_path, "parent blocked:", source)
            case _:
                print(event.node_path, event.state)


run(Execution(Graph(structure=(graph).structure), events=Events()))
```

## Direct witnesses

For `A → B → C`, A's action failure produces `ActionFailed` for A,
`BlockedByDependency(A)` for B, and `BlockedByDependency(B)` for C. The whole chain
is not copied into each event. An unentered subgraph passes its own blocked
identity to descendants. A failed dynamic session links an actual failed/blocked
child back to its parent session's step.

When multiple causes suffice, the runtime records one witness observed at the
transition. This is neither every root cause nor the earliest wall-clock failure.
A false conditional edge does not propagate its source's failure. A port
provider failing after its ready output cannot retroactively block an already
released consumer.

An added cause link costs O(1) per failed/blocked transition. Subgraph aggregation
reuses the existing direct-child scan. Records and transport are linear in emitted
transitions rather than all transitive failure paths. The successful path gains no
whole-graph cause analysis.

## Delivery boundaries

Events describe committed runtime states. Callbacks are synchronous and serialized
within one execution, but not bound to the coordinator thread. Keep them short.
A host needing a dedicated persistence/UI thread can enqueue events and explicitly
drain its queue after execution. Rivlet does not create an implicit event thread.
A callback error still stops execution as `EventDeliveryError`.

Without a subscription, public events and their label index are not constructed.
No sequence numbers, timestamps, global run IDs, persistence, retries, or checkpoint
recovery are introduced. Compilation errors, including static-condition evaluation
errors before execution, are not step runtime events.

Cause objects contain no exception text, traceback, arguments, callable, or result.
Normal execution exceptions still propagate to the caller; existing logging is
unchanged. Hosts remain responsible for labels and the data they persist.

## Construction compatibility

Compared with 0.1.0, subscribers use `event.state` instead of `event.phase`, and
`NodeExecutionState` replaces `StepExecutionPhase`. Manual construction uses
`state_detail=` instead of the old `phase=` field. Failed and blocked states
require their typed details, not an optional cause that could contradict a state.
There are no aliases for the old names. This changes the reading API, construction,
and dataclass serialization,
so publication requires a minor-version release under the pre-1.0 version policy.
It must not overwrite the published 0.1.0 files.
