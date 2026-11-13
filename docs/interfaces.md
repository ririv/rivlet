# Graph interfaces and named ports

English | [简体中文](./zh-CN/interfaces.md)

A port dependency says “the consumer needs this contract,” not necessarily
“the consumer depends on this particular component.”

```text
A provides 123
C provides 123
B requires 123
```

A and C are candidate providers. Selection happens before execution; the runtime
does not choose whichever provider happens to finish first.

## Graph interfaces

Port names are plain strings, not wrapper objects. Names must be non-empty and
have no leading or trailing whitespace. Reuse a named string constant when several
declarations refer to the same port. Names alone do not connect graphs: declare
the requirement and resolve its provider explicitly.

A Graph may declare several input and output ports:

- `GraphInterface.inputs` maps a port to one or more internal Steps
  where consumption begins.
- `GraphInterface.outputs` maps a port to the single Step whose
  completion makes it ready.

Ports reference real Steps. They do not create empty actions or synthetic event nodes.

```python
from rivlet import GraphInterface

port_123 = "123"

a_graph = (a_1 >> a_2 >> a_3 >> a_4 >> a_5).graph(

    interface=GraphInterface(outputs={port_123: a_3}),
)
b_graph = (b_a >> b_b >> b_c >> b_d >> b_e).graph(

    interface=GraphInterface(inputs={port_123: b_b}),
)
a = a_graph.step(label="A")
b = b_graph.step(label="B")
```

An output is a stability promise: later steps must not destroy or replace what has
already been exposed. If later work produces a different fact, expose another
port.

Ports may reference direct Steps or descendant Steps inside preserved Python
subgraphs. This allows wrapping a graph without losing its real readiness points:

```python
wrapped = (prepare >> Step(child_graph) >> normalize).graph(
    interface=GraphInterface(
        inputs=child_graph,
        outputs={installed_runtime: normalize},
    ),
)
```

If wiring was prepared separately, declare the complete interface when materializing it:

```python
graph = prepared_wiring.graph(
    interface=GraphInterface(
        inputs={toolchain: compile_stage},
        outputs={installed_runtime: install_stage},
    ),
)
```

There is no API to overwrite, merge, or append ports after Graph construction.

## Forwarding and reuse

Graph, Graph.from_wiring, and wiring.graph accept the same `interface` argument.
Omitting it means an empty interface, not automatic exposure of inner ports.

```python
# Forward the full interface without copying Steps or flattening the child.
outer = (prepare >> Step(child_graph)).graph(interface=child_graph)

# Forward inputs only; publish a different output.
outer = (Step(child_graph) >> normalize).graph(
    interface=GraphInterface(
        inputs=child_graph,
        outputs={installed_runtime: normalize},
    ),
)

# Reuse normalized mappings or an original mapping.
interface = GraphInterface(inputs=child_graph.interface.inputs)
shared_inputs = {toolchain: compile_stage}
shared = GraphInterface(inputs=shared_inputs)
another = GraphInterface(inputs=shared.inputs)
```

`inputs=graph` and `outputs=graph` forward only that direction, equivalent to
passing `graph.interface.inputs` or `.outputs`. Construction copies mappings
into read-only views; later changes to the caller's dictionary do not affect the
declaration. Inputs normalize to nonempty Step tuples; each output remains one Step.

Referenced Steps must be mounted exactly once in the resulting graph. Forwarding
does not make an external or ambiguously mounted Step valid.

A port occupies one dictionary key. Duplicate Python keys are overwritten
before Graph sees them, so use a tuple to constrain multiple input Steps:

```python
consumer = ((prepare_a >> configure_a) | (prepare_b >> configure_b)).graph(
    interface=GraphInterface(
        inputs={toolchain: (configure_a, configure_b)},
    ),
)
```

Both preparations can start without the toolchain. Each configure waits for its
own predecessor and the toolchain, not for the other configure. A single binding
expands to both inputs, without an empty action or shared waiting node.
Outputs do not accept several Steps: that would require an explicit all/any
readiness model.

## Direct connections

`Edge` combines ordinary completion dependencies, port routing, and optional
runtime conditions. Port names are local to each graph:

```python
from rivlet import Edge

Edge(source=a, target=b)  # wait for all of A before starting B
Edge(source=a, target=b, ports="ready")  # A.ready -> B.ready
Edge(source=a, target=b, ports=("sdk-ready", "toolchain-ready"))
Edge(source=a, target=b, ports=("sdk-ready", "toolchain-ready"), condition=needed)
```

The fixed pair is `(source output, target input)`; a string normalizes to
`(name, name)`. These references do not declare ports. Both names must already
exist in the corresponding `GraphInterface`; source inputs and target outputs
are never searched. Different names require no renaming wrapper graph.

The condition controls only whether the internal input Steps wait for the output
Step. It does not gate the whole consumer graph or skip either component. One
condition instance is shared by all Steps in that input mapping, and its result
inputs are declared directly in `condition=(predicate, probe, ...)`, resolved in
the consumer component's parent graph. Unrelated consumer preparation can run early.
Static edge rules can also remove the connection during compilation.

Ports still express readiness, not result transport. Graph-local `Edge.results()` and
`Results(...)` in `wire(after=...)` bind `Option` arguments; there are no `.input()`/`.output()` or
`.argument()` endpoint helpers in this API.

## Two requirement forms

```python
type PortRequirement = AnyProviderRequirement | ExactProviderRequirement
```

