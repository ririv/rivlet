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
from rivlet import Graph, Execution, Step, ThreadRunOptions


def checks() -> Graph:
    validate = Step(lambda: print("validate"), label="validate")
    test = Step(lambda: print("test"), label="test")
    return (validate | test).graph()


prepare = Step(lambda: print("prepare"), label="prepare")
finish = Step(lambda: print("finish"), label="finish")

graph = (prepare >> Step(checks(), label="checks") >> finish).graph()
Execution(root=Step(graph, label="example")).run(ThreadRunOptions(workers=2))
```

After `prepare`, the `checks` subgraph can run `validate` and `test` concurrently.
`finish` waits for both to succeed; their print order is not guaranteed.

`checks()` returns a reusable `Graph` structure. `Step(checks(), label="checks")`
explicitly mounts it with its own identity and label. Nesting preserves its boundary
and internal dependencies. `>>` waits for the whole
left-hand step or subgraph; `|` adds no dependency between its branches. Actual
concurrency comes from the run options, not from `|` alone.

The operators also accept bare `Leaf` and `Graph`: `prepare >> checks() >> finish`.
Each bare occurrence gets a fresh anonymous Step; a Graph remains a subgraph.
Use an explicit Step when you need a label, condition, or a reference for wiring.

To wrap the whole composition directly, use
`(prepare >> checks() >> finish).step(label="pipeline")`;
this is equivalent to `.graph().step(label="pipeline")` and returns a Step.

Declarations and runs are separate: `Graph` describes the workflow;
`Step` owns identity, label and runtime condition; `Graph` owns structure, not a name.
`Execution(root_step)` owns one run, including the root's condition and lifecycle.
The root may contain a Graph or a single Leaf. Create a new execution to run the declaration
again. You can also declare the same wiring without the DSL; see the
[core model](https://github.com/ririv/rivlet/blob/main/docs/core_model.md).

## Connect subgraphs through input and output ports

Sometimes a downstream workflow needs only one intermediate result, not the
completion of an entire upstream subgraph. A port names that readiness
contract. Its output identifies the step that makes it ready; its input identifies
where the consumer starts needing it.

This complete example connects two otherwise independent subgraphs:

```python
from rivlet import (
    Edge,
    GraphInterface,
    Graph,
    Execution,
    Step,
    ThreadRunOptions,
)

data_ready = "validated-data"

fetch = Step(lambda: print("fetch data"), label="fetch")
validate = Step(lambda: print("validate data"), label="validate")
archive = Step(lambda: print("archive data"), label="archive")
producer_graph = (fetch >> validate >> archive).graph(

    interface=GraphInterface(outputs={data_ready: validate}),
)

prepare = Step(lambda: print("prepare consumer"), label="prepare")
process = Step(lambda: print("process data"), label="process")
report = Step(lambda: print("report results"), label="report")
consumer_graph = (prepare >> process >> report).graph(

    interface=GraphInterface(inputs={data_ready: process}),
)

producer = producer_graph.step(label="producer")
consumer = consumer_graph.step(label="consumer")
graph = Graph(

    steps=(producer, consumer),
    edges=(
        Edge(source=producer, target=consumer, ports=data_ready),
    ),
)
Execution(Step(graph, label="pipeline")).run(ThreadRunOptions(workers=3))
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
connects this producer and consumer. Ports synchronize readiness; they do not
transfer Python return values or inspect external data. Use result edges for
argument passing, and publish an output only when later steps will no longer
invalidate what the consumer reads.

