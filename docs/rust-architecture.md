# Rust architecture and language boundaries

English | [简体中文](./zh-CN/rust-architecture.md)

Declaration APIs are available in Python and Rust. Production execution uses a Rust declaration
compiler and scheduling state machine. Rust owns the compiled graph, scheduling
indexes, and session state; Python retains arbitrary callables, results, and host
integration.

## Crate boundaries

- `crates/action` defines typed Action inputs/outputs, Rust-owned values,
  stable errors, and process actions without
  Python callbacks. It does not depend on PyO3, Tokio, or Graph core.
- `crates/graph` exposes actual Rust Step/Graph nodes, the common Node trait,
  reusable GraphStructure, typed interfaces/edges/predicates, and GraphError.
  Adapters connect complete graphs rather than exposing individual actions to Python.
- `crates/execution` composes graph/action/core into a single-use synchronous Rust
  Execution with typed terminal outcomes and synchronous predicate evaluation.
  Its independent `coordinator` module also supplies native execution coordination
  for language adapters. It depends on no Python or third-party runtime;
  declarations do not depend on it, while the Python adapter uses the coordinator.
- `crates/topology` uses only the Rust standard library for integer nodes,
  adjacency indexes, and DAG algorithms. It knows nothing about actions or domains.
- `crates/core` (package `rivlet`) depends on the workspace topology crate,
  not third-party libraries. It owns declaration compilation, port resolution,
  the compiled execution graph, and scheduling.
- `crates/bridge` defines a versioned C ABI for independent native libraries;
  only this transport owns serde and scoped unsafe pointer code. Core remains
  standard-library-only and forbids unsafe code.
- `crates/python-sdk` (package `rivlet-python`) exports native bodies through a
  capsule into the host. It does not depend on the main Python adapter crate.
- `crates/python` is the private PyO3 adapter. It converts declarations into integer
  IDs in batches, retains Python action/predicate/observation side tables, connects
  host control, and dispatches native actions to their Rust implementation.
- `crates/node` is the experimental Node-API adapter in its own Cargo workspace
  and lockfile, excluded from the Python distribution workspace. It owns napi-rs/serde and
  converts JS declarations and completion batches into the existing core compiler
  and session. `bindings/node` retains JS callbacks and arbitrary result objects;
  no Python bridge is involved. See [the Node.js scope](./node-api.md).
- `crates/tokio-runtime` is a private experiment, not production API. It contains
  the Tokio dependency and measures per-action callbacks into Python through the
  project's CallbackHost interface.

PyO3 and third-party runtime types do not appear in core's public data model.

## Naming at language boundaries

Rust public types describe their role, not their implementation language:
`action::Action`, `ActionContext`, `ActionHandle`, `ActionError`, `ActionExecutor`,
`Value`, and `ValueType`. The ordered input protocol is `ActionInputs`, distinct
from an action's `Inputs` associated type.

Python uses the same Step/Graph node types for Python declarations and SDK imports.
The SDK exports concrete nodes; an imported Graph's `.structure` remains the single
public `GraphStructure` type with closed Python/native storage. Execution adapter records such as
`CompiledRustStep` and `RustActionBindings` retain an ownership qualifier. Private
compiler/session bindings use their role: `CompiledExecutionGraph`,
`DeclarationCompilation`, `ExecutionSession`, and `MixedExecutionSession`.
The lazy Rust result view is private `_RustResult`; application code uses the public
result APIs rather than handling this adapter wrapper.

The binary extension module is `rivlet._native`. Structural export keeps its
versioned `action_kind="native"` wire tag independently of declaration type names.

## Shared native execution coordination

This module belongs to the [experimental mixed-execution feature](./mixed-execution.md).
It is behind the non-default `experimental-mixed-execution` Cargo feature.
Ordinary Python sessions bypass it through the core-only host adapter.

`execution::coordinator::Coordinator` owns an existing core ExecutionSession,
native action workers, completion accounting, native values and synchronous
predicate dispatch. It does not create another scheduler: core still decides
readiness, admission, skips and failure propagation.

ActionDispatch and ConditionDispatch explicitly distinguish Host and Native.
The coordinator returns only host-owned action/predicate requests, ordered events,
native results and typed action/condition failures. HostValues converts a successful
host result only when a native receiver needs it; InputSite identifies that concrete
action or predicate. Skipped sources bypass conversion and remain absent.

The Python adapter retains PyO3 objects, codecs, lazy result wrappers and asyncio
notification. Native predicates execute outside the GIL; the host-value converter
briefly attaches only when it needs a Python object. With no native predicate,
ordinary host dispatch does not release/reacquire the GIL unnecessarily. Pure host
sessions create no native worker pool or CPU-capacity probe.

The same coordinator can support a Node adapter without importing Python types.
That Node integration is not implemented. The synchronous Rust Execution::run
entry remains a separate host driver over the same core/action primitives;
this extraction migrates the Python mixed path, not that high-level Rust driver.
See the runtime-independent tests in
[`crates/execution/tests/coordinator.rs`](../crates/execution/tests/coordinator.rs).

## External-library boundary

External graph, scheduling, or concurrency implementations belong in separate
adapter crates. An adapter owns the dependency and connects through project-defined
interfaces:

```text
core scheduler interface
        ↑ project-owned model
executor adapter
        ↓
third-party runtime
```

Third-party node, task, error, and executor types must not leak into Graph's public
model. Replacing an adapter should not require changing graph declarations.

## Production execution

