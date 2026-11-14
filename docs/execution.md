# Execution semantics

English | [简体中文](./zh-CN/execution.md)

## Presentation modes

Both public modes use the same Rust declaration compiler and execution session:

- `run(execution, mode="dynamic")` retains compatibility presentation records:
  composites also appear in `executed_steps/completed_steps`.
- `run(execution, mode="compiled")` records composites through their dedicated
  entry/completion state. `Execution.compile()` exposes the compiled structure.

str bindings use the same lowering in both modes. The runtime consumes
GraphCompilation, not a second Python scheduling model. Runtime-returned graphs
enter the same compilation pipeline with their parent NodePath, label path, and
ancestor result scopes.

## Compilation preparation

Execution binds its declaration and host references immutably. Its first
successful `compile()`, `run()`, or `arun()` prepares and fixes one root compilation;
later calls reuse that same object. `execution.compilation` is None before successful
preparation and exposes that object afterward. Compilation errors commit nothing.
Create a new Execution to reevaluate static choices; the standalone compile_graph
tool remains independent. Preparation reuse does not reset execution state or cache
business results. See [structural export](./graph_export.md) to freeze the exact graph.

## Python action return values

The blocking and awaitable entry points use the same action and session pipeline:

- Ordinary values go directly to result recording, without creating an event loop.
- Coroutine actions run on one event loop. A blocking run creates that loop only
  when it encounters an awaitable; subsequent coroutine actions and dynamically
  returned subgraphs reuse it. An awaitable run uses the caller's running loop.
  Synchronous functions returning coroutines and custom awaitables are also
  supported; the action's ContextVar context is preserved.
- A returned Graph executes as a dynamic subgraph before the outer Node completes.

Synchronous Python actions run in the supplied same-interpreter thread pool.
Known coroutine functions do not queue behind blocking thread work. Checking only
`async def` is insufficient: every synchronous return value is also inspected.
No event loop is created for a purely synchronous run, including its failure path.
See the [Node overhead benchmark](./step_overhead_benchmark.md).

## Conditional execution

[Conditional edges](./conditional_edges.md) decide whether dependencies apply;
[conditional steps](./conditional_steps.md) decide whether nodes participate.

In both modes:

- Static conditions are evaluated and removed during compilation.
- Runtime edges become CompiledConditionalDependency values.
- Runtime Node conditions become CompiledRuntimeCondition values.
- A false runtime node condition skips the node; a false edge condition removes
  only that dependency.
- Each compiled condition instance is evaluated at most once per session.
  Multiple instances may reuse the same Python condition object or predicate.
- Conditions inside subgraphs use the same lowering and scheduling rules.

## Rendering

See [render/](../src/rivlet/render/).

`render_graph(...)` and `render_graph_mermaid(...)` accept a Step/Graph node,
GraphStructure, or GraphCompilation and require an explicit inspection `root_label`.
Node labels remain part of the declaration; execution views derive the actual
root node's name rather than introducing a second execution label.

- Dynamic mode renders the declaration structure. For GraphCompilation, it uses
  `source_graph`.
- Compiled mode renders the compilation. Passing a Graph compiles it first.
- `render_graph_compilation_mermaid(compilation, root_label=...)` is also
  available for an existing compilation.

Execution views use recorded condition results; they do not rerun predicates.
`render_execution(root_step, state)` and `render_live_execution(root_step, state)`
receive the actual root Node and derive its name; they do not accept a second `root_label`.

## Managed and borrowed execution resources

There is no thread-versus-async execution mode or implicit graph-wide action quota.
`run` blocks its caller; `arun` allows its caller to await the same mixed workflow.
The `dynamic` / `compiled` argument only controls presentation records.

```python
from rivlet import arun, run

state = run(workflow, max_workers=4)
# In an async function:
# state = await arun(workflow, max_workers=4)
```