Require a port from an unspecified provider:

```python
AnyProviderRequirement(consumer=b, port=port_123)
```

Require it specifically from A:

```python
ExactProviderRequirement(consumer=b, provider=a, port=port_123)
```

One input can require several exact providers, for example both A and C. That
produces two required bindings, not an either/or selection. The variants are
explicit; `provider=None` is not a substitute for the sum type.

## Provider resolution

`resolve_port_requirements()` turns symbolic requirements into definite
`Edge(..., ports=name)` values before graph execution. Requirement resolution
continues to match the common name on both sides; use a direct `Edge` for
different local names:

```text
AnyProviderRequirement(123)
    → select a provider
    → Edge(A.123 → B.123)

ExactProviderRequirement(A.123)
    → verify that A provides 123
    → Edge(A.123 → B.123)
```

An any-provider requirement resolves directly when only one provider exists.
With both A and C, select explicitly:

```python
# c is another graph-valued Step providing port_123.
requirement = AnyProviderRequirement(consumer=b, port=port_123)
bindings = resolve_port_requirements(
    (a, b, c),
    (requirement,),
    selections=(ProviderSelection(requirement=requirement, provider=a),),
)
graph = Graph( steps=(a, b, c), edges=bindings)
```

Ambiguity without a selection is an error. There is no “first ready wins” policy
and no provider failover. Hosts can derive selections from their configuration
or lock data; Graph validates and lowers them without adopting domain policies.

## A plain-language example

B depends on A. A has steps 1, 2, 3, 4, and 5, and declares step 3 as its output.
B has steps a, b, c, d, and e, and declares step b as the input where it starts
needing that port.

Before A reaches step 3, B can already do step a on its own. After step 3, A tells
B only “I'm ready.” B does not need to know which internal step A reached.
B continues from b while A continues with 4 and 5.

If B also needs a different port from C, it can attach that input to a
different internal Step. There is no single input that must gate every dependency;
each port is attached where it is first consumed.

## Failures at interface ports

Failures follow actual compiled dependencies, not mere membership in the same
subgraph. These examples assume explicit bindings: matching port names alone do
not create edges.

### The output is ready, then later work fails

```text
A: 1 → 2 → 3 [output: library-ready] → 4 [failed]
           │
           │ library-ready
           ▼
B: a ────→ b [input] → c
```

B's a can run early. Once A's 3 succeeds, b can start without waiting for 4.
Failure in 4 does not revoke the ready port; b and c can continue.
A and the overall run still report failure.

In plain terms: A already handed B what it needs; A's later problem should not
stop work B can safely do. This requires a truthful output contract. If 4 still
modifies files B will read, the output belongs at 4 or later. Graph does not inspect
files to infer readiness.

### The output cannot become ready

```text
A: 1 → 2 [failed] → 3 [output: library-ready, blocked]
                   │
                   │ unavailable
                   ▼
B: a [can run] ──→ b [input, blocked] → c [blocked]
```

The same applies when the output Step itself fails. Only the dependent portion
of B is blocked. Blocked is not an explicit skip and cannot release downstream
work as though it succeeded.

### Several ports in one subgraph

```text
A:
├── Generate headers [success, output: headers-ready]
└── Generate library [failed, output: library-ready]

B:
├── Prepare sources [can run]
├── Check headers [input: headers-ready, can run]
└── Link program [input: library-ready, blocked]
```

The header output stays ready. This assumes header checking has no other failed
prerequisite. A failed output blocks all inputs bound to it. Failure in one consumer
does not propagate backward to the provider or sideways to independent consumers.

When one input requires both A and C, both must be ready. C's success cannot
compensate for A's failure, while unrelated consumer work can still proceed.

### Waiting for a whole subgraph is different

```python
graph = (Step(a_graph) >> Step(b_graph)).graph()
```

This waits for all of A to succeed before entering B:

```text
A [overall failure] → B [entire subgraph blocked]
```

Use port connections for early readiness and independent preparation. If both
a whole-graph ordering constraint and a port connection are declared, both
apply; interface ports do not override ordering.

There is no rollback or provider switching. See [failure propagation](./execution.md#failure-propagation)
for exception aggregation, predicate failures, cancellation, and snapshots.

## Compilation and execution

The declaration compiler locates internal port nodes and lowers each binding to
an ordinary execution dependency:

```text
A.3 ── port 123 ──> B.b
```

Both subgraph boundaries remain. The compiler does not turn this into “all of A
must finish before any of B starts.” Both public modes use the same lowering:

```python
Execution(root=Step(graph)).run(mode="compiled")
Execution(root=Step(graph)).run(mode="dynamic")
```

## Language boundaries

Python holds Step references and validates interface ownership. Components,
ports, requirements, and selections are encoded as integer IDs for one
Rust provider-resolution call. The resolver validates uniqueness, ambiguity, and
explicit selections without executing actions or providing failover.

Explicit bindings enter declaration compilation and lowering. Final DAG validation
and scheduling live in Rust; Python does not maintain a second provider solver.

Ports reference Python-declared Steps, not opaque NativeSteps inside a NativeGraph.
To publish completion of an entire native graph, mount it in a Python Step and
use that Step as an output. A dynamically returned graph cannot expose unknown
internal ports before it exists.

Provider components and their direct output Steps cannot use runtime `condition` to
decide whether they exist. Provider selection is fixed before compilation; runtime
skipping is not a mechanism for changing candidates.
