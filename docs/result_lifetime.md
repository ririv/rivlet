# Result recording and lifetime

English | [简体中文](./zh-CN/result_lifetime.md)

Result transport and post-run inspection are different needs. Transport needs a
source value while a consumer can still use it; inspection needs the value after
execution. The current Python driver uses the same ResultStore for both. It has
no selectable retention policy or per-consumer reclamation.

## Current Python recording rules

| Execution input | Python result recording |
| --- | --- |
| Order-only or readiness edges, with no result inputs or explicitly supplied store | Not enabled by default |
| Result inputs to a Python action | Enabled automatically before the session executes |
| Result inputs to a node condition or conditional-edge predicate | Enabled automatically before the session executes |
| Python results needed as Rust action inputs | Enabled automatically before the session executes |
| Only Rust-to-Rust result inputs | Native transport does not by itself enable a Python ResultStore |
| Explicit `ExecutionState(results=ResultStore())` | Enabled explicitly |

Recording is execution state, not a property of one source or consumer. Once it
is enabled, subsequent successful Python action returns are recorded by mounted
NodePath, **including returns that no result edge consumes**. This is not a
selective cache of only the referenced producers. Enabling recording later does
not reconstruct values that were previously discarded. A returned subgraph is
executed, not stored as an ordinary action payload.

For example, one result edge also enables recording of `unrelated`:

```python
from rivlet import Edge, Execution, Graph, GraphStructure, Step, run

source = Step(lambda: "plan", label="source")
consumer = Step(lambda _ctx, result: print(result.unwrap()), label="consumer")
unrelated = Step(lambda: "not consumed", label="unrelated")
workflow = Graph(
    GraphStructure(
        nodes=(source, consumer, unrelated),
        edges=Edge.results((source,), consumer),
    )
)
execution = Execution(workflow)
run(execution, max_workers=2)
print(execution.require_result(unrelated).unwrap())
```

This prints `plan` and then `not consumed`. The final query reads an already
recorded value; it does not run that action again.

To request post-run inspection without any result edge, supply the store
explicitly using the current API:

```python
from rivlet import Execution, ExecutionState, ResultStore, Step, run

source = Step(lambda: "inspect me", label="source")
execution = Execution(
    source,
    execution_state=ExecutionState(results=ResultStore()),
)
run(execution)
print(execution.require_result(source).unwrap())
```

Without the explicit store or an automatically enabling input, that query fails
with `graph execution did not enable graph results`. It does not silently return
None. A successfully recorded return of None is still `Some(None)`, distinct
from an absent result.

## Retention and ownership

Completing a consumer does not delete its producers' entries. Finishing the run
does not clear the store either. Recorded values remain strongly referenced
while their store is alive. ExecutionState, its ResultView, or an Execution that
owns that state can keep the store alive; a snapshot copies result records, not
payloads, and can independently prolong their lifetime. Caller-held references
can outlive the store. Dropping only the Execution is therefore not a general
guarantee of payload destruction or an immediate decrease in process RSS.

ResultView provides get/require/has, not mutation or manual deletion. The store
records a successful result once per mounted path. There is no count of remaining
consumers, automatic deletion after consumption, selected-node retention option,
or persistent result cache. Treat published payloads as immutable; use explicitly
injected shared state for shared mutation, not the result store.

## Rust ownership

The mixed Python/Rust driver keeps native values in their execution session.
When Python result recording is enabled, retained Rust results are opaque native
references; conversion to a Python value happens only when read. Pure native
transport does not require Python result wrappers. See [Rust results](./rust-results.md).

The standalone Rust `execution::Execution::run()` has a different inspection
owner: its ExecutionOutcome retains successful native values until that outcome
is dropped. Cloned Arc references held by actions or callers can keep a payload
alive longer. Neither path currently reclaims a result just because its last
consumer has completed. See [Rust execution](./rust-execution.md).

## Future directions — candidates, not implemented

These are possible improvements, not current API, a promised default, or a
release commitment. The current behavior remains unchanged.

- Separate **transport lifetime** from **post-run inspection retention**. A value
  must remain available for required input binding even if it is not retained
  for later queries.
- Consider two explicit, mutually exclusive retention choices: retain all
  recorded results, or retain a caller-selected set and release other store
  references when no longer needed. An explicitly empty set could mean no
  post-run retention. The final API and default are not decided here.
- Do not infer the retained set from GraphInterface outputs: an output can
  express readiness only, not a request to preserve data for inspection.
- Derive consumers from existing result bindings, not a second user-maintained
  consumer list. Action arguments, node predicates and edge predicates are
  distinct result-use sites; ordinary order-only edges are not reads.
- Account for skipped, blocked, failed and cancelled consumers, in-flight work,
  fan-out, repeated mounts and runtime-created sessions. Predicate evaluation
  and action completion have different lifetimes. Waiting only for successful
  consumers would leak values when a consumer never runs.
- Define queries of released results explicitly. Reclamation must not disguise
  an evicted value as a skipped source or `Some(None)`, or erase terminal-state
  and failure information.
- Releasing a store reference is not forcing Python collection or destroying
  a Rust value still owned by an Arc. Language bindings and snapshots must obey
  the same ownership rules.

Before choosing a policy, measure representative payloads, fan-out and retained
inspection state under a reproducible environment. No-op node overhead alone
does not justify changing result semantics. Focused correctness tests would
also need to cover all terminal paths, mount identity, cross-language ownership
and post-run inspection. Until then, automatic reclamation is deliberately
deferred; consumers need no new public management API.