These high-level functions create and shut down their Python thread pool.
`max_workers` defaults to 1 and limits only synchronous Python worker threads, not in-flight
coroutines. The pool starts threads lazily. Pass a Node or an already prepared
Execution; the latter preserves its compilation, results, events and host bindings.
`observer=...` is optional and has the same meaning for both entry points.

For externally managed resources, use the lower-level Execution methods:

```python
from concurrent.futures import ThreadPoolExecutor

from rivlet import Execution

with ThreadPoolExecutor(max_workers=4) as pool:
    execution = Execution(workflow)
    compilation = execution.compile()
    state = execution.run(pool=pool)
    assert execution.compilation is compilation
    assert pool.submit(lambda: 42).result() == 42
```

Inside an async function, use `await execution.arun(pool=pool)`. No loop argument
is needed. `execution.run` rejects calls from an already running event loop;
use `arun` there. Both lower-level methods borrow the pool and never shut it down,
even after an action failure or cancellation. They wait for their own submitted
work to exit, not unrelated work belonging to the pool's owner.

Only same-interpreter ThreadPoolExecutor instances are supported. Process pools
and InterpreterPoolExecutor are not interchangeable: closures, contexts, shared
state and extension values have no serialization/isolation contract here. Native
Rust actions still execute in Rust workers without Python action callbacks; their
session-owned resources are separate from the borrowed Python pool.
Each Rust session defaults to the logical CPU count (1 when unavailable), capped
at 32 and at its number of Rust actions; Python `max_workers` is not its capacity input.
Rust batch completion notifications do not occupy Python action workers, so a
full Python pool cannot prevent a coroutine from consuming an already finished
Rust result.

Runtime-generated subgraphs use the same Python pool and event loop as their
parent execution. Returning a child graph releases the parent worker rather than
waiting recursively inside it. Independent branches have no promised order.
Without explicit `max_concurrency`, there is no automatic bound on outstanding coroutine
tasks or queued synchronous submissions. Apply resource-specific limits explicitly,
for example an asyncio semaphore around network requests; never assume `max_workers`
is a download/API/memory budget.

## Optional subtree concurrency limits

Keep limits outside the graph declaration. All four entry points accept the same flat
`max_concurrency` mapping from a declared Step or Graph to a positive integer:

```python
downloads = (download_a | download_b | download_c).graph()
processing = (process_a | process_b).graph()
workflow = (downloads | processing).graph()

run(
    workflow,
    max_workers=8,
    max_concurrency={workflow: 4, downloads: 2, processing: 3},
)
```

For a borrowed pool, pass the same mapping to
`Execution(workflow).run(pool=pool, max_concurrency=limits)`; the `arun` variants
accept it identically. The graph and its cached compilation do not change. No extra
Python pools are created for subgraphs. Existing Rust worker ownership is unchanged.

An action is in flight from admission until completion or failure, including time
queued in a worker pool and time awaiting I/O. Synchronous Python, coroutine and Rust
actions consume the same scoped allowance. A step must have room in every configured
ancestor scope as well as its own scope. In this example, downloads can use at most 2
slots and processing at most 3, with at most 4 in total. Limits are ceilings, not
reservations or promises of parallelism; independent branches have no fairness guarantee.

Rust checks all allowances before dispatch; waiting actions do not occupy workers or
partially acquire scopes. Graph lifecycle nodes and runtime predicates do not consume
action slots. A step returning a runtime subgraph releases its own slots while waiting
for the child; child actions inherit those scopes across sessions. Ordinary failures
return slots and block only related dependents. Cancellation stops new admission and
drains submitted work, without closing a borrowed pool or forcibly interrupting actions.

Node references must be declared in the execution root and uniquely mounted there.
For shared structures, configure distinct `Graph(shared_structure)` instances, not an
ambiguous internal Step mounted in both. Unknown or ambiguous references and non-positive
limits are errors before any action starts. Limits for declared nodes removed by static
pruning are valid but perform no work. Dynamically created, undeclared nodes cannot be
preconfigured; they inherit the producing step's scopes. Omitting the mapping (or passing
an empty mapping) means no admission limits. The mapping is snapshotted before dispatch.

