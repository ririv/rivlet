# Node modeling decision

English | [简体中文](../zh-CN/design/node-modeling.md)

This records the adopted direction; the specification is in the [core model](../core_model.md).

## Problem in the wrapper design

The former `Step(node=Leaf | Graph)` put identity, label and condition on a wrapper
and made it every edge's endpoint. Graph owned its interface on another layer.
This unified lifecycle, but hid concrete capabilities: `(step, "plan")` appeared
to reference a port even when that Step held a leaf. Forwarding an original graph's
interface could also lose which mount was intended. Construction shortcuts reduce
spelling but do not resolve that ownership gap.

## Adopted model

Leaf and Graph are actual nodes, each composing common metadata. Only Graph exposes
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

Reusing a structure does not require guessing the mount. Leaf does not gain an
interface through wrapping. Forwarded port references retain their owning graph.

## Composition and trade-offs

| Former form | Current form |
| --- | --- |
| Step(action, label=...) | Leaf(action, label=...) |
| Step(graph, label=...) | Graph(structure, label=...) |
| Graph(steps=..., edges=...) | Graph(GraphStructure(nodes=..., edges=...)) |
| wiring.step(label=...) | wiring.graph(label=...) |
| StepWiring | NodeWiring |
| StepRunContext / step_id / step_path | NodeRunContext / node_id / node_path |
| NativeGraph (opaque body) | RustGraphStructure |

Recipe functions still return Graph. Execution receives that actual node directly.
Events and mounted identity types likewise use the Node prefix.

A protocol is not implementation inheritance. Common metadata remains composed
into concrete types; the compiler still uses leaf/composite sum variants. Node
supplies no mutable base implementation and does not register arbitrary node kinds.

The former wrapper was not universally inferior: it centralized lifecycle metadata
and provided one endpoint type. This decision favors concrete capabilities and
port ownership instead. Costs include two constructors, explicit reusable structure
and a breaking migration. Neither approach is inherently faster merely because it
uses a protocol or composition; performance needs measurement.
