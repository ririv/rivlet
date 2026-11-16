# Python graphs and Rust graphs

English | [简体中文](./zh-CN/rust-graphs.md)

> **Experimental:** Python/Rust mixed execution and the extension SDK, as one feature.
> See [status and removal boundaries](./mixed-execution.md). This does not mark the
> pure Rust declaration/Execution::run APIs experimental.

A Python declaration can mount a complete Rust-native subgraph. Both enter one
Rust CompiledExecutionGraph and share dependencies, hierarchy, failure propagation,
and explicit concurrency scopes, when configured. Native subgraphs are not converted into
Python actions.

```text
Python Graph / Step
    ├── Python Step
    └── Graph(GraphStructure)
          ├── Rust Step
          └── Rust Graph → Rust Step / Graph
                │
                ▼
    Rust CompiledExecutionGraph
        ├── Python step → Python driver
        └── Rust step   → Rust worker
```

Python uses the same public GraphStructure for Python and Rust bodies, not individually callable Rust Step or
Rust action objects. Native action execution does not require a per-action Python
callback. The mixed driver receives batches of native completion events for
results, logs, and presentation. Internal nodes remain visible in compiled and
execution views; opacity does not hide them as a single undifferentiated action.

## Declaration boundary

A Rust adapter constructs a Step or Graph and exports it through the
`rivlet-python` binding SDK's `export_node`. Python receives the corresponding
ordinary node and composes it directly alongside Python actions:

```python
from rivlet import Graph, Step


def make_workflow(rust_work: Graph) -> Graph:
    prepare = Step(lambda: print("prepare"), label="prepare")
    finish = Step(lambda: print("finish"), label="finish")
    return (prepare >> rust_work >> finish).graph()
```

`rust_work` can be the real `demo_native.pipeline` attribute in
[the independent-extension guide](./python-native.md). The declaration API below
shows how to construct the underlying graph in Rust.

Here rust_work is an outer composite. Rust sees the internal nodes and edges for
global scheduling while preserving the composite boundary.

Step is the smallest native node exposed to Python. Its action remains Rust-owned
and joins the same native driver; there is no Python Action or ProcessAction
submission path. To reuse a Graph body, use `rust_work.structure` to create new
Graph nodes with explicit metadata.

## Anonymous and named ports

Rust uses `graph::PortName::Anonymous` and `PortName::Named(String)` as distinct
identities. Existing string arguments remain named ports. The same interface can
declare `.input(PortName::Anonymous, [&task])?` alongside `.input("apply", [&task])?`,
and `.output(PortName::Anonymous, &task)?` alongside named outputs.
Lookup is explicit: `node.interface().input(PortName::Anonymous)?` and
`.output(PortName::Anonymous)?`. A sole named port is never selected implicitly.
Forward these references or pass them to `Edge::ports` / `Edge::results` as usual.
Python exposes the same identity as `None`, including opaque Rust graph interfaces.

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
- python: integration of concrete Python/imported nodes and reusable structures.

Adapters implement arbitrary Rust actions without passing Rust trait objects
between dynamic libraries. `crates/bridge` carries versioned C-layout callbacks
and batched metadata; `crates/python-sdk` exports concrete Step or Graph nodes.
Graph body reuse uses `.structure`. Actions and values retain their originating library owner.
See [the SDK boundary and value limitations](./python-native.md).

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
forwarding. [Rust runtime predicates](./rust-conditions.md) use typed closures held
by the Rust projection and evaluated by the Rust Execution host; core gains neither
opaque closures nor implicit serialization. An outer Python Graph retains its
existing runtime/static conditions. The Python mixed driver evaluates Rust synchronous predicates natively,
without Python predicate callbacks or Python action worker slots. Rust async
predicates remain unsupported.