Only graph actions are counted, not threads, requests or subprocesses spawned inside
one action. A limit can deadlock application code that waits for another action excluded
by that same limit; express dependencies as graph edges rather than blocking inside an
action waiting for a peer. This policy is not a resource-specific rate limit or priority.

## Execution control

ExecutionControl holds stop state and stop handlers, not action results.
Ordinary action or runtime-predicate exceptions do not request global cancellation.
Explicit host cancellation, ExecutionStopped, KeyboardInterrupt, and other
execution interrupts stop the run. The host can register handlers to reclaim
running subprocesses and other resources.

Control is single-use: once stopped, it stays stopped and has no reset.
An execution with already-stopped control raises before running. A stop during a run
prevents new dispatch, waits for already-started work, then reports cancellation.

Each execution has independent control by default:

```python
execution = Execution(root=Graph(structure=(graph).structure))
```

A host may share control between sequential executions:

```python
control = ExecutionControl()
first_execution = Execution(
    root=Graph(structure=(first_graph).structure), execution_control=control
)
second_execution = Execution(
    root=Graph(structure=(second_graph).structure), execution_control=control
)
```

These remain separate runs with independent ExecutionState values. Sharing
control shares cancellation and resource-stop signals, not results. After an
explicit stop, the second execution cannot start. If the caller catches an ordinary
failure and explicitly starts the next execution, control does not prevent it.
For success dependencies, compose one graph or explicitly handle failure.

Process signals and cross-execution request management belong to the host.

Actions can cooperatively check `ctx.control.stop_requested()` or call
`ctx.control.require_running()`. Cancelling the task awaiting `arun()` requests
a stop and waits for started actions to clean up. Stop handlers run outside the
control lock; registering after a stop invokes the handler immediately. All stop
handlers are called before any handler failures are raised together.

## Node execution events

Pass `events=NodeEventSink` to Execution to receive `NodeExecutionEvent`
values with declaration `node_id`, mounted `node_path` (including `.labels`),
typed `state_detail`, and a derived
`state: NodeExecutionState`:
`STARTED`, `COMPLETED`, `FAILED`, `BLOCKED`, or `SKIPPED`.
Static and runtime-generated subgraphs and Rust-native actions use the same
interface. Events describe committed transitions; composite events do not depend
on presentation mode. An event callback is synchronous and serialized. A callback
failure stops the execution with `EventDeliveryError`, preserving the
transition already committed. With no subscription, no event objects or event
label index are created. GraphObserver remains the separate presentation interface.

## Failure propagation

An ordinary Node failure blocks only work that depends on it:

```text
A (failed) → B (blocked, not executed)
C (succeeded) → D (continues)
```

The overall run still reports failure.

- Finished, Skipped, Failed, and Blocked are distinct states.
- Active conditional dependencies and result edges propagate failure;
  inactive conditional edges do not.
- A predicate exception blocks affected nodes instead of reading missing results.
- An entered subgraph lets unrelated internal branches finish, then reports
  failure without AFTER_SUCCESS. Work depending on the whole subgraph is blocked.
  If a subgraph is blocked before entry, all descendants are marked blocked.
- A successful port output remains ready even if another part of its
  provider subgraph later fails. A failed output blocks its bound inputs, not
  unrelated preparation inside the consumer.
- Failure in a dynamically returned graph fails its outer action.
- Python and Rust actions follow the same rules. Native errors and failure states
  cross the boundary in batches, not per-node exception queries.

