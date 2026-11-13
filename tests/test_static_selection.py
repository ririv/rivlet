from __future__ import annotations

import asyncio

import pytest

from rivlet import (
    AsyncRunOptions,
    Case,
    CompiledPythonStep,
    Edge,
    Execution,
    Graph,
    GraphError,
    GraphInterface,
    GraphMode,
    GraphStructure,
    MultipleStaticBranchesSelectedError,
    Node,
    NoStaticBranchSelectedError,
    Results,
    StaticCondition,
    StaticConditions,
    StaticSelection,
    Step,
    ThreadRunOptions,
    compile_graph,
    render_graph,
    render_graph_mermaid,
    static,
)


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize("version", [2, 3])
def test_selection_preserves_sequence_and_captures_conditions(
    mode: GraphMode, version: int
) -> None:
    calls: list[str] = []
    platform = "android"
    prepare = Step(lambda: calls.append("prepare"), label=("prepare"))
    modern = Step(lambda: calls.append("modern"), label=("modern"))
    legacy = Step(lambda: calls.append("legacy"), label=("legacy"))
    linux = Step(lambda: pytest.fail("unselected action ran"), label=("linux"))
    finish = Step(lambda: calls.append("finish"), label=("finish"))
    cases = [
        Case(platform == "android" and version >= 3, modern),
        Case(platform == "android" and version < 3, legacy),
        Case(platform == "linux", linux),
    ]
    choice = static.select(cases=cases)
    cases.clear()
    platform = "linux"
    graph = (prepare >> choice >> finish).graph()

    assert not calls
    assert graph.structure.nodes == (prepare, modern, legacy, linux, finish)
    assert len(graph.structure.static_selections[0].cases) == 3
    assert len(compile_graph(graph.structure, root_label="root").steps) == 3
    Execution(Graph(structure=graph.structure)).run(
        ThreadRunOptions(workers=4), mode=mode
    )
    assert calls == ["prepare", "modern" if version >= 3 else "legacy", "finish"]


def test_selection_is_checked_only_at_compile_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selections: list[StaticSelection] = []
    original = StaticSelection.selected_node

    def record(selection: StaticSelection) -> Node:
        selections.append(selection)
        return original(selection)

    monkeypatch.setattr(StaticSelection, "selected_node", record)
    selected = Step(lambda: None, label=("selected"))
    other = Step(lambda: None, label=("other"))
    graph = static.select(cases=(Case(True, selected), Case(False, other))).graph()
    assert not selections
    tree = render_graph(graph.structure, root_label="root")
    mermaid = render_graph_mermaid(graph.structure, root_label="root")
    assert "selected [select: True]" in tree
    assert "other [select: False]" in tree
    assert "[select: True]" in mermaid and "[select: False]" in mermaid
    assert not selections
    for _ in range(2):
        assert len(compile_graph(graph.structure, root_label="root").steps) == 1
    assert selections == [graph.structure.static_selections[0]] * 2


@pytest.mark.parametrize("reverse", [False, True])
def test_overlapping_true_conditions_fail_without_precedence(reverse: bool) -> None:
    cases = (
        Case(True, Step(lambda: pytest.fail("action ran"))),
        Case(True, Step(lambda: pytest.fail("action ran"))),
    )
    graph = static.select(cases=cases[::-1] if reverse else cases).graph()
    assert len(graph.structure.nodes) == 2
    with pytest.raises(
        MultipleStaticBranchesSelectedError, match="multiple are active"
    ):
        compile_graph(graph.structure, root_label="root")


@pytest.mark.parametrize("cases", [(), (Case(False, Step(lambda: None)),)])
def test_no_active_case_fails_at_compile(cases: tuple[Case, ...]) -> None:
    graph = static.select(cases=cases).graph()
    with pytest.raises(NoStaticBranchSelectedError, match="none are active"):
        compile_graph(graph.structure, root_label="root")


