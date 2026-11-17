# Node.js API

English | [简体中文](./zh-CN/node-api.md)

The experimental source package lives in `bindings/node`. It uses Node-API through
`crates/node`, not Python or a subprocess. It is private and not published to npm;
the current PyPI release does not include this adapter.

## Build and check

Use Node.js 22.13+ and Rust 1.95, the repository's current build toolchain.
The adapter requires Rust 1.88 or later; the dependency-free core still declares
its own Rust 1.85 minimum. From the repository root:

```sh
cd bindings/node
npm ci
npm test
npm run check
node examples/ports.mjs
```

`npm test` builds the Node extension and TypeScript declarations, then executes the
integration tests in a real Node process. `npm run check` checks both the frontend
and TypeScript usage examples. Local validation covers macOS arm64 with Node 26.7;
do not infer Windows/Linux Node validation from the Python wheel matrix.
The manual `Node-API checks` workflow prepares the same npm build/tests on Linux,
macOS and Windows. It neither publishes packages nor runs on ordinary pushes.

The build produces `native/rivlet.node` and `lib/`. The loader requires that exact
binary and checks its version against `package.json`; there is no Python or
JavaScript-only fallback. Do not mix artifacts from different revisions of the
private protocol, even if their package versions are equal.

## Actions, conditions and result edges

```js
import { Edge, Graph, Step, run } from './lib/index.js';

const enabled = new Step(() => true, { label: 'enabled' });
const plan = new Step(async () => ({ count: 21 }), { label: 'plan' });
const apply = new Step((_context, proposal) => proposal.unwrap().count * 2, {
  label: 'apply',
  condition: [value => value.unwrap(), enabled],
});
const workflow = new Graph({
  nodes: [enabled, plan, apply],
  edges: [Edge.results([plan], apply)],
  label: 'workflow',
});

const outcome = await run(workflow);
console.log(outcome.result(apply).unwrap()); // 42
```

Construction never invokes an action. An action receives `NodeRunContext` first,
followed by its ordered `Option<T>` result arguments. The context contains
`nodeId`, `nodePath` and `labelPath`; it has no cross-step result lookup API.
`nodeId` is a process-local `bigint`, while paths distinguish mounted occurrences.

`Edge.results([a, b], receiver)` passes the results of `a` and `b` in that order
and waits for them to finish. Exactly one complete result group may bind an action.
`new Edge(a, receiver)` adds waiting without adding an argument. A node condition
has its own sources, independent of action arguments. A source-free condition is
just a function. Conditions must return `boolean` or a promise of `boolean`.

Conditional waiting uses the same condition input convention:

```js
new Edge(checks, publish, {
  condition: [decision => decision.unwrap(), approval],
});
```

This always waits for `approval`. When the predicate is true, `publish` additionally
waits for `checks`; false removes that waiting relationship, not either action.

Every result argument is `Some(value)` or `Nothing`. A skipped producer yields
`Nothing`; a successful `undefined` return is `Some(undefined)`. `unwrap()` throws
on `Nothing`; inspect `.kind === 'some'` when absence is meaningful. Arbitrary JS
objects, functions, symbols and error objects stay in JS without serialization or
cloning. Successful values remain retained by the outcome; there is no early
consumer-based reclamation.

In TypeScript, the second `Step` generic describes the ordered source values:

```ts
import { Step, type NodeRunContext, type Option } from './lib/index.js';

interface Plan { count: number }
const apply = new Step<number, [Plan]>(
  (_context: NodeRunContext, plan: Option<Plan>) => plan.unwrap().count * 2,
);
```

