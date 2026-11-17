# Running Rust graphs

English | [简体中文](./zh-CN/rust-execution.md)

The workspace `execution` crate provides synchronous and asynchronous single-use Rust execution
entry. It composes `graph::Compilation`, the core `ExecutionSession`, and
`action::ActionExecutor`. It does not require Python, introduce another scheduler,
or move execution policy into graph declarations.

Host state can be injected through `ExecutionOptions::shared(Arc<T>)` and accessed
with `ctx.shared::<T>()`, including dynamically generated children. See
[shared state (中文)](./zh-CN/shared_state.md) for synchronization and lifetime rules.

## Public entry

```rust,ignore
use execution::{Execution, ExecutionOptions, ExecutionStatus};

let compilation = graph.compile()?;
let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;

assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
```

`4` is the blocking-action worker capacity, not an extra allowance for each batch
or subgraph. Async actions do not occupy this pool; `max_concurrency(&node, maximum)`
limits admission across both kinds. Idle execution waits on the worker completion
queue rather than polling on a timer. Empty or fully skipped graphs need no worker
pool. Completed actions free slots for the next ready batch.

Run the complete typed-result example from the repository root:

```sh
cargo run --locked -p execution --example typed_results
```

The example declares `design → apply` through `Edge::results`, shares a Rust Plan
without a Clone bound, and prints `apply: 42`. The graph and action APIs are the
same as for low-level hosts; only the execution driver is supplied by the library.

## One execution, one result namespace

`Execution::run(self)` consumes the execution object. To run a compiled graph
again, explicitly create another Execution. Share an `Arc<Compilation>` when
several executions should use the same compiled declaration:

```rust,ignore
let compilation = std::sync::Arc::new(graph.compile()?);
let first = Execution::new(
    std::sync::Arc::clone(&compilation), ExecutionOptions::new(4)?,
).run()?;
let second = Execution::new(compilation, ExecutionOptions::new(2)?).run()?;
```

`run()` does not recompile. Every execution has independent mutable scheduling
state, workers, skips, and result slots. Action handles are shared; mutable state
explicitly captured by an action remains shared according to that action's own
implementation. This API does not promise action isolation.

The outcome retains the exact Compilation for metadata and identity lookup.
`outcome.nodes()` yields `(NodeId, &NodeOutcome)` pairs;
`outcome.node(id)` returns None only for an out-of-range index. NodeId is local to
its compilation, not a cross-graph identifier. Use
`outcome.compilation().node(id)` for mounted metadata and paths. Repeated graph
mounts receive separate result slots even when their declaration identities match.

## Results and failures

NodeOutcome is a closed terminal-state union:

| Variant | Meaning |
| --- | --- |
| `Succeeded(Value)` | Successful step result, or unit for a completed composite |
| `Skipped` | Explicit skip or false node/ancestor condition; no successful value |
| `Cancelled` | Undispatched work or cooperative cancellation; no successful value |
| `Failed(NodeFailure::Action(error))` | Original action error |
| `Failed(NodeFailure::Child(id))` | Composite failed because of this direct child |
| `Blocked(cause)` | Dependency, parent, predicate, or predicate-input failure prevented execution |

Read a successful result using the existing checked `Value::downcast::<T>()`:

```rust,ignore
use execution::NodeOutcome;

match outcome.node(id) {
    Some(NodeOutcome::Succeeded(value)) => {
        let plan = value.downcast::<Plan>()?;
        consume_plan(&plan);
    }
    Some(other) => report_terminal_state(other),
    None => return Err("node index is outside this compilation".into()),
}
```

Result edges bind `Option<Arc<T>>` as usual. Skipped sources produce None;
successful unit produces Some. Failed sources block their dependent actions;
they are not converted into missing results. Values and original action errors
are recorded before completion is returned to the scheduler. Results are read-only
through the outcome API; this is not a cache or a shared mutable-state service.

An action failure does **not** make `run()` return early. Independent branches
finish, dependent branches are blocked, and the returned ExecutionOutcome has
`ExecutionStatus::Failed`. Inspect node outcomes for errors and direct failure
witnesses. `run()` returns Err only for invalid execution input or infrastructure
and scheduler/result consistency errors. Already executed side effects are not
rolled back. Worker panic handling is inherited from ActionExecutor.

## Initial skips

Initial skips use compiled indexes from this same Compilation:

```rust,ignore
let options = ExecutionOptions::new(4)?.skip([node_id]);
let outcome = Execution::new(compilation, options).run()?;
```

Repeated `skip(...)` calls add nodes; they do not replace earlier selections.
Skipping a composite also skips its descendants. Skips affect this execution only,
not the shared declaration or subsequent runs. Unknown indexes are rejected before
workers start.

## Runtime predicates

`Condition::new(predicate, inputs)` declares a synchronous predicate; `new_async` declares an async one.
Use `Step::with_condition` / `Graph::with_condition` for node conditions, and
`Edge::with_condition` / `Edge::ports_with_condition` for conditional edges.
Compilation does not call predicates; `Execution::run()` / `run_async()` evaluate them when their
input results are ready. Predicate failure blocks only affected dependents.

`outcome.condition(id)` returns `ConditionOutcome::{NotEvaluated, Resolved, Failed, Cancelled}`;
failed predicates retain their original `ActionError`. Predicates run on the host
driver, outside action worker capacity. See [Rust runtime predicates](./rust-conditions.md)
and the runnable `crates/execution/examples/conditions.rs` example.

## Asynchronous execution and resource lifetime

`Execution::run_async().await` shares core transitions and result commits; only
waiting and pool ownership differ. Sync predicates retain direct evaluation.
Low-level hosts may call `compilation.condition(id).start(&values)` for Ready or Pending.

The action pool is lazy; async execution adds one cleanup-only thread, not a
predicate worker. Normal completion awaits shutdown. Dropping the Future drops
pending predicates and async actions without joining workers on the event-loop thread. Submitted
synchronous actions finish; undispatched work does not start and effects are not rolled back.

Low-level `ActionExecutor::prepare_wait()` returns a CompletionWait supporting both
`.wait()` and `.await` through notifications. The last low-level pool owner still
controls where its blocking Drop occurs; high-level async cleanup does not change that.

## Scope and costs

Explicit cancellation is configured with `ExecutionOptions::cancellation(token)`;
actions read `ctx.control()`. Both entry points use the same core transitions and
drain submitted work before returning a cancellation outcome. This differs from
dropping a Future. See [cancellation (Chinese)](./zh-CN/cancellation.md).

- Synchronous actions, sync/async predicates, runtime-created graphs and explicit event
  observation are supported. `AsyncAction` adds runtime-independent futures without a Tokio requirement.
  See the [advanced API guide (Chinese)](./zh-CN/node-rust-advanced.md).
- ActionContext has no default log path, carries the execution cancellation token,
  and inherits the explicitly supplied shared object. Hosts needing lower-level contexts or dispatch can compose
  ExecutionSession and ActionExecutor directly.

- Session creation currently clones the compiled core graph once, O(V + E), and
  initializes per-node state/results, O(V). The declaration is not compiled again.
  The driver processes each terminal result/event once and binds R result inputs
  in O(R), excluding the existing scheduler and action costs.
- Successful values remain retained until their outcome is dropped. This entry
  adds no persistent recovery or per-consumer result reclamation. Rust payloads
  remain shared through Arc, without JSON or Python conversion.

For the distinction from Python's on-demand recording and possible future
retention policies (not implemented), see [result lifetime](./result_lifetime.md).

Graph/action/core remain independently usable. The execution crate is a host
composition, not a dependency of graph declarations or the Python extension.
