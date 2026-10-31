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

Prebuilt wheels are available for CPython 3.13–3.15 on Linux x86_64 (glibc 2.28+)
and macOS arm64 (macOS 11+). These installations need no Rust toolchain.
Other platforms require a source build; see
[distribution support](https://github.com/ririv/rivlet/blob/main/docs/releasing.md).

## Compose steps and reusable subgraphs

```python
from rivlet import Graph, GraphExecution, Step, ThreadGraphRunOptions


def checks() -> Graph:
    validate = Step(lambda: print("validate"), label="validate")
    test = Step(lambda: print("test"), label="test")
    return (validate | test).graph(label="checks")


prepare = Step(lambda: print("prepare"), label="prepare")
finish = Step(lambda: print("finish"), label="finish")

graph = (prepare >> checks() >> finish).graph(label="example")
GraphExecution(graph=graph).run(ThreadGraphRunOptions(workers=2))
```

After `prepare`, the `checks` subgraph can run `validate` and `test` concurrently.
`finish` waits for both to succeed; their print order is not guaranteed.

`checks()` returns an ordinary `Graph`, ready to place in other workflows. Nesting
it preserves its boundary and internal dependencies. `>>` waits for the whole
left-hand step or subgraph; `|` adds no dependency between its branches. Actual
concurrency comes from the run options, not from `|` alone.

Declarations and runs are separate: `Graph` describes the workflow;
`GraphExecution(graph)` owns one run. Create a new execution to run the declaration
again. You can also declare the same wiring without the DSL; see the
[core model](https://github.com/ririv/rivlet/blob/main/docs/core_model.md).

## Connect subgraphs through input and output ports

Sometimes a downstream workflow needs only one intermediate result, not the
completion of an entire upstream subgraph. A capability names that readiness
contract. Its output identifies the step that makes it ready; its input identifies
where the consumer starts needing it.

This complete example connects two otherwise independent subgraphs:

```python
from rivlet import (
    Capability,
    CapabilityBinding,
    CapabilityInterface,
    Graph,
    GraphExecution,
    Step,
    ThreadGraphRunOptions,
)

data_ready = Capability("validated-data")

fetch = Step(lambda: print("fetch data"), label="fetch")
validate = Step(lambda: print("validate data"), label="validate")
archive = Step(lambda: print("archive data"), label="archive")
producer_graph = (fetch >> validate >> archive).graph(
    label="producer",
    capabilities=CapabilityInterface(outputs={data_ready: validate}),
)

prepare = Step(lambda: print("prepare consumer"), label="prepare")
process = Step(lambda: print("process data"), label="process")
report = Step(lambda: print("report results"), label="report")
consumer_graph = (prepare >> process >> report).graph(
    label="consumer",
    capabilities=CapabilityInterface(inputs={data_ready: process}),
)

producer = producer_graph.step()
consumer = consumer_graph.step()
graph = Graph(
    label="pipeline",
    steps=(producer, consumer),
    edges=(
        CapabilityBinding(source=producer, target=consumer, capability=data_ready),
    ),
)
GraphExecution(graph).run(ThreadGraphRunOptions(workers=3))
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

Matching port names do **not** create dependencies: `CapabilityBinding` explicitly
connects this producer and consumer. Ports synchronize readiness; they do not
transfer Python return values or inspect external data. Use result edges for
argument passing, and publish an output only when later steps will no longer
invalidate what the consumer reads.

This is different from `producer_graph >> consumer_graph`, which waits for the
entire producer before starting any consumer step. For selecting among providers,
multiple inputs, and failure behavior, see
[capability ports](https://github.com/ririv/rivlet/blob/main/docs/capabilities.md).

### Forward an interface when wrapping a subgraph

Using `producer_graph` from the previous example:

```python
setup = Step(lambda: print("set up producer"), label="setup")
wrapped = (setup >> producer_graph).graph(
    label="wrapped producer",
    capabilities=producer_graph,
)
```

`capabilities=producer_graph` explicitly exposes the child's input and output
ports on the wrapper. Omit it and the wrapper exposes no ports. You can forward
only one direction or declare a new interface with `CapabilityInterface`.

## Use conditions

| What you want | API | When the condition is false |
| --- | --- | --- |
| Exclude optional work before execution | `StaticConditions.when(...)` | Remove the node and its incident edges during compilation |
| Decide whether a task runs | `Step(condition=runtime_condition(...))` | Skip its action; a subgraph is not entered |
| Decide whether a task waits for another | `ConditionalPrerequisite(...)` | Remove only that waiting requirement, not either task |

### Static conditions: choose work before execution

```python
from rivlet import (
    GraphExecution,
    StaticCondition,
    StaticConditions,
    Step,
    ThreadGraphRunOptions,
)

extra_checks = StaticCondition(payload=False, description="extra checks enabled")
static = StaticConditions(evaluator=lambda condition: condition.payload is True)

prepare = Step(lambda: print("prepare static workflow"), label="prepare")
verify = Step(lambda: print("verify"), label="verify")
extra = Step(lambda: print("extra checks"), label="extra")
finish = Step(lambda: print("finish static workflow"), label="finish")

graph = (
    prepare >> (verify | static.when(extra_checks, extra)) >> finish
).graph(label="static conditions")
GraphExecution(graph).run(ThreadGraphRunOptions(workers=2))
```

With `payload=False`, compilation removes `extra`; change it to `True` to include
it alongside `verify`. The application defines the payload and its evaluator;
Rivlet does not embed its own feature or platform rules.

Static pruning does not reconnect a removed node's predecessors and successors.
Here the always-present `prepare → verify → finish` path preserves the required
ordering. Static conditions also do not defer Python construction of a child graph.

### Runtime conditions: decide from a step result

```python
from rivlet import Graph, GraphExecution, Option, Step, runtime_condition


def should_process(result: Option[object]) -> bool:
    return result.unwrap() is True


probe = Step(lambda: False, label="has work")
has_work = runtime_condition(when=should_process)
process = Step(
    lambda: print("process work"), label="process", condition=has_work
)
done = Step(lambda: print("workflow finished"), label="done")

graph = Graph.from_wiring(
    (
        probe.wire(),
        process.wire(inputs={has_work: (probe,)}),
        done.wire(after=(process,)),
    ),
    label="runtime conditions",
)
GraphExecution(graph).run()
```

`inputs` declares the result dependency and passes the result to the predicate as
an `Option`; there is no second `after=(probe,)` to maintain. Because the probe
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
    GraphExecution,
    Step,
    ThreadGraphRunOptions,
    runtime_condition,
)

decision = Step(lambda: False, label="wait for checks")
checks = Step(lambda: print("run checks"), label="checks")
publish = Step(lambda: print("publish"), label="publish")
wait_for_checks = runtime_condition(when=lambda result: result.unwrap() is True)

graph = Graph.from_wiring(
    (
        decision.wire(),
        checks.wire(),
        publish.wire(
            after=(ConditionalPrerequisite(checks, condition=wait_for_checks),),
            inputs={wait_for_checks: (decision,)},
        ),
    ),
    label="conditional dependency",
)
GraphExecution(graph).run(ThreadGraphRunOptions(workers=2))
```

`publish` waits for `decision`. If it returns `True`, `publish` also waits for
`checks`; if it returns `False`, `checks` still runs, but publishing need not wait
for it. Both tasks remain part of the overall run. To skip publishing itself,
declare it with `Step(..., condition=...)` instead. Static edge conditions are available
through `static.edge(source, target, condition=...)`.

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
