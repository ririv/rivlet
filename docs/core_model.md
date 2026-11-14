# Core model

English | [简体中文](./zh-CN/core_model.md)

```text
Node (shared protocol)
├── Step: identity + label + condition + callable
└── Graph: identity + label + condition + GraphStructure
                                         ├── nodes: Step | Graph
                                         ├── edges
                                         └── interface declarations / static rules
                        ↓ Execution(root).compile()
                   GraphCompilation
                        ↓ run() / arun()
                   ExecutionState
```

There is no generic node wrapper: Step holds an action, not a Graph. Step and Graph
compose shared internal metadata; neither
inherits implementation from Node. Node describes common operations;
`NodeKind = Step | Graph` is the closed set accepted by structures and execution.
Implementing a lookalike protocol does not register another executable kind.
The former wrapper's ownership problem and this decision are described separately
in the [node modeling discussion](./design/node-modeling.md).

## Nodes and reusable structure

```python
from rivlet import Edge, Execution, Graph, GraphStructure, Step, run

prepare = Step(lambda: print("prepare"), label="prepare")
finish = Step(lambda: print("finish"), label="finish")
body = GraphStructure(nodes=(prepare, finish), edges=(Edge(prepare, finish),))
workflow = Graph(body, label="workflow", condition=lambda: True)
run(workflow)

left = Graph(body, label="left")
right = Graph(body, label="right")
two_runs = (left | right).graph(label="two runs")
```

GraphStructure contains nodes, edges, GraphInterface declarations, observation
messages, StaticConditions and StaticSelection. It has no identity, label, runtime
condition or run state. Graph composes a structure and exposes `graph.interface`;
Step has no interface attribute. RustGraphStructure is an opaque Rust-owned body,
also composed with `Graph(native_structure, label="native")`.

Two Graph instances can share one structure. Their identities differ; shared inner
declarations retain theirs, but each mount has independent paths, results and
condition evaluations. A node cannot occur twice in the same structure. DSL
composition preserves actual nodes, never silently allocates replacement wrappers.

## Identity and presentation

- `node.id: NodeId` is allocated once per declaration, in O(1), through the shared
  Rust counter. No global declaration registry is retained.
- NodePath identifies a mount, such as `0.1.0`. Its `.segments`, `.labels` and
  `.parent` describe position and presentation. Labels do not affect equality/hash.
- Labels may repeat; an unlabeled node displays its numeric path.
- Execution mounts its actual Step or Graph root at `0`, with children at `0.0`
  and so on. It creates no wrapper identity or no-op action.
- Declaration IDs are not durable business IDs or cache keys. Rust calls that
  identity NodeIdentity; its low-level topology NodeId is an indexed reference.

## Edges, wire and DSL

```python
direct = Graph(
    GraphStructure(
        nodes=(prepare, finish),
        edges=(Edge(prepare, finish),),
    )
)
with_wire = Graph.from_wiring((prepare.wire(), finish.wire(after=(prepare,))))
with_dsl = (prepare >> (left | right) >> finish).graph(label="pipeline")
```

NodeWiring is construction input, not an executable wrapper. GraphStructure keeps
normalized Edges. `>>` connects structural exits to entries; `|` adds no dependency.
Both produce GraphWiring, materialized by `.graph(...)`. Operands are Step, Graph
or existing GraphWiring, not raw functions. There is no `.step()` materializer.
Subgraph boundaries remain intact. DSL boundaries are not named interface ports;
ports are never automatically forwarded or connected.

Edges reference actual local nodes, not labels. ConditionalPrerequisite adds a
wait only when its predicate is true, without skipping either endpoint. Cross-graph
connections use explicit ports. Rust validates the final resolved DAG; a cycle
drawn between components is not necessarily a cycle between their actual leaves.

## Graph declaration decorator

```python
from rivlet import Execution, GraphWiring, graph, run, step


@step
def prepare() -> None:
    print("prepare")


@graph
def pipeline() -> GraphWiring:
    @step
    def build() -> None:
        print("build")

    return prepare >> build


run(pipeline)
```

`@graph` immediately calls its zero-argument declaration once and passes the
returned GraphWiring to `Graph.from_wiring`, using the function name as the label.
After decoration, `pipeline` is a Graph, not a callable. This differs from `@step`,
which wraps a callable without running the action. Declaration errors propagate;
there is no retry or execution-time reconstruction.

The decorator does not discover local variables: only nodes in the returned wiring
are included. Outer Steps and Graphs are ordinary operands, retaining their
identities and interfaces. The resulting Graph supports the same DSL, `wire`,
compilation and structure reuse as a directly constructed Graph.

The declaration returns GraphWiring, not a prebuilt Graph, and does not call
`.graph()` itself. For parameterized construction, explicit graph metadata,
interfaces or static rules, keep using ordinary functions and the existing
`Graph` / `Graph.from_wiring` / `.graph(...)` constructors. `@graph` adds no new
template, lazy factory or execution semantics.

## Results and runtime context

