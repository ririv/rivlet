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

## Python boundary

Python declarations keep the existing API:

```python
edges = Edge.results(
    (producer.interface.outputs["plan"],),
    consumer.interface.inputs["apply"],
)
```

| Direction | Transport |
| --- | --- |
| Rust → Rust | Original native value, no Python conversion |
| Python → Python | Existing arbitrary Python objects |
| Rust → Python | Lazy adapter conversion, cached once per result |
| Python → Rust | Explicit receiver-type conversion before native dispatch |

Standard conversions cover unit/None, bool, signed/unsigned Rust integer widths,
f32/f64, String/str and Vec<u8>/bytes. Integer bounds are checked. Bool is not
accepted as an integer; strings are not parsed as numbers; other numeric categories
are not silently interchanged. Bytes conversion may copy the payload. Arc sharing
is zero-copy ownership inside Rust, not a claim of universal zero-copy FFI.

Only Python results referenced by native input edges are imported. Arbitrary
unrelated Python objects remain Python objects and are not serialized. Native-only
graphs with no Python result retention do not create Python result wrappers.
Retaining Python results stores an opaque native reference; the concrete Python
value is constructed only when read. Python result inputs use the existing Option
and unwrap API, not a new public RustResult API.

Custom conversion is an explicit PyO3 adapter responsibility. Internally, `ValueCodecs`
composes paired Rust export/import functions for a concrete type. A type's converter
cannot overwrite an existing or standard converter. Combining graphs can reuse the
same converter object; conflicting registrations are rejected. There is no global
mutable registry or public Python dynamic-registration plugin API. Independent
adapter distribution/ABI remains a separate boundary, not implicit trait-object
exchange between dynamic libraries.

A custom Rust value with no converter is valid on Rust-only edges. Reading it from
Python fails at that reader with an explicit missing-converter error. An invalid
Python input fails its native receiving step. Python representation mutation is not
automatically mirrored into the original Rust value; converters define value views,
not shared mutable state.

Tree/Mermaid and structural export show native result edges and argument positions
without including values, commands, environments, or converter internals. These
changes do not add native predicate execution or runtime graph expansion.
