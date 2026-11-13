# Conditional steps

English | [简体中文](./zh-CN/conditional_steps.md)

## Purpose

Conditional edges control whether a dependency applies. `Node.condition` controls
whether the Node itself participates. It works for Python leaves, Python Graph
composites, and Graph nodes containing RustGraphStructure.

## Declaration

Defined in [Step](../src/rivlet/model/step.py) and [Graph](../src/rivlet/model/graph.py):

```python
Step(..., label=(...), condition=(...))
```

Label is optional presentation. `condition` accepts `None`, a zero-argument
predicate, or a flat tuple `(predicate, source_step, ...)` with at least one source.
The function comes first; each following element is a Node, not a nested tuple or
an already computed value. Every predicate must return bool. Declaration never
calls it. The public `runtime_condition()` helper is no longer needed.

```python
probe = Step(lambda: 42)
build = Step(
    lambda ctx, result: build_source(),
    condition=((lambda result: result.unwrap() == 42, probe)),
)
graph = Graph.from_wiring(
    (
        probe.wire(),
        build.wire(after=(Results(probe),)),
    )
)
```

Use `condition=lambda: True` without result inputs. Subgraph mounts use the same
forms through `Graph(child_structure, condition=...)`.

## Semantics

### No condition

`condition=None` leaves the Node enabled.

### Static condition

Static rules belong to StaticConditions, not Node.condition:

```python
static = StaticConditions(evaluator=evaluate_target_condition)
graph = (prepare >> static.when(is_android, android) >> build).graph()
child_graph = static.when(
    is_android, Graph(structure=(android_graph).structure, label=("android"))
).graph()
```

False removes the node and its incident edges during compilation, without connecting
its predecessors to successors. The declaration stays unchanged and remains
inspectable. The child Graph is constructed first; this API does not defer Python
graph construction.

Without the DSL:

```python
graph = Graph(
    structure=GraphStructure(
        nodes=((android, checks, build)),
        edges=(
            (
                Edge(source=android, target=build),
                Edge(source=checks, target=build),
            )
        ),
        static=(
            StaticConditions(
                evaluator=evaluate_target_condition,
                conditions={
                    android: is_android,
                    (checks, build): wait_for_checks,
                },
            )
        ),
    )
)
```

Rules reference declared nodes or edges. One mapping covers both, without a second
Node or Graph type. Evaluation happens only during compilation; declarations can be
compiled again. Evaluators should be pure and must not mutate shared state.
A static node rule may coexist with runtime condition: static pruning happens first.

### Runtime condition

Before its inputs are ready, the Node is not ready. A true condition enables normal
execution. A false condition puts the Node in the Skipped terminal state without
invoking its action or producing an action result.

Skipped is a terminal state, not simultaneous Skipped and Finished states.
A false condition is not an action returning False.

For a Graph or RustGraphStructure composite, false means its subgraph is not entered.
A runtime-skipped composite and its descendants become skipped.

## Why a conditional edge is different

```text
A --[condition]--> B
```

This controls whether B waits for A; both nodes remain enabled.

```text
[condition] B
```

This controls whether B participates at all. Do not use one mechanism as a
substitute for the other.

## Compilation and execution

Both public modes use the Rust declaration compiler and ExecutionSession.
Static conditions are resolved during compilation; runtime conditions become node
activation conditions. The session manages node state, including failures,
blocking, and condition-result caching. Python evaluates predicates only when
requested in a batch and returns results through advance.

- Inputs must be satisfied before evaluation; failed inputs block the affected node.
- Once the condition is false, other after prerequisites are not awaited.
- A skipped node no longer blocks downstream work by waiting on unused prerequisites.
- A composite remains a real subgraph boundary, not a no-op step.

In [compile.py](../src/rivlet/compile.py), conditional edges lower to
CompiledConditionalDependency, while runtime Node.condition lowers to
CompiledRuntimeCondition on the node itself. Node conditions are not rewritten
as conditional edges.

Declared subgraph leaves join the execution structure. Their boundary becomes
CompiledComposite, retaining prerequisites, successors, observation, and runtime
condition. Activation releases direct children; false skips the subtree. Empty composites
still retain their dependency boundary.

## Inputs versus action prerequisites

