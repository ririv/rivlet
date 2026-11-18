# Rust runtime predicates

English | [简体中文](./zh-CN/rust-conditions.md)

The `rivlet::graph` module declares synchronous, typed Rust predicates. The `rivlet::execution`
module evaluates them through the same core `ExecutionSession` used by the other
frontends. No Python callback, Tokio dependency, extra Step, or serialized value
is involved. This is runtime selection, not compile-time static pruning.

## Declare a decision

```rust,ignore
use std::sync::Arc;
use rivlet::Condition;

let approved = Condition::new(
    |(flag,): (Option<Arc<bool>>,)| Ok(flag.is_some_and(|flag| *flag)),
    [(&probe).into()],
)?;
```

The predicate is first and its ordered result sources second. Sources are
`OutputEndpoint` values: convert a direct Step reference or a Graph's output port
with `.into()`. Only direct local nodes or public child ports may be referenced.
An empty source list has a concrete type, so `Condition::new(|()| Ok(true), [])?`
needs no placeholder source.

The callback is `Fn(I) -> Result<bool, ActionError> + Send + Sync + 'static`.
`I` reuses the existing `ActionInputs` decoding contract: `()` or an ordered tuple
of `Option<Arc<T>>`. Reusing this input/error primitive does not turn a predicate
into an action node. A skipped producer gives None; a successful unit value gives
Some. Failed inputs block the user without invoking its predicate.

Source count is checked at Condition construction. Actual source types and
mounted references are checked during projection/compilation, before any action
or predicate runs. Action and condition input groups are independent.

## Nodes and edges

```rust,ignore
let work = Step::with_condition("work", action, approved)?;
let child = Graph::with_condition("child", body, child_condition)?;

let dependency = Edge::with_condition(&prepare, &work, should_wait);
let readiness = Edge::ports_with_condition(
    provider.interface().output("ready")?,
    consumer.interface().input("use")?,
    should_wait_for_provider,
);
```

`Step::new` / `Graph::new` continue to construct unconditional nodes.
`with_condition` is a complete associated constructor, not a mutating method:
it allocates a new declaration identity and never overrides an existing node.
`Step`, `Graph`, and `Node` each expose `condition()` along with identity and label.

| Use | True | False |
| --- | --- | --- |
| Step condition | Wait for remaining prerequisites and run the action | Skip the Step |
| Graph condition | Enter the body after remaining prerequisites | Skip the Graph and all descendants |
| Completion edge condition | Wait for the source node to finish | Remove that waiting relationship |
| Port edge condition | Wait for the actual exposed provider | Remove that waiting relationship |

False does not cancel unrelated work. Result edges remain complete ordered action
argument groups and do not accept a separate condition. A predicate's declared
reads participate in DAG validation even though they are not action arguments.
Conditional port connections are checked after resolving actual internal nodes,
not by inventing whole-graph dependency cycles.

Each compiled use is evaluated at most once per execution. Reusing a Condition
clones its immutable declaration and callable ownership, not its decision cache.
Distinct mounts resolve input sources in their own scopes; repeated executions
also have independent decisions and outcomes. Mutable state explicitly captured
by a callable is still shared according to that state's own Rust synchronization.

## Run and inspect

Run the complete example:

```sh
cargo run --locked -p rivlet --example conditions
```

It prints:

```text
condition: true
apply: 42
```

The condition reads `enabled`; the action separately reads `plan`. Both paths are
declared explicitly. The example uses the actual Rust constructors, compiler and
high-level driver, not a simulated scheduler.

`ExecutionOutcome::condition(ConditionId)` returns a reference to a closed union:

| Variant | Meaning |
| --- | --- |
| `NotEvaluated` | No request was evaluated, e.g. the owner was skipped or an input failed |
| `Resolved(bool)` | The predicate returned this decision |
| `Failed(ActionError)` | The original predicate/decoding error, or a caught predicate panic |

None means only that the condition index is outside this compilation. Obtain the
runtime index from `compilation.graph().nodes()[node.index()].condition()` or from
a `BlockCause` condition site. `compilation.condition(id)` resolves the compiler's
condition mapping and exposes the mounted input indexes for a low-level host.

A predicate error is submitted as a failed condition resolution. Its users become
`NodeOutcome::Blocked` with a direct `BlockCause::Condition` witness; downstream
work is blocked, while independent branches finish. It is neither a false
decision nor a fabricated action failure. Input failures instead retain a
`BlockCause::ConditionInput` witness and leave the predicate NotEvaluated. Panics
are caught like action panics, without installing or replacing a global panic hook.

## Scheduling and boundaries

Predicates execute synchronously on the host driver, outside action worker slots.
They should remain short: a blocking predicate delays further driver processing,
although already-started worker actions can continue. Use a Step for expensive
work needing action concurrency budgets or its own output.

The worker pool is created only on the first actual action dispatch. A graph whose
conditions skip all actions does not create idle workers. Successful payloads are
shared through the existing Rust Value/Arc protocol without cloning the payload
or converting it to JSON.

This change does not add Rust async predicates, static selection, runtime-created
Rust graphs, cancellation, or observation subscriptions. Pure Rust conditions can
be consumed by the Rust high-level driver or a custom low-level host.

The Python and Node.js frontends execute their own language predicates; they do
not register Rust-native actions or predicates.
