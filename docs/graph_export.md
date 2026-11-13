# Structured graph export

English | [简体中文](./zh-CN/graph_export.md)

`export_graph()` returns a detached, JSON-only inspection document from the actual
`Graph`, root `Node`, or `GraphCompilation`. It does not parse rendered text, infer relationships
from labels, execute actions, or include application state. The public schema is
[`GraphDocument`](../src/rivlet/structure/schema.py), a discriminated TypedDict union.
It has no HTTP, frontend, or Pydantic dependency and is not an executable input format.

## Declaration versus compilation

```python
import json
from rivlet import Graph, Leaf, Case, Execution, export_graph, static

graph = static.select(
    cases=(
        Case(True, Leaf(lambda: "private result", label=("keep"))),
        Case(False, Leaf(lambda: None, label=("drop"))),
    )
).graph()

root = Graph(structure=(graph).structure, label=("choose"))
declared = export_graph(root)  # no selection or static evaluator is run
execution = Execution(root)
prepared = execution.compile()  # static evaluation happens here, once
compiled = export_graph(prepared)  # no second compilation or evaluation
assert execution.compile() is prepared
json.dumps({"declaration": declared, "compiled": compiled})
execution.run()  # uses the same prepared structure
assert execution.compilation is prepared
```

For this example, selected fields are:

```json
{
  "declaration": {
    "schema_version": 2,
    "view": "declaration",
    "nodes": [{"id": "0", "label": "choose"}, {"id": "0.0", "label": "keep"}, {"id": "0.1", "label": "drop"}],
    "selections": [{"id": "graph:0/selection:0", "cases": [
      {"node": "0.0", "condition": true}, {"node": "0.1", "condition": false}
    ]}]
  },
  "compiled": {
    "schema_version": 2,
    "view": "compiled",
    "nodes": [{"id": "0", "label": "choose"}, {"id": "0.0", "label": "keep"}],
    "edges": [],
    "dependencies": [],
    "excluded_nodes": ["0.1"],
    "excluded_edges": []
  }
}
```

The excerpt omits common node and scope fields; the actual document always contains
all fields of its TypedDict variant. Declaration export retains every candidate,
including invalid zero/multiple-match selections that have not been compiled. It
does not call static evaluators or select cases. Opaque static rules are represented
by target references and condition-position IDs, not their arbitrary payload,
evaluator, description, or a guessed boolean. `Case` booleans are already explicit
declaration data and are retained.

Compilation export includes only statically retained nodes and edges. Excluded IDs
refer to the corresponding declaration, not runtime `skipped` events. Runtime
conditions remain unevaluated, so a compiled graph is not an execution trace.
Its condition table gives the actual session condition `index`, ID, and ordered
input node IDs. Node and edge guards refer to this table. Reusing a predicate or
condition tuple at several sites can produce multiple compiled instances.

## Identities and composition boundaries

- Node `id` is `str(NodePath)`, such as `0` or `2.1`. It identifies a mounted position,
  not a label, function, process-global object address, or permanent business entity.
- Reusing the same child Graph creates different mounted IDs, for example `0.0`
  and `1.0`. Export and runtime events use the same mounted NodePath namespace.
- Node `node_id` is the declaration's process-local integer identity. Repeated
  mounts share it; recompiling the same declarations preserves it. It is not a
  persistent identity across process restarts or a cache key.
- Keeping the same mounting layout preserves IDs when labels change. Changing
  ordering or composition can change IDs. Pair an ID with its frozen document/run,
  not a graph produced by another source revision.
- `label_path` follows mount labels. The root scope has ID `graph:root`; each child
  scope has `graph:<owner NodePath>`. Scope `owner` and local `nodes`, together with a
  subgraph node's `child_graph`, preserve composition rather than inventing no-op actions.
- Interfaces retain their port names and exact input/output node references in `ports`.
  Port forwarding appears on the scope that explicitly exposes it.

Node kinds are leaf/subgraph; an opaque Rust body's actions are also leaf nodes.
A result edge through ports retains source/target component IDs and, as applicable,
source_port/provider_node or target_port/consumer_nodes. Resolved fields identify
actual internal endpoints, including forwarded ports.