Python Step/Graph are concrete nodes; Node is their protocol, not a wrapper.
GraphStructure holds the reusable body. PyO3 collects these nodes directly into
existing Rust step/composite variants, without a second scheduler or implementation
inheritance. Rust NodeIdentity is the process-local declaration identity; indexed
NodeId is the compiler/topology reference. Rust graph::GraphStructure holds a closed
NodeKind union of Step/Graph. A Graph has its own identity and typed interface,
while its body can be shared with Arc. Projection resolves mounted scopes and
ports and calls the existing GraphDeclaration compiler. Python's GraphStructure
wraps either a Python declaration or an opaque native body; a Graph node composes
that reusable body. Detached structural snapshots do not expose executable Rust
objects. See [the independent-extension SDK](./python-native.md).
Native composites have no action handle, not a synthetic no-op. See the
[Rust declaration API](./rust-graphs.md#rust-declaration-api) for examples and limits.

Pure Rust hosts can use [Execution::run](./rust-execution.md) instead of writing
their own session/worker loop. It uses the same core session and action executor;
the mixed-language Python driver remains independent. Rust predicates stay in the
Rust projection side table and are evaluated by this host, outside action worker
capacity. Python mixed dispatch also evaluates native synchronous predicates
directly, releasing the GIL around evaluation. See [Rust runtime predicates](./rust-conditions.md).

All production paths share the same compiler and session implementation:

```text
Python Graph
    ↓ one PyO3 compilation call
Rust GraphDeclaration
    ├── static pruning
    ├── hierarchy and declaration scopes
    ├── explicit port-port location
    └── conditions and prerequisites
    ↓
Rust CompiledExecutionGraph
    ├── hierarchy lowering
    ├── contiguous NodeId / GraphId
    ├── membership → owners / roots / children
    ├── reads, prerequisite, and dependent indexes
    ├── port connection lowering
    └── DAG validation
    ↓
Rust ExecutionSession
    ├── Python leaves → batched Python driver
    │                    ├── arbitrary callables
    │                    ├── ThreadPoolExecutor
    │                    └── asyncio tasks
    └── native leaves → Rust workers → completion queue
```

Production runtime does not call back into Python per action. Static condition
leaves do call Python during compilation. At runtime, start/advance exchanges
batches when state changes:

Python runtime predicates can return bool directly or await a boolean. The Python
driver returns synchronous resolutions immediately and tracks awaitable resolutions
on the same loop as coroutine actions. Core retains the outstanding request until
its response arrives, even when the last ordinary action has already completed.
Predicates do not become action nodes or consume action admission slots.

- Ready Python actions, predicates to evaluate, and lifecycle events go to Python.
- Native action handles remain in Rust after graph registration.
- Python completions, predicate results, and explicit skips return in batches.
- Native completions are collected in batches and advance the same session.
- Rust results are stored before advancing completion. Native consumers share
  those values directly; language converters and lazy Python views belong to the
  Python adapter, not core. See [native result values](./rust-results.md).
- There is no timer-based polling loop; idle drivers wait for actual work completion.

Known subgraphs compile as composites and leaves without losing their boundaries.
A graph returned by an action is compiled later with the current path and ancestor
scopes, in a nested Rust session. The Python driver coordinates active sessions
using the same Python pool and, when needed, event loop. There is no implicit
global action budget. Explicit `max_concurrency` scopes are checked by Rust before
dispatch and inherited by runtime children. Waiting parent actions consume no worker
or admission slot. Python-only sessions create no empty Rust worker pool. See
[execution limits](./execution.md#optional-subtree-concurrency-limits).

Dynamic and compiled modes retain different presentation/composite-recording
behavior, not different execution engines.

## Runtime experiment

The private `crates/tokio-runtime` and `benchmarks/runtime_drivers.py` compare
batched execution with per-action Tokio/PyO3 callbacks. They are not production
APIs. Historical measurements are recorded in the
[benchmark notes](./recipe_scale_benchmark.md#runtime-driver-experiment).

## Single sources of truth

Lowering receives graph scopes and node membership rather than independent owner,
roots, and children declarations:

- Scope: Root or OwnedBy(NodeId).
- Node: Step or Composite with child_graph.
- Every node belongs to one GraphId.

Rust validates parent/child graph consistency, derives owners from membership, and
derives roots and composite children from owners. Port connections are separate
inputs lowered into ordinary prerequisites. Python presentation recovers final
prerequisites rather than implementing another rewrite rule.

Prerequisites are unconditional or conditional variants. Runtime conditions use
ConditionId; predicates remain in their host's side tables (Python, Node.js, or
native Rust), while core records reads. Core can therefore detect blocking and
cycles without interpreting language-specific closures.

## Domain boundary

The core does not interpret application payloads or implement business-specific
resolution and persistence policies. Actions and predicates are supplied by the
application. Python's generic topology API and the Rust topology crate
are separate implementations; see [topology complexity](./topology_complexity.md).

## Python boundary

Port resolution validates Python port ownership, encodes components,
ports, requirements, and selections as integers, and calls Rust once.
Provider indexing, uniqueness, explicit selection, and duplicate/mixed requirement
validation have no second Python solver.

Python retains host-facing objects:

- Static step evaluators and runtime predicates.
- Actions, results, observation, labels, and log paths.
- Compilation side tables for actions, predicates, observations, paths, and scopes.
- Execution state snapshots, explicit skips, and results.

Native actions form a separate binding branch, both for imported Steps and inside
native GraphStructure bodies. Their typed
results stay native on Rust edges. Only the Python adapter converts values when
crossing the language boundary; process-action unit maps to Python None.

Readiness, scheduling states, condition blocking, hierarchy lifecycle, execution
DAG validation, provider resolution, and binding lowering have one Rust
implementation. Wrapping every remaining Python host object in a native object
would add conversions rather than remove another scheduler.