@pytest.mark.parametrize("reverse", [False, True])
def test_unique_selection_is_independent_of_case_order(reverse: bool) -> None:
    first = Step(lambda: None)
    selected = Step(lambda: None)
    cases = (Case(False, first), Case(True, selected))
    graph = static.select(cases=cases[::-1] if reverse else cases).graph()
    compiled_step = compile_graph(graph.structure, root_label="root").steps[0]
    assert isinstance(compiled_step, CompiledPythonStep)
    assert compiled_step.action is selected


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_invalid_selection_prevents_all_actions(mode: GraphMode) -> None:
    calls: list[str] = []
    prepare = Step(lambda: calls.append("prepare"))
    graph = (prepare >> static.select(cases=())).graph()
    with pytest.raises(NoStaticBranchSelectedError):
        Execution(Graph(structure=graph.structure)).run(mode=mode)
    assert not calls


def test_direct_graph_declaration_matches_dsl() -> None:
    first = Step(lambda: None)
    second = Step(lambda: None)
    finish = Step(lambda: None)
    cases = (Case(False, first), Case(True, second))
    direct = Graph(
        structure=GraphStructure(
            nodes=(first, second, finish),
            edges=(Edge(first, finish), Edge(second, finish)),
            static_selections=(StaticSelection(cases),),
        )
    )
    dsl = (static.select(cases=cases) >> finish).graph()
    assert compile_graph(direct.structure, root_label="root") == compile_graph(
        dsl.structure, root_label="root"
    )


def test_nested_unselected_rules_are_not_evaluated() -> None:
    invalid = static.select(cases=()).graph()
    valid = static.select(cases=(Case(True, Step(lambda: None)),)).graph()
    graph = static.select(
        cases=(
            Case(False, Graph(structure=invalid.structure)),
            Case(True, Graph(structure=valid.structure)),
        )
    ).graph()
    assert len(compile_graph(graph.structure, root_label="root").steps) == 1


def test_selection_in_statically_pruned_graph_is_not_evaluated() -> None:
    invalid = static.select(cases=()).graph()
    graph = (
        StaticConditions(evaluator=lambda _condition: False)
        .when(
            StaticCondition(payload=False, description="disabled"),
            Graph(structure=invalid.structure),
        )
        .graph()
    )
    assert not compile_graph(graph.structure, root_label="root").steps


def test_graph_can_be_mounted_twice_without_sharing_step_paths() -> None:
    calls: list[str] = []
    chosen = static.select(
        cases=(Case(True, Step(lambda: calls.append("chosen"))),)
    ).graph()
    graph = (
        Graph(structure=chosen.structure) | Graph(structure=chosen.structure)
    ).graph()
    compiled = compile_graph(graph.structure, root_label="root")
    assert len({step.path for step in compiled.steps}) == 2
    Execution(Graph(structure=graph.structure)).run(ThreadRunOptions(workers=2))
    assert calls == ["chosen", "chosen"]


def test_parallel_selections_keep_independent_decisions() -> None:
    first = Step(lambda: None)
    second = Step(lambda: None)
    graph = (
        static.select(cases=(Case(True, first), Case(False, Step(lambda: None))))
        | static.select(cases=(Case(False, Step(lambda: None)), Case(True, second)))
    ).graph()
    compiled_steps = compile_graph(graph.structure, root_label="root").steps
    assert len(compiled_steps) == 2
    assert {
        step.action for step in compiled_steps if isinstance(step, CompiledPythonStep)
    } == {
        first,
        second,
    }


def test_runtime_condition_and_result_binding_remain_independent() -> None:
    calls: list[object] = []
    source = Step(lambda: 42)
    selected = Step(
        lambda _ctx, result: calls.append(result.unwrap()), condition=(lambda: True)
    )
    choice = static.select(cases=(Case(True, selected),))
    graph = Graph.from_wiring(
        (source.wire(), selected.wire(after=(Results(source),))),
        static_selections=choice.static_selections,
    )
    Execution(Graph(structure=graph.structure)).run()
    assert calls == [42]


