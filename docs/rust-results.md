# Rust result values

English | [简体中文](./zh-CN/rust-results.md)

Result edges both wait for a source and bind its result to a positional action
argument. Payloads belong to the executor, not the language-independent scheduler.
There is no implicit JSON conversion and no result lookup added to action contexts.

## Rust declarations

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, GraphStructure, Step};

struct Plan { count: i64 }
struct Design;
impl Action for Design {
    type Inputs = ();
    type Output = Plan;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Plan, ActionError> {
        Ok(Plan { count: 21 })
    }
}

struct Apply;
impl Action for Apply {
    type Inputs = (Option<Arc<Plan>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (plan,): Self::Inputs)
        -> Result<i64, ActionError>
    {
        let plan = plan.ok_or_else(|| ActionError::new("plan was skipped"))?;
        Ok(plan.count * 2)
    }
}

fn workflow() -> Result<graph::Compilation, graph::GraphError> {
    let design = Step::new("design", ActionHandle::new(Design))?;
    let apply = Step::new("apply", ActionHandle::new(Apply))?;
    let results = Edge::results([&design], &apply)?;
    GraphStructure::new([design.into(), apply.into()], [results])?.compile_structure()
}
```

`Output` can be any `Send + Sync + 'static` Rust type; it need not implement Clone
or a serialization trait. The executor owns it through a shared native value.
Inputs receive `Option<Arc<T>>`; fan-out clones Arc references, not T. Internal
type erasure is checked against exact Rust type identities. Built-in tuple input
implementations cover one through twelve slots; custom `ActionInputs` implementations
can declare other complete input groups, so graph fan-in is not capped at twelve.

Compilation validates arity, ordered source types, one complete result group per
actual target, and the final dependency DAG. Public graph inputs may remain
unbound while composing; final compilation must close every active action's inputs.

## Across subgraphs

The same graph-owned ports used for readiness can carry results when explicitly
connected by a result edge:

```rust,ignore
let edge = Edge::results(
    [producer.interface().output("plan")?],
    consumer.interface().input("apply")?,
)?;
```

Input forwarding may reach several leaves. Each receives the entire ordered group
and is checked independently. Two graph nodes sharing a GraphStructure retain
separate mounted indexes and results; declaration identity alone is never a store key.
The executor must retain results within their execution session/index namespace.

The workspace `execution` crate supplies that store and driver for pure Rust
hosts. [Execution::run](./rust-execution.md) returns immutable terminal node
outcomes bound to their Compilation; custom hosts may still compose the lower-level APIs.

`Edge::new` does not transport values. `Edge::ports` establishes readiness only.
There is no inference from callable parameter names or hidden dependency via context.

## Presence, completion, and failure

| Event | Input meaning |
| --- | --- |
| Source succeeded with T | `Some(Arc<T>)` |
| Source was skipped | `None` |
| Source succeeded with unit | `Some(Arc<()>)`, Python `Some(None)` |
| Source failed | Existing failure/blocking propagation, not a missing value |

A worker puts its actual result in the completion queue. The execution layer stores
that value **before** notifying the scheduler of successful completion. A finished
source without its required value is an error, never silently treated as skipped.
Conversions into native inputs fail the receiver, not unrelated branches.

The result store is per execution, not a persistent cache or mutable shared-state
API. Ordinary native results needed by native consumers are retained through their
session; this does not add per-consumer reference-count GC or durable recovery.

Python-side recording is enabled on demand; enabling it does not selectively
retain only consumed Python results. Standalone Rust outcomes have their own
ownership boundary. Current activation/retention rules and explicitly unimplemented
policy candidates are documented in [result lifetime](./result_lifetime.md).

See [Rust execution](./rust-execution.md) for native result inspection. Python
objects use their separate [result lifetime](./result_lifetime.md) contract. Mainline
does not provide cross-language result conversion.
