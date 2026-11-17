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
  reusable Graph, typed interfaces/edges/predicates, and GraphError.
- `crates/execution` composes graph/action/core into a single-use Rust
  Execution with typed outcomes and synchronous/asynchronous predicate evaluation.
  It depends on no Python or third-party
  runtime; declarations and the mainline Python adapter do not depend on it.
  The experimental mixed adapter explicitly enables its coordinator feature;
  see [experimental boundaries](./mixed-execution.md).
- `crates/topology` uses only the Rust standard library for integer nodes,
  adjacency indexes, and DAG algorithms. It knows nothing about actions or domains.
- `crates/core` (package `rivlet`) depends on the workspace topology crate,
  not third-party libraries. It owns declaration compilation, port resolution,
  the compiled execution graph, and scheduling.
- `crates/python` is the private PyO3 adapter. It converts declarations into integer
  IDs in batches, retains Python action/predicate/observation side tables, connects
  host control.
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

Python Step/Graph declarations use role-based private compiler/session bindings:
CompiledExecutionGraph, DeclarationCompilation and ExecutionSession. Pure Rust
declaration and execution APIs remain independent.

The binary extension module is `rivlet._native`; Python graph declarations use the
core compiler and session without an action/value bridge to Rust declarations.

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

Python Step/Graph declarations enter the core compiler through batched PyO3 calls.
Core owns execution dependencies, hierarchy, readiness and state transitions.
The Python driver owns callables, borrowed pools, asyncio, objects and exceptions.
Python runtime conditions may return bool or an awaitable and do not consume action slots.

The driver exchanges batched work, condition requests and events through core's
start/advance API. Runtime-returned graphs use the same compiler; the host manages
dynamic sessions sharing its pool and event loop. A parent waiting for its returned
graph releases action admission. Explicit subtree limits and dynamic inheritance
use the existing core's shared counters.

Pure Rust Step/Graph declarations use the graph projection and the same compiler.
The execution crate supplies Execution::run/run_async, native workers and typed
outcomes, independently of Python. The Node.js frontend independently uses its JS
callbacks and event loop with the same core.

Mainline has no RustGraph, foreign-result codecs, SDK or cross-language
mixed execution entry. The complete experiment remains on the
codex/experimental-mixed-execution branch, not in the current mainline API.

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


Readiness, scheduling states, condition blocking, hierarchy lifecycle, execution
DAG validation, provider resolution, and binding lowering have one Rust
implementation. Wrapping every remaining Python host object in a native object
would add conversions rather than remove another scheduler.
