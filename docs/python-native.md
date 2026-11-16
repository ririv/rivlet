# Independent Rust extensions in Python workflows

English | [简体中文](./zh-CN/python-native.md)

> **Experimental:** mixed execution, the SDK, ABI bridge and coordinator are one
> feature family and may change or be removed. No experimental namespace is required.
> See [status and removal boundaries](./mixed-execution.md).

This SDK is available in the current source tree, not the released 0.4.0 host.
Build the matching host extension before using it. The SDK crates are not yet
published to crates.io; the example uses explicit path dependencies.

## Export concrete nodes

The SDK has one export entry: `rivlet_python::export_node(py, node)`. It accepts a
Rust Step or Graph and returns the corresponding ordinary Python `rivlet.Step` or
`rivlet.Graph`, with root label, condition and declaration identity retained in
the host's identity space. Actions and predicates remain Rust-owned. There is no
public RustNode, RustGraphStructure, NativeAction wrapper or separate structure
export entry. Imported steps join the native driver, not Python callable execution.

An extension may register the resulting object as a module attribute:

```rust
module.add("double", export_node(module.py(), step)?)?;
module.add("pipeline", export_node(module.py(), graph)?)?;
```

Python directly uses `demo_native.double` or `demo_native.pipeline`. A factory is
only needed when the extension wants fresh declarations or constructor arguments;
it is not required by the SDK. A Step has no graph interface or structure.

To reuse a Graph body, use its existing `.structure`:

```python
left = Graph(pipeline.structure, label="left")
right = Graph(pipeline.structure, label="right")
```

These are distinct declarations sharing one body, not copies of the exported root
identity, label or condition. Declare any new root condition explicitly.
To deliberately retain an imported root predicate on a new node, use the existing
`condition=native.condition.as_input()` when the condition is present. Its predicate
stays Rust-owned; it is not wrapped in a Python callable. Exported node declarations
must not reference undeclared sibling nodes; export their enclosing Graph instead.

`pipeline.structure.describe()` returns a detached declaration document using the existing
[structural export schema](./graph_export.md). `structure.nodes` and
`structure.edges` return tuples of node/edge descriptions, including nested
declarations, not language-specific executable objects. Inspection neither compiles
nor evaluates static rules; modifying a returned dictionary does not modify the
declaration. Use one `describe()` call when inspecting multiple fields.

Pass `interface=` when declaring a Python body; obtain mounted endpoint references
from `graph.interface`. An opaque native body exposes only its declared interface
for external wiring. Python cannot extract its Rust callable objects.

## Run the real example

The standalone [example extension](../examples/python-native/src/lib.rs) defines
typed Echo and Double actions and a synchronous predicate. Its exported
`pipeline` attribute is an actual public Python Graph exported with
`rivlet_python::export_node`; `double` is an exported Step. The optional
`make_graph()` factory creates a fresh Graph declaration using the same export API.

With the current Rivlet host installed and Maturin available:

```sh
just test-native-bridge
# Or select an explicit Maturin executable:
python scripts/check_native_bridge.py --maturin /path/to/maturin
```

The check builds the independent extension with its own Cargo.lock, extracts its
wheel into a temporary directory, runs the integration tests and the demo, then
removes only that temporary directory. It does not install the demo into the host
environment. Python, pytest, Rust and Maturin are explicit prerequisites.

The complete Python composition is:

```python
from demo_native import pipeline
from rivlet import Edge, Graph, GraphStructure, NodeRunContext, Option, Step, run

def show(_ctx: NodeRunContext, value: Option[int]) -> None:
    print("Python received:", value.unwrap())

prepare = Step(lambda: 21, label="prepare")
native = pipeline
display = Step(show, label="display")
workflow = Graph(
    GraphStructure(
        nodes=(prepare, native, display),
        edges=(
            *Edge.results((prepare,), native.interface.input),
            *Edge.results((native.interface.output,), display),
        ),
    ),
    label="mixed",
)
run(workflow, max_workers=2)
```

Output: `Python received: 42`. Rust checks the input is positive before doubling
it. A negative input skips Double; its consumer receives Nothing, not a successful
unit value. The integration tests also exercise serial execution, arun, borrowed
thread pools, conditional completion/port edges and repeated mounts.

The extension's [type stub](../examples/python-native/demo_native.pyi) declares
`pipeline: Graph`, `double: Step`, and `def make_graph() -> Graph`.
PyO3 returns a Python object handle,
not a Rust GraphStructure layout shared with the host binary.

## Ownership and ABI

- `crates/bridge` owns the versioned C-layout header and callback tables.
- `crates/python-sdk` (package `rivlet-python`) provides only `export_node`.
- The host adapter imports metadata in a batch and connects native callbacks to
  the existing compiler and execution session. Python uses the language-independent
  `execution::coordinator` for native workers, values and predicates; PyO3 objects
  and conversions remain in its adapter. It does not add another scheduler.

Metadata uses a typed JSON descriptor once at import. Actions, predicates and result
payloads are not JSON. Rust trait objects, Arc layouts, TypeId representations and
allocator ownership never cross the dynamic-library boundary. The importer checks
the ABI version, header/table sizes, structural references and endpoint ownership.
Callback panics become explicit errors; this is not a native-code sandbox.

Each origin library owns its graph/value allocation and its release callback.
Graph handles and retained results keep an origin lease until their last reader
finishes. The bridge assumes the same process/architecture and a live Python
extension library; it is neither an IPC protocol nor an explicit library-unload API.

## Values and conditions

Builtins support unit, bool, integer and float primitives, UTF-8 strings and bytes.
Across libraries, numeric wire values use little-endian binary, strings UTF-8 and
bytes their raw contents. Conversion occurs only when an actual reader needs it.
Skipped results remain absent; successful unit remains present.

Custom `Send + Sync + 'static` payloads need neither Clone nor serialization for
native consumers in the same extension provider. The example sends one non-Clone
payload to two arguments and verifies Arc::ptr_eq inside Rust. Another copy of the
library is a different provider: identical Rust type names do not establish type
compatibility.

The SDK currently has no registration API for custom cross-provider or Python
converters. Such use fails explicitly instead of guessing a representation. Builtin
values can cross Python and independent Rust extensions; arbitrary Python objects
remain Python-owned.

Rust synchronous predicates execute directly in mixed dispatch, with the GIL
released around evaluation. Node, Graph, completion-edge and port-edge uses keep
their own mounted inputs and decisions. They should be short, because they run on
the driver rather than an action worker. Python predicates may still be async.

This SDK does not add Rust async predicates, runtime-created Rust graphs, or
registration of native actions/predicates in the Node.js frontend. It also does
not establish a performance guarantee; benchmark the actual workflow.
