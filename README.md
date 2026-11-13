# Rivlet

English | [简体中文](https://github.com/ririv/rivlet/blob/main/README.zh-CN.md)

Rivlet is a Python library for composing and running task graphs, with a Rust
execution core. Write your tasks as Python functions, connect them explicitly,
and reuse whole workflows as subgraphs.

Use it for build pipelines, automation, data processing, or validation workflows
that need clear dependencies and controlled concurrency. It runs inside your
application; it is not a distributed workflow service.

## What you can do

- **Compose steps and subgraphs:** use `>>` for ordering and `|` for independent
  branches, without flattening reusable subgraphs.
- **Connect readiness ports:** let a consumer prepare early and wait only where
  it actually needs a provider's output, not for the provider's entire workflow.
- **Make data flow explicit:** bind step results to action and condition arguments
  through result edges; order-only dependencies need no result passing.
- **Apply conditions where they belong:** prune optional work before execution,
  skip a step based on runtime results, or condition only a dependency.
- **Control execution:** run synchronous or asynchronous Python tasks with explicit
  concurrency limits. Runtime-created subgraphs share the same action budget.
- **Inspect what happened:** render graphs, query results, and subscribe to typed
  execution events. A failed step blocks dependent work while independent branches
  continue; the overall run still reports failure.

## Install

Requires Python 3.13 or later:

```bash
pip install rivlet
```

Prebuilt wheels are available for CPython 3.13–3.15 on Linux x86_64 (glibc 2.28+),
macOS arm64 (macOS 11+), and Windows x64. These installations need no Rust toolchain.
Other platforms require a source build; see
[distribution support](https://github.com/ririv/rivlet/blob/main/docs/releasing.md).
For Windows x64 source builds and installed-wheel validation, see
[Windows setup](https://github.com/ririv/rivlet/blob/main/docs/development.md#windows-setup).

## Compose steps and reusable subgraphs

```python
from rivlet import Execution, Graph, Leaf, ThreadRunOptions


def checks() -> Graph:
    validate = Leaf(lambda: print("validate"), label="validate")
    test = Leaf(lambda: print("test"), label="test")
    return (validate | test).graph(label="checks")


prepare = Leaf(lambda: print("prepare"), label="prepare")
finish = Leaf(lambda: print("finish"), label="finish")
graph = (prepare >> checks() >> finish).graph(label="example")
Execution(graph).run(ThreadRunOptions(workers=2))
```

After prepare, validate and test can run concurrently; finish waits for both.
`>>` orders nodes, while `|` adds no dependency. Run options set actual concurrency.

Leaf executes a callable; Graph contains a substructure. Both own identity, label
and condition and support the same wire/DSL operations. Node is their common
protocol, not another wrapper. Only Graph exposes an interface.

GraphStructure holds reusable structure without a node identity. Declare distinct
Graph nodes to use one structure twice:

```python
body = checks().structure
left = Graph(body, label="left")
right = Graph(body, label="right")
both = (left | right).graph(label="both")
Execution(both).run(ThreadRunOptions(workers=2))
```

The two Graph nodes share structure, not mounted results or paths. The DSL retains
actual nodes; the same node cannot occur twice in one structure. Execution accepts
the actual Leaf or Graph root, with no extra Step wrapper. Create another Execution
to run a declaration again. For direct construction, see the
[core model](https://github.com/ririv/rivlet/blob/main/docs/core_model.md).

## Connect subgraphs through input and output ports

Sometimes a downstream workflow needs only one intermediate result, not the
completion of an entire upstream subgraph. A port names that readiness
contract. Its output identifies the step that makes it ready; its input identifies
where the consumer starts needing it.

This complete example connects two otherwise independent subgraphs:

```python
from rivlet import (
    Graph,
    GraphStructure,
    Leaf,
    Edge,
    GraphInterface,
    Execution,
    ThreadRunOptions,
)

data_ready = "validated-data"

fetch = Leaf(lambda: print("fetch data"), label=("fetch"))
validate = Leaf(lambda: print("validate data"), label=("validate"))
archive = Leaf(lambda: print("archive data"), label=("archive"))
producer_graph = (fetch >> validate >> archive).graph(
    interface=GraphInterface(outputs={data_ready: validate}),
)

prepare = Leaf(lambda: print("prepare consumer"), label=("prepare"))
process = Leaf(lambda: print("process data"), label=("process"))
report = Leaf(lambda: print("report results"), label=("report"))
consumer_graph = (prepare >> process >> report).graph(
    interface=GraphInterface(inputs={data_ready: process}),
)

producer = Graph(structure=(producer_graph).structure, label=("producer"))
consumer = Graph(structure=(consumer_graph).structure, label=("consumer"))
graph = Graph(
    structure=GraphStructure(
        nodes=((producer, consumer)),
        edges=((Edge(source=producer, target=consumer, ports=data_ready),)),
    )
)
Execution(Graph(structure=(graph).structure, label=("pipeline"))).run(
    ThreadRunOptions(workers=3)
)
```

```text
producer: fetch → validate [output] → archive
                     │ validated-data
                     ▼
consumer: prepare → process [input] → report
```

The consumer can prepare immediately. Once validation succeeds, `process` can run
without waiting for `archive`, provided its own preparation has also completed.
The overall execution still waits for both subgraphs to finish.

Matching port names do **not** create dependencies: `Edge` explicitly
connects this producer and consumer. An ordinary port edge synchronizes readiness without passing values.
Use result edges with typed interface endpoints for argument passing, and publish an output only when later steps will no longer
invalidate what the consumer reads.

This is different from `producer >> consumer`, which waits for the
entire producer before starting any consumer step. For selecting among providers,
multiple inputs, and failure behavior, see
[interface ports](https://github.com/ririv/rivlet/blob/main/docs/interfaces.md).

### Forward an interface when wrapping a subgraph

Using `producer_graph` from the previous example:

```python
setup = Leaf(lambda: print("set up producer"), label=("setup"))
wrapped = (
    setup >> producer_graph
).graph(
    interface=producer_graph,
)
```

`interface=producer_graph` explicitly exposes the child's input and output
ports on the wrapper. Omit it and the wrapper exposes no ports. You can forward
only one direction or declare a new interface with `GraphInterface`.

## Use conditions

| What you want | API | When the condition is false |
| --- | --- | --- |
| Exclude optional work before execution | `StaticConditions.when(...)` | Remove the node and its incident edges during compilation |
| Decide whether a task runs | `Leaf(condition=predicate)` / `Graph(condition=predicate)` or `condition=(predicate, source, ...)` | Skip its action; a subgraph is not entered |
| Decide whether a task waits for another | `ConditionalPrerequisite(...)` | Remove only that waiting requirement, not either task |

### Static conditions: choose work before execution

```python
from rivlet import (
    Graph,
    Leaf,
    Execution,
    StaticCondition,
    StaticConditions,
    ThreadRunOptions,
)

extra_checks = StaticCondition(payload=False, description="extra checks enabled")
static = StaticConditions(evaluator=lambda condition: condition.payload is True)

prepare = Leaf(lambda: print("prepare static workflow"), label=("prepare"))
verify = Leaf(lambda: print("verify"), label=("verify"))
extra = Leaf(lambda: print("extra checks"), label=("extra"))
finish = Leaf(lambda: print("finish static workflow"), label=("finish"))

graph = (prepare >> (verify | static.when(extra_checks, extra)) >> finish).graph()
Execution(Graph(structure=(graph).structure, label=("static conditions"))).run(
    ThreadRunOptions(workers=2)
)
```

With `payload=False`, compilation removes `extra`; change it to `True` to include
it alongside `verify`. The application defines the payload and its evaluator;
Rivlet does not embed its own feature or platform rules.

Static pruning does not reconnect a removed node's predecessors and successors.
Here the always-present `prepare → verify → finish` path preserves the required
ordering. Static conditions also do not defer Python construction of a child graph.

### Static selection: retain candidates, select exactly one

```python
from rivlet import Graph, Leaf, Case, Execution, static

platform = "android"
version = 3
prepare = Leaf(lambda: print("prepare"), label=("prepare"))
modern = Leaf(lambda: print("modern Android"), label=("modern"))
legacy = Leaf(lambda: print("legacy Android"), label=("legacy"))
linux = Leaf(lambda: print("Linux"), label=("linux"))
finish = Leaf(lambda: print("finish"), label=("finish"))

graph = (
    prepare
    >> static.select(
        cases=(
            Case(platform == "android" and version >= 3, modern),
            Case(platform == "android" and version < 3, legacy),
            Case(platform == "linux", linux),
        ),
    )
    >> finish
).graph()
Execution(Graph(structure=(graph).structure, label=("static selection"))).run()
```

Each `Case(condition, component)` contains one complete boolean condition and a
Leaf or Graph. A Graph is already a node; pass it directly.
Declaration rendering retains every case; compilation requires exactly
one true condition and produces `prepare → modern → finish`. Zero or multiple active
cases are errors, not first-match precedence. `static` is the module namespace and
requires no evaluator. Python computes the booleans at construction; compilation
checks the selection. See [static selection](https://github.com/ririv/rivlet/blob/main/docs/static_selection.md)
for subgraphs, boundaries, and direct declarations.

### Passing results to an action

```python
from rivlet import Graph, Leaf, Execution, Option, Results, NodeRunContext

prepare = Leaf(lambda: print("prepare"))
plan = Leaf(lambda: "implementation plan")
review = Leaf(lambda: True)


def implement(
    ctx: NodeRunContext, plan: Option[object], review: Option[object]
) -> None:
    ctx.control.require_running()
    print(plan.unwrap(), review.unwrap())


work = Leaf(implement)
graph = Graph.from_wiring(
    (
        prepare.wire(),
        plan.wire(),
        review.wire(),
        work.wire(
            after=(
                prepare,
                Results(plan, review),
            )
        ),
    )
)
Execution(Graph(structure=(graph).structure)).run()
```

`after` lists both kinds of prerequisite: a plain Node only waits for completion;
`Results(plan, review)` also passes their results in that order. For direct construction,
the equivalent edges are `Edge(prepare, work)` and `*Edge.results((plan, review), work)`.
Skipped producers supply `Nothing()`; a successful `None` is `Some(None)`.
Failed producers block the consumer. Result edges can also use
`producer.interface.outputs[name]` and `consumer.interface.inputs[name]` across
subgraphs; see the complete [port result example](https://github.com/ririv/rivlet/blob/main/docs/interfaces.md#result-transport).

### Runtime conditions: decide from a step result

```python
from rivlet import Graph, Leaf, Execution, Option


def should_process(result: Option[object]) -> bool:
    return result.unwrap() is True


probe = Leaf(lambda: False, label=("has work"))
process = Leaf(
    lambda: print("process work"),
    label=("process"),
    condition=((should_process, probe)),
)
done = Leaf(lambda: print("workflow finished"), label=("done"))

graph = Graph.from_wiring(
    (
        probe.wire(),
        process.wire(),
        done.wire(after=(process,)),
    ),
)
Execution(Graph(structure=(graph).structure, label=("runtime conditions"))).run()
```

`condition=(should_process, probe)` declares the result dependency and passes it as
an `Option`; there is no second `after=(probe,)` to maintain. The tuple is flat:
`(predicate, source_a, source_b, ...)`, with no fixed maximum number of sources.
With no inputs, pass the predicate directly, for example `condition=lambda: True`.
These are lightweight condition declarations, not extra Steps. Because the probe
returns `False`, `process` is skipped and `done` still runs. Return `True` to run
`process`. A skipped action produces no result; an action returning `None` produces
`Some(None)`. If a source may be skipped, inspect `is_some()` or `is_none()` before
unwrapping. A predicate error blocks the affected task; it is not treated as false.

For a subgraph, pass the same condition to `Graph(structure, condition=...)`.

### Conditional edges: decide whether to wait

```python
from rivlet import Graph, Leaf, ConditionalPrerequisite, Execution, ThreadRunOptions

decision = Leaf(lambda: False, label=("wait for checks"))
checks = Leaf(lambda: print("run checks"), label=("checks"))
publish = Leaf(lambda: print("publish"), label=("publish"))
wait_for_checks = lambda result: result.unwrap() is True

graph = Graph.from_wiring(
    (
        decision.wire(),
        checks.wire(),
        publish.wire(
            after=(
                ConditionalPrerequisite(
                    checks,
                    condition=(wait_for_checks, decision),
                ),
            ),
        ),
    ),
)
Execution(Graph(structure=(graph).structure, label=("conditional dependency"))).run(
    ThreadRunOptions(workers=2)
)
```

`publish` waits for `decision`. If it returns `True`, `publish` also waits for
`checks`; if it returns `False`, `checks` still runs, but publishing need not wait
for it. Both tasks remain part of the overall run. To skip publishing itself,
declare it with `Leaf(..., condition=...)` instead. Static edge conditions are available
through `static.edge(source, target, condition=...)`.

## Export structure without running tasks

```python
import json
from rivlet import Graph, Leaf, Execution, export_graph

graph = (
    Leaf(lambda: None, label=("prepare")) >> Leaf(lambda: None, label=("finish"))
).graph()
execution = Execution(Graph(structure=(graph).structure))
declaration = export_graph(graph)
compilation = execution.compile()
compiled = export_graph(compilation)
json.dumps({"declaration": declaration, "compiled": compiled})
execution.run()  # reuses the same compilation
```

The versioned JSON document preserves mounted IDs, subgraph boundaries, typed edges,
conditions, and interface ports. Declaration inspection evaluates no host code;
compiled export reads existing decisions without recompiling. Runtime events and
results stay separate. See [structural export](https://github.com/ririv/rivlet/blob/main/docs/graph_export.md)
for the schema, historical snapshots, and static/dynamic scope limits.

## Explore more

- [Result inputs and action context](https://github.com/ririv/rivlet/blob/main/docs/core_model.md#results-and-runtime-context)
- [Conditional steps](https://github.com/ririv/rivlet/blob/main/docs/conditional_steps.md) and [conditional edges](https://github.com/ririv/rivlet/blob/main/docs/conditional_edges.md)
- [Async execution, cancellation, and failure propagation](https://github.com/ririv/rivlet/blob/main/docs/execution.md)
- [Typed failure events](https://github.com/ririv/rivlet/blob/main/docs/failure_events.md)
- [All documentation](https://github.com/ririv/rivlet/blob/main/docs/index.md)

For contributors: [local development and checks](https://github.com/ririv/rivlet/blob/main/docs/development.md),
[architecture](https://github.com/ririv/rivlet/blob/main/docs/rust-architecture.md),
and [releasing](https://github.com/ririv/rivlet/blob/main/docs/releasing.md).

## License

This project is licensed under the [MIT License](https://github.com/ririv/rivlet/blob/main/LICENSE).
