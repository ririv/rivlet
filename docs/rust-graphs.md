# Python graphs and Rust graphs

English | [简体中文](./zh-CN/rust-graphs.md)

A Python declaration can mount a complete Rust-native subgraph. Both enter one
Rust CompiledExecutionGraph and share dependencies, hierarchy, failure propagation,
and explicit concurrency scopes, when configured. Native subgraphs are not converted into
Python actions.

```text
Python Graph / Step
    ├── Python Step
    └── Graph(RustGraphStructure)
          ├── Rust Step
          └── Rust Graph → Rust Step / Graph
                │
                ▼
    Rust CompiledExecutionGraph
        ├── Python step → Python driver
        └── Rust step   → Rust worker
```

Python holds an opaque RustGraphStructure, not individually callable Rust Step or
Rust action objects. Native action execution does not require a per-action Python
callback. The mixed driver receives batches of native completion events for
results, logs, and presentation. Internal nodes remain visible in compiled and
execution views; opacity does not hide them as a single undifferentiated action.

## Declaration boundary

A Rust adapter constructs graph::GraphStructure. The Python adapter wraps it as
RustGraphStructure; Python composes that body into a Graph alongside Python actions:

```python
from rivlet import Graph, RustGraphStructure, Step


def make_workflow(body: RustGraphStructure) -> Graph:
    prepare = Step(lambda: print("prepare"), label="prepare")
    finish = Step(lambda: print("finish"), label="finish")
    rust_work = Graph(body, label="rust-work")
    return (prepare >> rust_work >> finish).graph()
```

`body` must come from an adapter that provides a RustGraphStructure. The function
does not invent an importer or require any particular external build system.
The Rust declaration API below shows how to construct the underlying graph in Rust.

Here rust_work is an outer composite. Rust sees the internal nodes and edges for
global scheduling while preserving the composite boundary.

RustGraphStructure is the smallest native unit exposed to Python. There is no public
Python Action or ProcessAction API that would create a second per-action
submission path.

## Execution and results

Within one execution session:

- Python leaves are returned in batches to the Python driver, with arbitrary
  callables, Python results, and runtime-returned graphs.
- Rust leaves go directly to Rust workers and a native completion queue.
- The mixed driver receives native start, completion, and failure information.
- Python thread capacity and Rust workers are separate execution resources.
  Explicit `max_concurrency` scopes count both languages together; without them,
  there is no shared action quota. See [execution limits](./execution.md#optional-subtree-concurrency-limits).

Native actions declare typed inputs and outputs. Rust-to-Rust result edges share
Rust-owned values; Python conversions happen only at the adapter boundary.
A process action returns unit, observed as `Some(None)` in Python, not an absent
result. See [native result values](./rust-results.md) for the API and conversion rules.
Native actions still cannot read Python NodeRunContext or expand dynamic graphs.

## Rust crates

- action: native action trait, execution context, errors, and workers.
- graph: Rust Step/Graph nodes, Node trait, GraphStructure, typed ports, and GraphError.
- topology: integer nodes and pure graph algorithms.
- core: language-independent declaration compilation and execution sessions.
- python: integration of Python Graph and complete RustGraphStructure declarations.

Adapters may implement arbitrary Rust actions, but Rust trait objects are not
passed between dynamic libraries. An independent adapter should provide a stable
batch graph specification, from which the Graph extension constructs RustGraphStructure
once. This preserves package boundaries without routing each action through Python.

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

The Python adapter imports this projection into the same compiler call as Python
nodes. Python can inspect hierarchy/ports and explicitly forward or connect them;
it does not obtain Rust action objects. Tree/Mermaid, structural export, logs,
events, skips, and failure isolation preserve real native graph boundaries. The
private process/composition importer functions are adapter boundaries, not a second
public Python declaration API.

Readiness connections and result connections remain distinct uses of the same
interface: `Edge::ports` waits for readiness; `Edge::results` additionally binds a
complete ordered argument group. Actual step types/arity are checked after
forwarding. Rust predicate execution is still outside this API. An outer Python
Graph retains its existing runtime/static conditions; core gains neither opaque
closures nor implicit serialization.