def test_result_input_from_unselected_case_is_rejected() -> None:
    selected = Step(lambda: None)
    unselected = Step(lambda: None)
    consume = Step(lambda _ctx, result: result.unwrap())
    choice = static.select(cases=(Case(True, selected), Case(False, unselected)))
    graph = Graph.from_wiring(
        (
            selected.wire(),
            unselected.wire(),
            consume.wire(after=(Results(unselected),)),
        ),
        static_selections=choice.static_selections,
    )
    with pytest.raises(GraphError, match="result input was removed"):
        compile_graph(graph.structure, root_label="root")


def test_selected_subgraph_retains_its_interface_ports() -> None:
    ready = "ready"
    calls: list[str] = []
    produce = Step(lambda: calls.append("produce"))
    consume = Step(lambda: calls.append("consume"))
    provider = Graph(
        structure=GraphStructure(
            nodes=(produce,), interface=GraphInterface(outputs={ready: produce})
        )
    )
    case = Case(True, Graph(structure=provider.structure))
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={ready: consume})
        )
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(case.node, consumer),
            edges=(Edge(case.node, consumer, ports=ready),),
            static_selections=(StaticSelection((case,)),),
        )
    )
    Execution(Graph(structure=graph.structure)).run(ThreadRunOptions(workers=2))
    assert calls == ["produce", "consume"]


def test_required_port_cannot_use_unselected_provider() -> None:
    ready = "ready"
    produce = Step(lambda: None)
    consume = Step(lambda: None)
    provider = Graph(
        structure=GraphStructure(
            nodes=(produce,), interface=GraphInterface(outputs={ready: produce})
        )
    )
    provider_case = Case(False, Graph(structure=provider.structure))
    selected = Step(lambda: None)
    consumer = Graph(
        structure=GraphStructure(
            nodes=(consume,), interface=GraphInterface(inputs={ready: consume})
        )
    )
    graph = Graph(
        structure=GraphStructure(
            nodes=(provider_case.node, selected, consumer),
            edges=(Edge(provider_case.node, consumer, ports=ready),),
            static_selections=(StaticSelection((provider_case, Case(True, selected))),),
        )
    )
    with pytest.raises(GraphError, match="inactive provider"):
        compile_graph(graph.structure, root_label="root")


def test_selected_empty_subgraph_preserves_composite_ordering() -> None:
    calls: list[str] = []
    first = Step(lambda: calls.append("first"))
    last = Step(lambda: calls.append("last"))
    graph = (
        first
        >> static.select(cases=(Case(True, Graph(structure=GraphStructure())),))
        >> last
    ).graph()
    Execution(Graph(structure=graph.structure)).run(ThreadRunOptions(workers=2))
    assert calls == ["first", "last"]


def test_runtime_created_graph_uses_the_same_static_selection() -> None:
    calls: list[str] = []

    def create_graph() -> GraphStructure:
        return (
            static.select(
                cases=(
                    Case(False, Step(lambda: pytest.fail("unselected action ran"))),
                    Case(True, Step(lambda: calls.append("selected"))),
                ),
            )
            .graph()
            .structure
        )

    Execution(Graph(structure=GraphStructure(nodes=(Step(create_graph),)))).run()
    assert calls == ["selected"]


def test_async_driver_executes_only_the_selected_case() -> None:
    calls: list[str] = []

    async def action() -> None:
        await asyncio.sleep(0)
        calls.append("selected")

    graph = static.select(
        cases=(
            Case(False, Step(lambda: pytest.fail("unselected action ran"))),
            Case(True, Step(action)),
        ),
    ).graph()
    asyncio.run(
        Execution(Graph(structure=graph.structure)).arun(AsyncRunOptions(concurrency=2))
    )
    assert calls == ["selected"]