```python
from rivlet import NodeRunContext, Option, Results

plan = Step(lambda: "plan")
review = Step(lambda: True)


def implement(
    ctx: NodeRunContext, plan: Option[object], review: Option[object]
) -> None:
    ctx.control.require_running()
    print(plan.unwrap(), review.unwrap())


work = Step(implement)
root = Graph.from_wiring(
    (
        prepare.wire(),
        plan.wire(),
        review.wire(),
        work.wire(after=(prepare, Results(plan, review))),
    )
)
```

Plain prerequisites only order execution. Results also passes ordered Option
arguments; `Edge.results((plan, review), work)` creates the same edges directly.
Multiple Results entries in one after tuple concatenate into one complete group.
Do not duplicate their producers as ordinary prerequisites, slice a returned group,
or install independent groups on the same action.

Without inputs Step calls `fn()` or `fn(ctx)`; when both are valid `fn()` wins,
preserving default-argument closures. With inputs it calls `fn(ctx, result_a, ...)`.
Annotations never select invocation semantics. Complete arity is checked before run.

Results can cross subgraphs through actual Graph-owned ports:

```python
from rivlet import GraphInterface

producer = (plan | review).graph(
    interface=GraphInterface(outputs={"plan": plan, "review": review}),
)
consumer = Graph(
    GraphStructure(
        nodes=(work,),
        interface=GraphInterface(inputs={"apply": work}),
    )
)
root = Graph(
    GraphStructure(
        nodes=(producer, consumer),
        edges=Edge.results(
            (producer.interface.outputs["plan"], producer.interface.outputs["review"]),
            consumer.interface.inputs["apply"],
        ),
    )
)
```

An exposed input may await outer wiring. Final compilation rejects unresolved
inputs, conflicting groups and actual cycles. Port references retain their Graph
owner through forwarding and reuse; no `(Step, "port")` or separate bind is needed.
See [interfaces](./interfaces.md).

- Some(value) means successful return, including Some(None).
- Nothing() means no value, such as runtime skip or a value-less composite.
- Failed producers block consumers; failure is not Nothing.
- Static removal of a required result source is a compilation error.
- Explicit inputs enable recording before producers run.

ResultView has get/require/has, not writes. Successful results are recorded once
per path. Snapshots copy records, not payload objects; treat published values as
immutable. NodeRunContext exposes node_id, node_path, cooperative control, and
shared(ExpectedType). It has no result lookup, log path or scope-index escape hatch.
Hosts can inspect Execution.require_result(node). Shared state is explicitly passed
to Execution; callers own synchronization and cleanup. Sharing creates no edges.

## Conditions and selection

Step and Graph accept the same runtime condition: a zero-argument bool predicate
or flat `(predicate, source_node, ...)` tuple. Sources supply Options independently
of action Results. False skips the node; false on Graph skips its subtree.
Edge conditions remove only a wait. StaticConditions binds host evaluator and
rules for compilation; pruning never reconnects removed paths.

`static.select(cases=(Case(bool, node), ...))` retains candidates and requires exactly
one true case during compilation. select_one instead chooses an ordinary value
before construction. These APIs do not defer creation of unselected Python objects.
See [node conditions](./conditional_steps.md), [conditional edges](./conditional_edges.md)
and [static selection](./static_selection.md).

## Static selection before construction

select_one accepts StaticBranch(active=bool, value=...) entries and returns the
original selected value. Exactly one must be active. Select a factory and call it
if unselected declarations must not be constructed; the graph-internal static.select
instead retains every constructed candidate. Neither uses declaration order as priority.

## Execution and compilation

Execution accepts Step or Graph. Its first successful compile is reused by
compile/run/arun; create another execution to evaluate static rules again. State
is single-use. Execution paths share Rust compilation/scheduling. Python nested
sessions share the execution's Python pool and event loop; explicitly configured
concurrency scopes apply across sessions, with no implicit action quota.
GraphCompilation contains Python leaves,
native leaves and composite lifecycle boundaries, not no-op composite actions.
Low-level compile_graph accepts GraphStructure for inspection/expansion.

An action returning GraphStructure expands a body under its current node. Returning
Graph attaches that actual node, preserving identity, label, condition and boundary.
Neither permits arbitrary mutation of a running declaration. Result lookup remains
mount-local. See [execution](./execution.md) and [typed events](./failure_events.md).

Bind logs at construction with `Execution(root, node_log_paths={node: path})`.
References must identify one mount; resolution is O(V+K) for V nodes and K bindings,
with no traversal for an empty mapping. Leaves inherit the nearest ancestor binding.
ActionLog owns routing; actions cannot rewrite bindings. Observers and event sinks
are explicit; disabled sinks construct no event payloads. Events follow committed
transitions and run synchronously. Delivery failure stops execution without
relabeling completed work. Events are not persistence, messaging or recovery.

## Pure topology and native bodies

DirectedTopology permits cycles; DAGTopology validates acyclicity. They know nothing
about actions, ports or state. See [topology complexity](./topology_complexity.md).
Rust adapters expose RustGraphStructure, whose actions remain Rust-owned while
sharing one compiled execution graph. See [native graphs](./rust-graphs.md).
