# Python graphs and Rust-native graphs

English | [简体中文](./zh-CN/native-graphs.md)

A Python declaration can mount a complete Rust-native subgraph. Both enter one
Rust CompiledExecutionGraph and share dependencies, hierarchy, failure propagation,
and the session's concurrency budget. Native subgraphs are not converted into
Python actions.

```text
Python Graph / Leaf
    ├── Python Leaf ──────────┐
    └── Graph(NativeGraphStructure)           │
          └── Rust Leaf / Arc      │
                                   ▼
                      Rust CompiledExecutionGraph
                          ├── Python leaf → Python driver
                          └── Rust leaf   → Rust worker
```

Python holds an opaque NativeGraphStructure, not individually callable Rust Leaf or
Rust action objects. Native action execution does not require a per-action Python
callback. The mixed driver receives batches of native completion events for
results, logs, and presentation. Internal nodes remain visible in compiled and
execution views; opacity does not hide them as a single undifferentiated action.

## Declaration boundary

Python actions retain their usual declaration:

```python
prepare = Leaf(prepare_source, label=("prepare"))
```

A Rust adapter constructs graph::GraphStructure. The Python adapter wraps it as
NativeGraphStructure; Python composes that body into a Graph:

```python
from rivetoo_ninja import import_clean_build_graph

ninja_graph = import_clean_build_graph(
    "build/build.ninja",
    shell="/bin/sh",
    env=build_env,
)
compile = Graph(ninja_graph, label="compile")
graph = (prepare >> compile >> install).graph()
```

This example requires the separate, experimental rivetoo-ninja package.
It is not included with Graph. The importer requires selected outputs to be absent:
it imports clean builds, not Ninja's incremental-build decisions. The host must
explicitly choose this adapter.

Here compile is an outer composite. Rust sees the internal nodes and edges for
global scheduling while preserving the composite boundary.

NativeGraphStructure is the smallest native unit exposed to Python. There is no public
Python NativeAction or ProcessAction API that would create a second per-action
submission path.

## Execution and results

Within one execution session:

- Python leaves are returned in batches to the Python driver, with arbitrary
  callables, Python results, and runtime-returned graphs.
- Rust leaves go directly to Rust workers and a native completion queue.
- The mixed driver receives native start, completion, and failure information.
- Both languages share total capacity; they do not each independently consume the
  full worker limit.

A successful native leaf currently returns None. It cannot read Python
NodeRunContext, return arbitrary Python objects, or produce dynamic graphs.
Supporting native results would require an explicit stable result protocol,
not object fields or implicit serialization.

## Rust crates

- action: native action trait, execution context, errors, and workers.
- graph: Rust Leaf, graph-owned arcs, GraphStructure, and GraphError.
- topology: integer nodes and pure graph algorithms.
- core: language-independent declaration compilation and execution sessions.
- python: integration of Python Graph and complete NativeGraphStructure declarations.

Adapters may implement arbitrary Rust actions, but Rust trait objects are not
passed between dynamic libraries. An independent adapter should provide a stable
batch graph specification, from which the Graph extension constructs NativeGraphStructure
once. This preserves package boundaries without routing each action through Python.

## Rust declaration API

The graph crate currently exposes flat Leaf/GraphStructure declarations and
compile_structure(), not a second scheduler. GraphStructure is the Rust type;
NativeGraphStructure is the Python-visible opaque wrapper. The Native prefix is
not part of the Rust graph API. Hierarchical Graph nodes and named interfaces in
this Rust declaration layer remain a separate extension, not a feature of this rename.