See [port failure examples](./interfaces.md#failures-at-port-ports).

After scheduling finishes, one error is raised through the exception chain with
its node path; multiple errors are reported together in a Python ExceptionGroup.
Blocked nodes do not produce duplicate errors. Execution snapshots record
`GraphNodeFailure.FAILED | BLOCKED` in `node_failures`, and rendering distinguishes
these terminal states from running work.

Compilation, scheduler-protocol, logging, and observation failures are infrastructure
errors; continuing after them is not guaranteed. Failure neither rolls back
successful work nor retries it. Dependencies, not exception names, determine which
work can continue.

Blocking propagation follows existing adjacency indexes rather than rescanning the
whole graph for every error: blocked-subtree traversal is O(V_blocked + E_out).
Direct-child counters make composite terminal-state aggregation O(V) overall.

## Host runtime interfaces

Execution connects host facilities through two independent interfaces:

- ActionLog establishes an action log scope and appends Python exception text
  from parallel actions.
- ExecutionOutput consumes the final state after success or failure.
  TreeFileExecutionOutput writes a view into an explicitly supplied directory.

Standalone defaults do not connect host logging or write execution-view files.
Control remains the responsibility of ExecutionControl, not the logger.
There is no global-directory discovery or silent change of behavior.

## Explicit skipping

Explicit skip state can target a step path. In compiled mode, skipping a composite
also skips its descendants. This is distinct from failed or blocked state and does
not invoke the action or create its result.

The common declaration, compilation, and result APIs are described in the
[core model](./core_model.md); conditions and failure propagation follow the rules
above.

## Why there are no side-effectful runtime hooks

Graph does not provide hidden before/after-step hooks for cache probes, cache
writes, or skip decisions. Such behavior belongs in the visible declaration:

- Cache probes and writeback are explicit Steps.
- Logging is an explicit ActionLog integration or action wrapper.
- Skipping is an explicit host write through execution control/state APIs.

Observers exist for tracing, timing, visualization, and summaries, not for replacing
Steps or changing results.

## DAG constraints

At construction, Graph validates nonempty explicitly supplied labels, Node-only
node membership, duplicate mounting, local edge endpoints, and local acyclicity.
Omitting a label is valid.

Local explicit edges and runtime inputs are projected to integer nodes and blocking
arcs, then checked by Rust `dag_cycle`, not Python DirectedTopology/DAGTopology.
Cross-subgraph port connections are checked in the final lowered DAG.
See [topology complexity](./topology_complexity.md).

## Subgraphs and result scopes

For a declared subgraph:

- Leaves join the global execution structure.
- The boundary becomes a non-executable CompiledComposite.
- Children can become ready only after composite activation.
- A composite reaches its terminal state after all direct children do.
- An activated empty composite completes immediately.
- GraphObservationMessages.before/after_success follows composite lifecycle.

Runtime-returned graphs cannot be compiled ahead of time. They receive a nested
compilation/session rooted at the current step path.

Result edges resolve producer identities inside the current mount. Actions receive
their ordered Option inputs, not graph scope indexes or a result query context.
An action returning a child graph can capture these received values in its child
actions. There is no implicit lookup into an ancestor graph's result store.

Multiple structural entry nodes are legal. They may all become ready, but readiness
does not imply simultaneous execution.

## Concurrency boundaries

Blocking and awaitable entry points share the same mixed-action scheduling model;
they do not select thread-versus-async modes. Python `max_workers` bounds synchronous
Python threads, not coroutine tasks or Rust workers. Resource ownership is described
in [managed and borrowed execution](#managed-and-borrowed-execution-resources).
ContextVar isolates action log scopes per task.

Only ready nodes are dispatched. Ordinary failures stop dependent work, while
unrelated ready nodes continue. Explicit cancellation stops new dispatch.
Already-running workers/tasks are not forcibly cancelled by Graph.

Runtime-returned subgraphs use nested Rust sessions coordinated by one driver.
Python sessions share one pool and event loop, with no implicit root-wide action quota.
Explicit subtree limits are shared across those sessions; see
[concurrency limits](#optional-subtree-concurrency-limits).
Waiting parents retain their dependency boundary without occupying a worker.
Per-node readiness remains in Rust. Safety of concurrent host-state writes and
limits on work spawned inside individual actions remain the host's responsibility.

Do not rely on ordering between independent Steps. Declare meaningful ordering
with `wire(after=...)`, edges, or `>>`. Graph manages DAG concurrency, not global
host resources, processes, or transactions.
