# Node modeling decision

English | [简体中文](../zh-CN/design/node-modeling.md)

This records the adopted direction; the specification is in the [core model](../core_model.md).

## Problem in the wrapper design

The former generic Step wrapper held either an action or a subgraph, put identity,
label and condition on that wrapper, and made it every edge's endpoint. Graph owned
its interface on another layer.
This unified lifecycle, but hid concrete capabilities: `(step, "plan")` appeared
to reference a port even when that wrapper held only an action. Forwarding an original graph's
interface could also lose which mount was intended. Construction shortcuts reduce
spelling but do not resolve that ownership gap.

## Adopted model

Step and Graph are actual nodes, each composing common metadata. Only Graph exposes
an interface. Node is their shared protocol; NodeKind is their closed union.
GraphStructure holds the reusable body. Edges reference actual nodes, and result
edges can reference typed ports obtained from that actual graph:

```python
left = Graph(shared_structure, label="left")
right = Graph(shared_structure, label="right")
edges = Edge.results(
    (left.interface.outputs["plan"],), consumer.interface.inputs["apply"],
)
```

Reusing a structure does not require guessing the mount. Step does not gain an
interface through wrapping. Forwarded port references retain their owning graph.

## Composition and trade-offs

| Former form | Current form |
| --- | --- |
| Step(action, label=...) as a generic wrapper | Step(action, label=...) as an action node |
| Step(graph, label=...) | Graph(structure, label=...) |
| Graph(steps=..., edges=...) | Graph(GraphStructure(nodes=..., edges=...)) |
| wiring.step(label=...) | wiring.graph(label=...) |
| StepWiring | NodeWiring |
| StepRunContext / step_id / step_path | NodeRunContext / node_id / node_path |
| NativeGraph (opaque body) | RustGraphStructure |

Execution receives an actual Step or Graph directly; application factory return
contracts are not part of this node model.
Events and mounted identity types likewise use the Node prefix.

The action node retains the familiar Step name (the intermediate Leaf name is
removed without an alias). This does not restore the former wrapper: Step cannot
hold a Graph, Graph is directly composable, and only Graph owns an interface.

A protocol is not implementation inheritance. Common metadata remains composed
into concrete types; the compiler still uses step/composite sum variants. Node
supplies no mutable base implementation and does not register arbitrary node kinds.

The former wrapper was not universally inferior: it centralized lifecycle metadata
and provided one endpoint type. This decision favors concrete capabilities and
port ownership instead. Costs include two constructors, explicit reusable structure
and a breaking migration. Neither approach is inherently faster merely because it
uses a protocol or composition; performance needs measurement.
