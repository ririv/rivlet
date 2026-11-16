# Rust graph declarations

English | [简体中文](./zh-CN/rust-graphs.md)

Rust Step, Graph and GraphStructure compose native workflows using the shared core
compiler and scheduler. Mainline does not import Rust nodes into Python workflows.

## Rust declaration API

The `graph` crate exposes actual `Step` and `Graph` nodes. Both compose common
identity/label metadata and implement `Node`; `NodeKind` is the closed
`Step | Graph` enum. The trait does not introduce implementation inheritance or
arbitrary executable node kinds. Only `Graph` has an interface.

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, Graph, GraphInterface, GraphStructure, Step};

struct Work;
impl Action for Work {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        println!("work");
        Ok(())
    }
}

fn workflow() -> Result<graph::Compilation, graph::GraphError> {
    let task = Step::new("task", ActionHandle::new(Work))?;
    let body = Arc::new(GraphStructure::with_interface(
        [task.clone().into()], [],
        GraphInterface::default()
            .input("apply", [&task])?
            .output("ready", &task)?,
    )?);
    let left = Graph::new("left", Arc::clone(&body))?;
    let right = Graph::new("right", body)?;
    let edge = Edge::ports(
        left.interface().output("ready")?,
        right.interface().input("apply")?,
    );
    Graph::new("root", Arc::new(GraphStructure::new(
        [left.into(), right.into()], [edge],
    )?))?.compile()
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
let wrapper = Graph::new("wrapper", Arc::new(GraphStructure::with_interface(
    [child.into()], [], interface,
)?))?;
```

Local references must belong directly to that structure; use child ports for
descendants. Port handles retain their graph identity and direction. Missing or
ambiguous membership is an error, never guessed. Cloning a node preserves its
declaration identity; constructing two `Graph` nodes sharing an `Arc<GraphStructure>`
creates distinct mounts. Repeated inner identities are disambiguated by mounted
paths. Repeating the same node twice in one structure is rejected.

`Projection` collects mounted scopes, resolved ports, metadata, and action handles;
it calls the existing core `GraphDeclaration` compiler. `Compilation` holds that
exact compiled graph and its side table. The host drives `ExecutionSession` and
uses `compilation.node(id)` to locate each dispatched Rust leaf's action. Composites
are never submitted as no-op actions. `Graph::compile()` retains the actual root;
`GraphStructure::compile_structure()` compiles a body without inventing a root action.

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
