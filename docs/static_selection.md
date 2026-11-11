# Static selection inside a graph

English | [简体中文](./zh-CN/static_selection.md)

`static.select()` retains every candidate in the Graph declaration and requires
exactly one true condition during compilation. It returns `GraphWiring` for
`>>` / `|` composition. There is no executable selector action or Step,
GraphTemplate, or second Step/Graph API.

## Complete conditions and branch contents

```python
from rivlet import Case, GraphExecution, Step, static

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
).graph(label="platform workflow")

GraphExecution(graph).run()
```

This runs `prepare → modern → finish`. Each `Case(condition, component)` has two arguments:

- `condition` is a required, complete static bool, not a matching value or predicate.
  Strings, functions, and other non-boolean objects are not implicitly made true.
- `component` is a Step or Graph. Graphs use normal `.step()` mounting semantics.
  `case.step` is the actual node available for explicit wiring.
- `static` is the `rivlet.static` module, not a `StaticConditions` evaluator instance.

There is no separate subject, pattern, guard, wildcard, or chained condition method.
Conditions require neither preclassification nor an extra `StaticCondition` object.
Existing `StaticConditions.when/edge` rules with explicit evaluators are unchanged.

Python computes conditions and constructs all candidates before the API call.
The declaration retains booleans; compilation performs the exact-one check.
This does not defer expressions or factories, and does not read Step results.
The `cases` sequence is copied to a tuple; later list mutation does not affect it.

## Exactly one true condition, with no precedence

Each graph mount that participates in compilation is checked independently:

- No true conditions raise `NoStaticBranchSelectedError`.
- One true condition retains that branch.
- Multiple true conditions raise `MultipleStaticBranchesSelectedError`.

Both errors derive from `StaticSelectionError`. Declaration order is not precedence,
and the compiler does not prove exhaustiveness over the entire input space.
Construction and declaration rendering do not select cases. Selection errors occur
during compilation, before actions execute.

`Case(True, component)` means the condition is true, not “use this if all others
fail.” Another true condition still produces a conflict. There is no implicit fallback.

## Direct declarations and composition boundaries

The same rules and topology can be declared without the DSL:

```python
from rivlet import Case, Graph, GraphExecution, StaticSelection, Step, Edge

first = Step(lambda: print("first"), label="first")
second = Step(lambda: print("second"), label="second")
finish = Step(lambda: print("finish"), label="finish")
cases = (Case(False, first), Case(True, second))

graph = Graph(
    steps=tuple(case.step for case in cases) + (finish,),
    edges=tuple(Edge(case.step, finish) for case in cases),
    static_selections=(StaticSelection(cases=cases),),
)
GraphExecution(graph).run()
```

`Graph.from_wiring()` and `.graph()` also accept `static_selections=`.
The DSL already propagates selections, so do not pass them twice.
Selection removes unselected nodes and incident edges without inferring or
reconnecting dependencies. `prepare >> static.select(...) >> finish` preserves
ordering because exactly one branch remains. A selected empty subgraph retains
its normal composite lifecycle, not a no-op action.

Rules and nested selections in unselected subgraphs are not evaluated. Neither
are selections under a statically pruned parent. Constructed candidates still
follow normal declaration validation. A scope cannot declare the same Step twice;
reuse a child Graph across cases to obtain independent mounts, not implicit node
merging. Each compilation and mount selects independently, without caching a
chosen branch in the declaration.

Selection does not automatically expose or forward interface ports.
Use `case.step` to bind ports explicitly. An active consumer requiring a port
or result from an unselected case causes a compilation error, not silent removal
of the requirement. Runtime conditions never trigger reselection. Result bindings
and thread/async execution retain their existing rules.

## Inspect declarations and compiled graphs

Tree and Mermaid declarations retain all candidates with `[select: True]` or
`[select: False]`. These are captured boolean results, not reconstructed Python
expressions. Compiled rendering (`mode="compiled"`) compiles first and shows only
retained nodes. Unselected cases emit no execution events or runtime Skipped state.

For C cases, selection takes O(C) time and O(C) space for the inactive-node index.
Each active mount transfers its selection to Rust in one batch; Rust consumes
opaque node identities, not business conditions.

The graph-external `select_one()` remains available for choosing ordinary values
or factories before construction. It does not retain unselected declarations;
see [static selection before construction](./core_model.md#static-selection-before-construction).