Mounted Node labels and port names are deliberately visible. Hosts must
not put secrets in these display/contract names. Unnamed nodes use their positional
IDs; the exporter never obtains a label from callable `repr()`.

## Edge semantics

Both views retain declaration edge IDs and distinguish:

| `kind` | Meaning |
| --- | --- |
| `order` | An explicit completion dependency, without result transfer |
| `result` | An action result argument at `argument_index`; condition sources are in the condition table's `inputs` |
| `conditional` | A dependency guarded by the referenced runtime condition |
| `port` | A connection with `source_port`, `target_port`, an optional `condition` ID, and exact `provider_node` / `consumer_nodes` readiness endpoints |

`argument_index` is generated from the source order in `Edge.results()`; it is
inspection metadata, not a user-supplied wiring parameter. Result edges always
target the action, so no separate receiver field is needed.

Compiled `dependencies` is a separate view of the Rust compiler's actual prerequisite
relationships: `required` or `conditional`. A port connection can therefore show
component `0 → 1` in `edges`, but endpoint `0.0 → 1.1` in `dependencies`; it does not
make the entire consumer wait for the entire provider. Result and port provenance
is not guessed from `after` or reclassified as order-only edges.

To interpret scheduling, also retain condition `inputs` and scope ownership; the
prerequisite list alone is not a replacement for these contracts. Static edge removals
are recorded by the compiler during its original evaluation, not reevaluated by export.

## Freeze history separately from runtime state

Persist the returned document with the request/history record that used it. For
example, serialize `export_graph(execution.compile())` before calling `run()`.
The returned lists/dicts are fresh copies; changing them does not change the graph
or later exports. JSON serialization freezes their values for that historical record.

`schema_version=2` identifies the document format, not a package version, workflow
revision, run ID, or fingerprint. Hosts should store their own run/revision identity
alongside it. Do not regenerate an old history's topology from today's source graph,
add nodes that never existed, or relabel unknown historical work as executed.

Version 2 uses edge kind `port`, separate `source_port` and `target_port` names,
and a `condition` field that is null for an unconditional port connection. Scope
port declarations still use a single `port` name. Version 1 used `capability`
for the edge kind and common name. Consumers must check the schema version
rather than interpreting the old document as the new format.

Events and execution snapshots remain separate. Join them with the exact frozen
node ID; do not import arbitrary return values, shared objects, exception messages,
log paths, command lines, environments, or function objects into this document.
Rust-native subgraphs expose declaration identities, labels and topology, never their process details.

Each Execution binds its declaration and host references immutably. Its first
successful `compile()`/`run()`/`arun()` fixes the root compilation; subsequent calls
reuse it. Failed compilation commits nothing and can be retried. This is preparation
reuse, not action/result caching or permission to reuse the single-use execution state.
Use a new Execution to reevaluate static choices. Standalone `compile_graph()`
still produces a fresh compilation and does not prepare another execution.

## Limits and delivery

Export reflects the supplied scope, not future work. A Graph returned by an action
cannot be known before that action runs; when such a child is actually compiled,
export that child compilation with its existing parent NodePath prefix. The root
document is not silently rewritten or presented as an all-knowing execution history.
For such a mounted export, `parent` names the external owner and `root` is
`graph:<parent NodePath>`; that owner is outside this document's nodes. A standalone
root has `parent=null` and `root="graph:root"`. Scope and edge IDs therefore keep
their mount namespace when independently exported child documents are correlated.

Exporting a manually assembled GraphCompilation without compiler provenance raises
GraphError rather than recompiling or guessing missing edge kinds. Use compile_graph
or Execution.compile to obtain a compiler-produced result.

This is a new source-level API, absent from PyPI 0.2.0.post1 and from the original
0.3.0 preparation commit. Integration requires an explicitly validated build containing
this change; updating a consumer's installed Rivlet is a separate deployment action.
No JS binding, cross-language executor, service, compatibility reader, or publication
is implied by exporting JSON. The Python export projection has no new dependencies;
only the existing Python adapter gains compiler provenance and safe native inspection.

Export cost is proportional to mounted nodes, edges, ports, condition/input references,
and total path/output size. It performs no action I/O and does not affect scheduling.
