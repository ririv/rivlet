# Rust graph declarations

English | [简体中文](./zh-CN/rust-graphs.md)

Rust Step and Graph compose native workflows using the shared core
compiler and scheduler. Mainline does not import Rust nodes into Python workflows.

## Rust declaration API

The `rivlet::graph` module exposes `Step` and `Graph`. The closed `Node` enum
holds either declaration; `Graph::nodes()` returns a slice of these values.
Wire a `Step` or `Graph` directly with `Edge::new(&source, &target)`: its sealed
`HasNodeId` trait only supplies declaration identities. Convert with `.into()`
when adding the concrete values to a parent graph. Only `Graph` exposes an interface.

```rust
use std::sync::Arc;
use rivlet::{Action, ActionContext, ActionError, ActionHandle};
use rivlet::{Compilation, Edge, Graph, GraphError, GraphInterface, Step};

struct Work;
impl Action for Work {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        println!("work");
        Ok(())
    }
}

fn workflow() -> Result<Compilation, GraphError> {
    let task = Step::new("task", ActionHandle::new(Work))?;
    let body = Arc::new(Graph::with_interface("scope",
        [task.clone().into()], [],
        GraphInterface::default()
            .input("apply", [&task])?
            .output("ready", &task)?,
    )?);
    let left = Graph::with_interface("left", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
    let right = Graph::with_interface("right", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
    let edge = Edge::ports(
        left.interface().output("ready")?,
        right.interface().input("apply")?,
    );
    Graph::new("root", [left.into(), right.into()], [edge])?.compile()
}
```

`Work` is any Rust implementation of the existing action trait, not restricted to
processes. `Edge::new(&a, &b)` waits for complete nodes; `Edge::ports(output, input)`
waits for the specific exported provider and constrains all exported consumers.
An input can fan out. A consumer can therefore start before the provider's entire
graph finishes. Interleaved graph dependencies are checked after resolving actual
endpoints, rather than approximated as component-level cycles.

Forward a child's ports explicitly when constructing an enclosing structure:

```rust,ignore
let interface = GraphInterface::default()
    .input("apply", [child.interface().input("apply")?])?
    .output("ready", child.interface().output("ready")?)?;
let wrapper = Graph::with_interface("wrapper", [child.into()], [], interface)?;
```

Local references must belong directly to that structure; use child ports for
descendants. Port handles retain their graph identity and direction. Missing or
ambiguous membership is an error, never guessed. Cloning a node preserves its
declaration identity; explicitly constructing another Graph from
`body.nodes().iter().cloned()` and `body.edges().iter().cloned()` creates a distinct
graph identity. Reuse its interface through `body.interface().declaration().clone()`.
Repeated inner identities are disambiguated by mounted
paths. Repeating the same node twice in one structure is rejected.

`Projection` collects mounted scopes, resolved ports, metadata, and action handles;
it calls the existing core `GraphDeclaration` compiler. `Compilation` holds that
exact compiled graph and its side table. The host drives `ExecutionSession` and
uses `compilation.node(id)` to locate each dispatched Rust leaf's action. Composites
are never submitted as no-op actions. `Graph::compile()` retains the actual root.
The original declaration remains available; compilation does not replace it with
its expanded execution graph.

The workspace `execution` crate also provides a composed synchronous
`Execution::new(compilation, options).run()` entry. It runs this exact Compilation
without recompiling; low-level hosts may still drive session/worker APIs directly.
See [running Rust graphs](./rust-execution.md) for complete examples and outcomes.



Readiness connections and result connections remain distinct uses of the same
interface: `Edge::ports` waits for readiness; `Edge::results` additionally binds a
complete ordered argument group. Actual step types/arity are checked after
forwarding. [Rust runtime predicates](./rust-conditions.md) use typed closures held
by the Rust projection and evaluated by the Rust Execution host; core gains neither
opaque closures nor implicit serialization.
