# Conditional edges

English | [简体中文](./zh-CN/conditional_edges.md)

## Purpose

A conditional edge describes an optional prerequisite: “B depends on A, but only
if this condition holds.” It does not decide whether A or B executes.
For node participation, use [conditional steps](./conditional_steps.md).

The runtime form is `Edge(source, target, condition=condition)`. The wiring
API provides:

```python
target.wire(
    after=(ConditionalPrerequisite(source, condition=condition),),
)
```

Graph.from_wiring normalizes this into an Edge before execution, without adding
an action or changing the compiled topology at runtime. Explicit skip requests
are a separate host API, not part of NodeRunContext.control.

## Two kinds of conditions

StaticConditions binds static rules to their evaluator. The `condition` argument
of Edge and ConditionalPrerequisite accepts a predicate or a flat
`(predicate, source_step, ...)` tuple, not a static condition. Evaluation differs:

- During declaration compilation, an explicit evaluator resolves static conditions.
- Runtime conditions lower to prerequisites identified by ConditionId.
- The resulting graph enters the unified Rust execution session.

Compiled conditional dependencies do not create extra gate Steps or worker tasks. CompiledComposite preserves each subgraph
boundary: a false internal edge does not make the whole composite finish early.

## Static conditions

A static condition is known at compilation time and does not read action results.
Its payload and interpretation are application-defined.

[static_condition.py](../src/rivlet/static_condition.py) models a condition as
an opaque payload and a display description:

```python
StaticCondition(
    payload=SomePayload(...),
    description="environment(production)",
)
```

Graph does not define application-specific expressions. The application supplies
their meaning through StaticConditions. Its
`evaluator` accepts either a StaticConditionEvaluator or
`Callable[[StaticCondition], bool]`.

Graph traverses declarations, calls the evaluator, and retains or removes the
dependency in the new compilation. It does not mutate the original declaration.
The host defines payloads and their interpretation.

Graph itself provides `all_(...)`, `any_(...)`, and `not_(...)` for combining
static conditions. Combining leaves does not remove the need for an evaluator:

```python
condition = all_(
    StaticCondition(payload="production", description="production environment"),
    StaticCondition(payload="audit", description="audit enabled"),
)
```

These strings are host-defined payloads, not built-in Graph expressions.
StaticConditions requires an explicit evaluator.

### A static-edge example

Suppose publishing waits for validation only when the configuration requires it.
This example controls only the dependency; validation itself stays enabled.

```python
from rivlet import Edge, Graph, GraphStructure, StaticCondition, StaticConditions, Step

collect = Step(lambda: print("collect"), label="collect")
validate = Step(lambda: print("validate"), label="validate")
publish = Step(lambda: print("publish"), label="publish")

condition = StaticCondition(
    payload=True,
    description="wait for validation",
)
graph = Graph(
    structure=GraphStructure(
        nodes=(collect, validate, publish),
        edges=(
            (
                Edge(source=collect, target=publish),
                Edge(source=validate, target=publish),
            )
        ),
        static=(
            StaticConditions(
                evaluator=lambda condition: condition.payload is True,
                conditions={(validate, publish): condition},
            )
        ),
    )
)
```

When the evaluator returns true:

```text
collect ───→ publish
validate ──→ publish
```

When it returns false:

```text
collect ───→ publish
validate      (still runs independently)
```

A true static edge becomes an ordinary dependency; a false one disappears.
Removing an edge does not disable its source Node. Use a static node rule to disable the
source as well. Payload types, evaluator functions, and actions in this example
belong to the host.

## Static edge DSL

```python
static = StaticConditions(evaluator=lambda condition: condition.payload is True)
graph = static.edge(validate, publish, condition=condition).graph()
```

This returns ordinary GraphWiring without gate Steps. Unlike static.when, false
removes only the edge; validate and publish remain. Composed StaticConditions retain
their own evaluators. Duplicate rules for one target are rejected, never overridden.
The DSL does not automatically merge repeated Node objects.

## Runtime conditions

Runtime conditions are evaluated during execution, often from a cache probe or
other Node result. Their public input forms are:

```python
condition = lambda: True
condition = (lambda result: result.unwrap() is True, probe)
```

The predicate receives one `Option[object]` per source Node, in tuple order.
Graph resolves the results in the current mount; no context lookup is required.
See [result inputs](./conditional_steps.md#result-inputs) for absence and unwrapping.

Each compiled condition instance is evaluated at most once in its session.
If it is true but the source prerequisite is not yet complete, subsequent scheduling
waits without rerunning the predicate. Reusing one condition tuple or
callable at several sites does not deduplicate those evaluations.

## Inputs and ordering prerequisites

Conditions bind their inputs at the point of use. Action arguments remain in
`Results(...)` inside `wire(after=...)`; plain Steps declare execution ordering. Node conditions
and individual edge conditions may read different sources without receiver lookup.

```python
permission = Step(lambda: True)
design = Step(lambda: "implement login")
environment_check = Step(lambda: True)
prepare = Step(lambda: print("prepare environment"))

prepare_dependency = ConditionalPrerequisite(
    prepare,
    condition=(
        lambda result: result.is_some() and result.unwrap() is True,
        environment_check,
    ),
)
implementation = Step(
    lambda ctx, plan: print(plan.unwrap()),
    condition=(
        (
            lambda result: result.is_some() and result.unwrap() is True,
            permission,
        )
    ),
)
graph = Graph.from_wiring(
    (
        permission.wire(),
        design.wire(),
        environment_check.wire(),
        prepare.wire(),
        implementation.wire(
            after=(
                prepare_dependency,
                Results(design),
            )
        ),
    )
)
```

The node condition reads permission, the edge condition reads environment_check, and the action reads design. False permission skips implementation; a false edge condition only stops waiting for prepare, without skipping implementation or cancelling prepare. No extra Step wrapper is needed for inline lambdas.

Each use has its own evaluation instance, even when its callable or tuple is
reused. Separate tuples can bind the same predicate to different sources.

## Runtime and compilation boundary

Rust declaration compilation removes static conditions and lowers runtime ones.
The session sees ordinary dependencies, ConditionId values, inputs, and compiled
conditional prerequisites. Python predicates remain in adapter side tables.
StaticCondition objects do not enter the runtime evaluation protocol.

Conceptually, a compiled Node's after list can contain:

```text
CompiledStepRef(probe)
CompiledConditionalDependency(
    node=CompiledStepRef(configure),
    condition=CompiledRuntimeCondition(inputs=(probe,), when=predicate),
)
```

The scheduler waits for inputs, evaluates the predicate, and either ignores configure
or waits for it. The dependency carries its own condition; no synthetic action or
metadata convention is needed.

The same rules apply inside subgraphs. Ordinary action failures propagate through
active conditional edges and inputs, but not inactive edges. See
[execution semantics](./execution.md#failure-propagation).

## Design boundary

Graph owns condition structure, evaluation timing, and scheduling semantics.
Hosts decide which domain relations are conditional and supply predicates.
Predicates return booleans. Operations needing their own dependencies or results
can be composed as Steps rather than being treated as additional predicate semantics.