`Results(...)` in `Node.wire(after=...)` binds action arguments only. Conditions declare their own
sources in `(predicate, source_step, ...)`. A node condition waits only for its
sources; after it enables the node, action inputs and `after` constrain execution.
Both kinds of inputs must reference nodes in the current graph and participate
in DAG validation and wiring entry/exit calculation.

```python
probe = Step(probe_cache, label=("probe"))
prepare_a = Step(prepare_a_source, label=("prepare_a"))
prepare_b = Step(prepare_b_source, label=("prepare_b"))
heavy_build = Step(
    lambda ctx, result: build_source(),
    label=("heavy_build"),
    condition=((lambda result: bool(result.unwrap()), probe)),
)
graph = Graph.from_wiring(
    wiring=(
        probe.wire(),
        prepare_a.wire(),
        prepare_b.wire(),
        heavy_build.wire(
            after=(
                prepare_a,
                prepare_b,
                Results(probe),
            )
        ),
    )
)
```

After probe succeeds, the condition can be evaluated without waiting for either
preparation. If false, heavy_build is skipped immediately. If true, it still waits
for prepare_a and prepare_b.

Each compiled condition instance is evaluated at most once per session. Reusing
one predicate or condition tuple at several sites does not merge those instances.
A recorded result is reused while other after prerequisites remain pending.

If the only model were a flat after list, it could not distinguish the early probe
needed for the decision from preparations needed only for execution. That is why
condition inputs are distinct from action prerequisites.

Condition inputs also participate in wiring entry/exit calculation. Do not repeat the same
probe in ordinary after solely to make the condition wait; declare after only for
independent ordering constraints.

## Result inputs

The flat condition tuple declares dependencies and positional predicate arguments:

```python
count_step = Step(lambda: 42)
approval_step = Step(lambda: True)


def should_run(count: Option[object], approval: Option[object]) -> bool:
    return count.unwrap() == 42 and approval.unwrap_or(False) is True


consumer = Step(
    lambda ctx, count, approval: print(count.unwrap(), approval.unwrap()),
    condition=((should_run, count_step, approval_step)),
)
graph = Graph.from_wiring(
    (
        count_step.wire(),
        approval_step.wire(),
        consumer.wire(after=(Results(count_step, approval_step),)),
    )
)
```

When both nodes execute successfully, the predicate receives `Some(42)` and
`Some(True)`. The numbers and booleans are actual action return values, not result
presence markers.

No separate `after` is needed for these inputs. A predicate with no inputs takes
no arguments: `condition=lambda: True`.
Declaring condition or action inputs enables result recording before producers run.
Graphs without inputs retain optional result recording.

`Option[T]` is `Some[T] | Nothing`:

- `Some(value)` contains a recorded result. `Some(None)` is a successful return of None.
- `Nothing()` means no recorded result, such as a skipped Node or a composite with no
  step return value. It is not a failed producer: producer failure blocks the consumer.
- `is_some()` and `is_none()` test presence.
- `unwrap()` returns the value, or raises ValueError for Nothing.
- `unwrap_or(default)` substitutes only for Nothing, never for Some(None) or Some(False).
- `bool(option)` raises TypeError. Test presence or unwrap explicitly.

Inputs reference nodes in the current graph. Reusing that graph under two parents
binds each input to its own mount, not to another execution of the same declaration.
Static pruning of an input is a compilation error, not Nothing.
Input order and duplicates are preserved, so `condition=(predicate, probe, probe)` supplies two
arguments even though they wait for the same producer.

Node is not generic today: annotations should use `Option[object]`, then validate or
narrow the payload as needed. An annotation does not select a different calling
convention. Actions receive ctx followed by their own ordered Option inputs; ctx has
no result lookup API. Each condition waits only for its bound sources. A false node
condition skips the target without waiting for action inputs or conditional-edge
inputs. Producers are independent nodes and are not cancelled by that skip.

## Boundaries

Node conditions and conditional edges share condition types but retain different
semantics. Neither is an exactly-one branch selector. For selection retained in the
declaration, see [static.select](./static_selection.md); for selection before graph
construction, see [select_one](./core_model.md#static-selection-before-construction).
Failure, cancellation, and result recording follow the common
[execution rules](./execution.md).