These annotations do not validate arbitrary user payload schemas at runtime.
Applications remain responsible for domain validation.
Port types are inferred from actual interface endpoints, not caller-provided payload generics.
Use tuple entries to preserve exact named signatures; see the [typed port guide (Chinese)](./zh-CN/node-rust-advanced.md#typescript-结果边).
In TypeScript conditions, annotate callback parameters explicitly, for example
`condition: [(value: Option<boolean>) => value.unwrap(), enabled]`; an Edge does not
infer a callback's parameter schema from its runtime producer list.

## Subgraphs and public ports

Both `Step` and `Graph` are nodes. `Graph` directly holds reusable node, edge and
interface declarations, with a distinct identity, label, condition and interface view.
Only a Graph has public input and output ports.

```js
import { Edge, Graph, GraphInterface, Step, run } from './lib/index.js';

const source = new Step(() => 21);
const provider = new Graph({
  nodes: [source],
  interface: new GraphInterface({ outputs: source }),
});
const apply = new Step((_context, value) => value.unwrap() * 2);
const consumer = new Graph({
  nodes: [apply],
  interface: new GraphInterface({ inputs: apply, outputs: apply }),
});
const workflow = new Graph({
  nodes: [provider, consumer],
  edges: [Edge.results([provider.interface.output()], consumer.interface.input())],
});

const outcome = await run(workflow);
console.log(outcome.result(consumer.interface.output()).unwrap()); // 42
```

The shorthand declares anonymous ports. For anonymous and named ports together,
use maps, with `null` as
the anonymous key:

```js
new GraphInterface({
  inputs: new Map([[null, apply], ['prepare', prepare]]),
  outputs: new Map([[null, apply], ['plan', source]]),
});
```

`graph.interface.input()` is equivalent to `input(null)`; named access requires its
actual name. There is no automatic choice of a sole named port. An input can bind
multiple internal endpoints with an array; an output exposes one endpoint. Forward
a child port explicitly by placing its port reference in the parent's interface.
Input and output references are different TypeScript types, so their directions
cannot be reversed in an `Edge` call.

A structure can be mounted by two distinct Graph nodes. Their child declaration
objects are shared, but paths and result slots are not. Read an exposed output on
the chosen Graph instance; querying a shared inner Step without a mount is
ambiguous and raises an error. Endpoints must belong to the direct structure or
be reached through a child Graph's public port.

See [`examples/ports.mjs`](../bindings/node/examples/ports.mjs) for the complete
bidirectional subgraph example: provider plan → consumer apply → provider finish.
The graph-level connections go both ways, but the actual action dependency graph
must remain acyclic.

## Execution and concurrency

```js
const execution = new Execution(workflow);
const compilation = execution.compile();
const outcome = await execution.run({
  maxConcurrency: new Map([[workflow, 4], [downloads, 2]]),
});
```

Import `Execution` and supply a `downloads` Graph mounted inside `workflow`.
`compile()` caches the first successful compilation. `run()` reuses that exact
instance, and each Execution runs once. A new Execution can reuse the immutable
declaration without sharing mutable session state or results.

Limits refer to actual nodes. Rust infers ancestor membership from the graph and
admits an action only when all applicable budgets permit it. There is no implicit
action limit if `maxConcurrency` is absent, no second pool, and no reservation by
a waiting composite. `skip: [child]` explicitly skips a node and its descendants.

All JS callbacks execute on Node's event loop. Promise-returning actions can overlap;
synchronous CPU work does not become parallel. An application may return a promise
from work delegated to its own workers. Rivlet does not create a JS worker pool or
claim that a concurrency limit is a thread count.

The driver registers one completion handler per action/predicate and batches queued
notifications into the Rust session. It does not poll timers or reconstruct a
`Promise.race` over every running task after each completion.

## Failures

An action failure blocks its dependents, not unrelated branches. After independent
work finishes, `run()` rejects with `ExecutionError`. Its `.outcome` exposes all
terminal node states and direct core blockage witnesses:

```js
try {
  await run(workflow);
} catch (error) {
  if (!(error instanceof ExecutionError)) throw error;
  for (const { mount, outcome } of error.outcome.nodes()) {
    console.log(mount.labelPath, outcome.state);
  }
}
```

Import `ExecutionError` from the package. An action failure keeps its original thrown
object in `failure.error`; a composite failure refers to the failing child instead.
Predicate errors are available through `conditionError(index)` using the condition
index in the compilation. Missing results on failed/blocked nodes raise errors;
they are not disguised as successful `undefined` or skipped values.

## Cancellation

`execution.run({ signal: controller.signal })` accepts a native AbortSignal.
Actions cooperate through `ctx.signal`. Cancellation stops new dispatch, drains
in-flight Promises, then rejects with `ExecutionCancelled` and its partial outcome.
Real failures still produce ExecutionError; an AbortError name alone is not a
cancellation acknowledgement. See the [cancellation contract (Chinese)](./zh-CN/cancellation.md).

## Architecture and current scope

```text
JS Step / Graph / ports
    → scoped declaration indices
    → crates/node: Node-API conversion
    → existing core GraphDeclaration compiler
    → existing core ExecutionSession
    ↔ batched ready actions, predicates and completions
JS event loop + JS-owned result table
```

The adapter has its own Cargo workspace and lockfile, keeping Node dependencies
and its Rust minimum out of Python source distributions. Its path dependency still
uses the one shared core implementation. The adapter owns napi-rs, serde and
Node-API dependencies. Core remains independent
of third-party libraries and language runtimes. napi-rs registration macros need
their own generated FFI unsafe allowance; only this adapter uses `deny(unsafe_code)`
instead of the workspace's `forbid`. Handwritten adapter code contains no unsafe
blocks, and core's policy is unchanged.

This first frontend supports explicit constructors, hierarchical composition,
anonymous/named ports, result passing, node/edge runtime conditions, subtree limits,
skips, compilation reuse, failure isolation, static selection, runtime-generated graphs
and event observation. See the [Node/Rust advanced API guide (Chinese)](./zh-CN/node-rust-advanced.md)
for examples and boundaries. `node.wire({ after: [...] })` and `Graph.fromWiring(...)`
provide declaration-only wiring; see the [wiring guide (中文)](./zh-CN/node-wiring.md).
Live execution rendering, symbolic provider selection, and Rust-native action registration
are not exposed.
`run(node, { shared })` injects one host object; actions access it through
`ctx.shared(ExpectedClass)`, including generated children. See [shared state (中文)](./zh-CN/shared_state.md).
Structural export and Tree/Mermaid rendering use the shared Rust inspection layer;
see the [complete inspection examples](./zh-CN/graph_export.md#三端共用的结构渲染).

There is no mixed Python/JS/Rust value protocol, browser/WASM binding, npm release,
or prebuilt Node platform matrix yet. Reusing core does not imply these features
already exist. Python distribution and its existing API remain separate.
