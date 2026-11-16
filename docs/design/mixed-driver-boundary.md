# Mixed execution driver boundaries: discussion

English | [简体中文](../zh-CN/design/mixed-driver-boundary.md)

This is an experimental-branch design record, not an implemented driver contract.
Mixed execution, the extension SDK, bridge and coordinator are kept together on
the experimental branch; the mainline does not expose Python/Rust mixed execution.
Pure Rust execution and the independent Node.js frontend are separate capabilities.

## Current implementation

The SDK exports a concrete Step or Graph through `export_node`. A Graph exported
by the current Rust SDK has Rust actions and predicates throughout: the SDK has no
Python-action injection API. Python can compose that node into an outer workflow.
Reuse of `.structure` creates new Graph declarations, not implicit copies of root
identity or condition. A factory is optional; an extension may export node objects
as module attributes.

Actions execute in Rust, and Rust-to-Rust values remain native. Nevertheless, the
Python host driver still calls `start/advance`, waits for native completion, and
calls `advance` again to dispatch the next action. Importing a complete Graph does
not currently make its native execution autonomous. Graph import happens once;
execution can still involve multiple host exchanges.

```text
Python start → Rust read → notify Python
Python advance → Rust decode → notify Python
Python advance → Rust validate → notify Python
```

The existing standalone Rust `Execution::run` has its own synchronous Rust driver.
It does not execute Python actions and does not depend on the Python host.

## Why the host driver exists

The earlier boundary put topology, readiness and scheduler state in core, while
Python managed arbitrary callables, borrowed thread pools, asyncio, ContextVar,
exceptions, results and runtime-returned graphs. The host exchanges batches with
the core rather than having a Rust worker call Python separately for every action.
The private Tokio callback experiment is not evidence that every Rust driver is
slower; it only investigates that particular per-action callback design.

Native actions introduced a different cost: an entirely Rust dependency chain
still requires Python to advance the shared session between completions.

## Candidate designs

An autonomous native pump underneath the Python driver would remove native-chain
handoffs, but retain two cooperating drivers. It must not be described as one
unified driver merely because there is only one core scheduler.

A genuinely unified Rust driver would own session progression, completion handling,
admission and dynamic-session relationships. Language adapters would execute their
own callables and report outcomes through batched requests and responses. Python
would retain its own event loop and host objects, but not decide graph progression.
One driver does not mean one thread or the absence of a host event loop.

Neither candidate has been implemented. If revisited, converge the existing Rust
driver and coordinator rather than adding a third long-lived driver implementation.

## Requirements and risks

- Keep the existing core graph model and scheduling algorithm. Driver policy belongs
  in execution, with language-specific objects and conversions in adapters.
- Do not turn a native Graph into an atomic black-box action. Preserve individual
  nodes, limits, failures and port readiness. A Python consumer of a decoded output
  may start while a later Rust validation action is still running.
- Do not wait for native work on the asyncio event-loop thread. Releasing the GIL
  does not free that thread. Thread ownership is a separate runtime decision, not
  an automatic consequence of implementing the driver in Rust.
- Keep Python ContextVar, coroutine, exception and object ownership semantics.
  Lazy conversions happen only for actual consumers and failures belong to them.
- Preserve shared admission, cancellation, skip and dynamic-child inheritance.
  A parent action awaiting its returned graph must not retain an action slot.
- Use completion/control notifications rather than timer polling. Batch observer
  events without dropping or reordering them; do not require a Python round trip
  solely to admit the next native node.
- Do not assume a throughput win. Compare native chains, small Python workflows,
  truly mixed graphs and asynchronous workloads, including handoff counts and
  cancellation/observer latency. Pure Python workflows must not unconditionally
  acquire a new native pool or background thread.

## Not runtime embedding

The SDK exposes native Rivlet nodes; it does not embed CPython or Node.js. Combining
a Python-only library with a Node.js-only library inside one process needs a
separate runtime bridge. Ordinary library calls do not require the graph SDK;
exporting nodes is useful when internal dependencies and progress should remain
visible to Rivlet.
