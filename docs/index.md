# Rivlet

English | [简体中文](./zh-CN/index.md)

`rivlet` is an explicit, composable DAG declaration and execution library with Python
and Rust declaration APIs.

## Features

- Directed topology and DAG validation.
- Node and Graph declarations with explicit wiring.
- Sequential `>>` and independent `|` composition.
- Exactly-one static selection before graph construction.
- Graph-internal static selection with exactly one true condition.
- Static conditions, runtime conditions, and conditional edges.
- Subgraph boundaries and interface ports.
- One Rust compilation/execution path with dynamic and compiled presentation modes.
- Mixed synchronous/coroutine execution with managed or borrowed Python thread pools.
- Optional execution-level subtree concurrency limits.
- Execution control, results, observation events, and rendering.

## Design goals

Step and Graph are actual nodes; GraphStructure holds reusable structure.
Execution owns one run. Declarations reference actual nodes. Internal `NodePath` values are assigned to mounted
nodes, while labels serve presentation only.

Dependencies, result edges, and port connections participate explicitly in
topology validation. Composition preserves subgraph boundaries rather than
silently flattening declarations. Host logging, terminals, request state, and
domain workflows are connected explicitly, not read from implicit global facilities.

## Documentation

- [Core model](./core_model.md)
- [Versioned structural graph export](./graph_export.md)
- [Static selection during compilation](./static_selection.md)
- [Topology complexity and implementation costs](./topology_complexity.md)
- [Execution semantics](./execution.md)
- [Python graphs and Rust-native graphs](./rust-graphs.md)
- [Running Rust graphs](./rust-execution.md)
- [Typed native result values and language conversions](./rust-results.md)
- [Result recording, lifetime and future retention candidates](./result_lifetime.md)
- [Conditional edges](./conditional_edges.md)
- [Conditional steps](./conditional_steps.md)
- [port dependencies and subgraph ports](./interfaces.md)
- [Rust architecture and language boundaries](./rust-architecture.md)
- [Local development and checks](./development.md)

## Historical benchmarks

These records preserve measured workloads and results, not the current API or a
performance guarantee for the latest release.

- [Hierarchical graph benchmark](./recipe_scale_benchmark.md)
- [Node overhead benchmark](./step_overhead_benchmark.md)
- [Workflow runtime and concurrency benchmark](./workflow_runtime_benchmark.md)

## Core boundaries

| Type or field | Responsibility |
| --- | --- |
| `Graph` | Composite node with identity, lifecycle, and a reusable body |
| `Execution` | A declaration and one execution scope |
| `Node` / `NodeKind` | Shared Step/Graph protocol / closed executable union |
| `GraphStructure.edges` | Explicit wiring within a reusable structure |
| `Node.wire(after=(Results(...),))` / `Edge.results(...)` | Ordered action results; conditions declare their own sources |
| `NodeWiring` / `GraphWiring` | Declaration-time composition |
| `GraphCompilation` | Runtime execution structure and composite boundaries |

## Outside Graph core

Rivlet does not interpret application payload schemas, business policies, or persistence
formats. Applications supply actions and predicates and explicitly connect shared state,
logging, events, and cancellation. These interfaces do not make application-specific
policies part of the graph model.

## Current limitations

- Runtime-created graphs receive nested sessions and inherit explicitly configured
  ancestor concurrency limits. There is no implicit action quota; work spawned
  inside an action is outside graph admission limits. See the
  [subgraph comparison](./execution.md#declared-versus-runtime-returned-subgraphs).
- `ExecutionState` is single-use, with no reset or reuse operation.
- The runtime does not aggregate host-domain results.
- Runtime-generated subgraphs cannot be compiled in advance. Once returned, they
  enter the same Rust compiler used for statically declared graphs.

## Languages

Node failure provenance and event delivery rules are documented in
[failure events](./failure_events.md).

For distribution coverage and the PyPI workflow, see [releasing](./releasing.md).

English is the default. Chinese pages live in [zh-CN/](./zh-CN/index.md), with the
same filenames and a language link on each page. Update both versions when
changing API semantics, examples, or limitations. Historical benchmark samples
remain tied to their recorded dates and workloads.