This is different from `producer >> consumer`, which waits for the
entire producer before starting any consumer step. For selecting among providers,
multiple inputs, and failure behavior, see
[interface ports](https://github.com/ririv/rivlet/blob/main/docs/interfaces.md).

### Forward an interface when wrapping a subgraph

Using `producer_graph` from the previous example:

```python
setup = Step(lambda: print("set up producer"), label="setup")
wrapped = (setup >> Step(producer_graph, label="producer")).graph(

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
| Decide whether a task runs | `Step(condition=predicate)` or `Step(condition=(predicate, source, ...))` | Skip its action; a subgraph is not entered |
| Decide whether a task waits for another | `ConditionalPrerequisite(...)` | Remove only that waiting requirement, not either task |

### Static conditions: choose work before execution

```python
from rivlet import (
    Execution,
    StaticCondition,
    StaticConditions,
    Step,
    ThreadRunOptions,
)

extra_checks = StaticCondition(payload=False, description="extra checks enabled")
static = StaticConditions(evaluator=lambda condition: condition.payload is True)

prepare = Step(lambda: print("prepare static workflow"), label="prepare")
verify = Step(lambda: print("verify"), label="verify")
extra = Step(lambda: print("extra checks"), label="extra")
finish = Step(lambda: print("finish static workflow"), label="finish")

graph = (
    prepare >> (verify | static.when(extra_checks, extra)) >> finish
).graph()
Execution(Step(graph, label="static conditions")).run(ThreadRunOptions(workers=2))
```

With `payload=False`, compilation removes `extra`; change it to `True` to include
it alongside `verify`. The application defines the payload and its evaluator;
Rivlet does not embed its own feature or platform rules.

Static pruning does not reconnect a removed node's predecessors and successors.
Here the always-present `prepare → verify → finish` path preserves the required
ordering. Static conditions also do not defer Python construction of a child graph.

### Static selection: retain candidates, select exactly one

```python
from rivlet import Case, Execution, Step, static

platform = "android"
version = 3
prepare = Step(lambda: print("prepare"), label="prepare")
modern = Step(lambda: print("modern Android"), label="modern")
legacy = Step(lambda: print("legacy Android"), label="legacy")
linux = Step(lambda: print("Linux"), label="linux")
finish = Step(lambda: print("finish"), label="finish")

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
Execution(Step(graph, label="static selection")).run()
```

Each `Case(condition, component)` contains one complete boolean condition and a
Step. To select a subgraph, pass `Step(child_graph, label="child")`.
Declaration rendering retains every case; compilation requires exactly
one true condition and produces `prepare → modern → finish`. Zero or multiple active
cases are errors, not first-match precedence. `static` is the module namespace and
requires no evaluator. Python computes the booleans at construction; compilation
checks the selection. See [static selection](https://github.com/ririv/rivlet/blob/main/docs/static_selection.md)
for subgraphs, boundaries, and direct declarations.

### Passing results to an action

```python
from rivlet import Edge, Execution, Graph, Option, Results, Step, StepRunContext

prepare = Step(lambda: print("prepare"))
plan = Step(lambda: "implementation plan")
review = Step(lambda: True)


def implement(
    ctx: StepRunContext, plan: Option[object], review: Option[object]
) -> None:
    ctx.control.require_running()
    print(plan.unwrap(), review.unwrap())


work = Step(implement)
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
Execution(Step(graph)).run()
```

`after` lists both kinds of prerequisite: a plain Step only waits for completion;
`Results(plan, review)` also passes their results in that order. For direct construction,
the equivalent edges are `Edge(prepare, work)` and `*Edge.results((plan, review), work)`.
Skipped producers supply `Nothing()`; a successful `None` is `Some(None)`.
Failed producers block the consumer. Result connections are currently graph-local;
subgraph interface ports express readiness, not value transport.

### Runtime conditions: decide from a step result

```python
from rivlet import Graph, Execution, Option, Step


def should_process(result: Option[object]) -> bool:
    return result.unwrap() is True


probe = Step(lambda: False, label="has work")
process = Step(
    lambda: print("process work"), label="process", condition=(should_process, probe)
)
done = Step(lambda: print("workflow finished"), label="done")

graph = Graph.from_wiring(
    (
        probe.wire(),
        process.wire(),
        done.wire(after=(process,)),
    ),

)
Execution(Step(graph, label="runtime conditions")).run()
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

For a subgraph, use the same runtime condition on `child_graph.step(condition=...)`.

### Conditional edges: decide whether to wait

```python
from rivlet import (
    ConditionalPrerequisite,
    Graph,
    Execution,
    Step,
    ThreadRunOptions,
)

decision = Step(lambda: False, label="wait for checks")
checks = Step(lambda: print("run checks"), label="checks")
publish = Step(lambda: print("publish"), label="publish")
wait_for_checks = lambda result: result.unwrap() is True

graph = Graph.from_wiring(
    (
        decision.wire(),
        checks.wire(),
        publish.wire(
            after=(
                ConditionalPrerequisite(
                    checks, condition=(wait_for_checks, decision),
                ),
            ),
        ),
    ),

)
Execution(Step(graph, label="conditional dependency")).run(ThreadRunOptions(workers=2))
```

`publish` waits for `decision`. If it returns `True`, `publish` also waits for
`checks`; if it returns `False`, `checks` still runs, but publishing need not wait
for it. Both tasks remain part of the overall run. To skip publishing itself,
declare it with `Step(..., condition=...)` instead. Static edge conditions are available
through `static.edge(source, target, condition=...)`.

## Export structure without running tasks

```python
import json
from rivlet import Graph, Execution, Step, export_graph

graph = (Step(lambda: None, label="prepare") >> Step(lambda: None, label="finish")).graph()
execution = Execution(Step(graph))
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
