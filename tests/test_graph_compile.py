from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from rivlet import (
    CompiledCompositeRef,
    CompiledConditionalDependency,
    CompiledRuntimeCondition,
    CompiledStepRef,
    ConditionalPrerequisite,
    Edge,
    Execution,
    ExecutionState,
    Graph,
    GraphCompilation,
    GraphError,
    Leaf,
    Results,
    ResultStore,
    StaticCondition,
    StaticConditionEvaluator,
    StaticConditions,
    Step,
    StepPath,
    ThreadRunOptions,
    all_,
    any_,
    compile_graph,
    not_,
    render_execution,
    render_graph,
    render_graph_compilation_mermaid,
    render_graph_mermaid,
    render_live_execution,
)


@dataclass(frozen=True, slots=True)
class _ConditionEvaluator(StaticConditionEvaluator):
    active: frozenset[str]

    def evaluate(self, condition: StaticCondition) -> bool:
        return condition.payload in self.active


def _condition(name: str) -> StaticCondition:
    return StaticCondition(payload=name, description=name)


def test_compile_graph_flattens_nested_graph_and_preserves_structure() -> None:
    stage_graph = _make_stage_graph()
    root_graph = _make_nested_graph(stage_graph=stage_graph)

    compilation = compile_graph(root_graph, root_label="plan")

    assert isinstance(compilation, GraphCompilation)
    assert compilation.label == "plan"
    assert [step.path for step in compilation.steps] == [
        StepPath((0,)),
        StepPath((1, 0)),
        StepPath((1, 1)),
        StepPath((2,)),
    ]
    assert compilation.steps[0].after == ()
    assert compilation.steps[1].after == ()
    assert compilation.steps[2].after == (CompiledStepRef(StepPath((1, 0))),)
    assert compilation.steps[3].after == (CompiledCompositeRef(StepPath((1,))),)
    assert len(compilation.composites) == 1
    stage = compilation.composites[0]
    assert stage.path.labels == ("stage",)
    assert stage.after == (CompiledStepRef(StepPath((0,))),)
    assert stage.children == (
        CompiledStepRef(StepPath((1, 0))),
        CompiledStepRef(StepPath((1, 1))),
    )
    assert compilation.steps[1].path.labels == (
        "stage",
        "configure",
    )
    parent_path = compilation.steps[1].path.parent
    assert parent_path is not None
    assert parent_path.labels == ("stage",)
    assert compilation.steps[1].context.graph_step_paths == {
        stage_graph.steps[0]: StepPath((1, 0)),
        stage_graph.steps[1]: StepPath((1, 1)),
    }
    assert (
        compilation.steps[1].context.ancestor_graph_scopes
        is compilation.steps[2].context.ancestor_graph_scopes
    )
    assert (
        compilation.steps[1].context.ancestor_composite_ids
        is compilation.steps[2].context.ancestor_composite_ids
    )


def test_pruned_native_side_tables_keep_only_compiled_nodes() -> None:
    from rivlet._native import compile_declaration

    removed = Step(
        lambda: None,
        label="removed",
        condition=lambda: False,
    )
    kept = Step(lambda: None, label="kept")
    checked = Step(
        lambda _ctx, *_inputs: None,
        label="checked",
        condition=(_checked_condition := (lambda _ctx: True, kept)),
    )
    graph = Graph(
        steps=(removed, kept, checked),
        static=StaticConditions(
            evaluator=lambda _condition: False,
            conditions={removed: _condition("disabled")},
        ),
        edges=(*Edge.results((kept,), checked),),
    )
    native = compile_declaration(graph, "test")
    assert native.declaration_nodes() == [1, 2]
    assert native.node_step_paths() == [[1], [2]]
    assert native.node_labels() == ["kept", "checked"]
    assert native.source_steps() == [kept, checked]
    assert len(native.actions()) == 2
    assert native.condition_reads() == [[1]]
    compiled = compile_graph(graph, root_label="test")
    assert [step.path for step in compiled.steps] == [StepPath((1,)), StepPath((2,))]
    assert compiled.steps[1].condition is not None
    assert compiled.steps[1].condition.inputs == (compiled.steps[0].ref,)


def test_graph_compilation_rejects_unknown_prerequisite() -> None:
    source_graph = Graph.from_wiring(
        wiring=(Step(label="build", node=Leaf(action=lambda: None)).wire(),),
    )
    compilation = compile_graph(source_graph, root_label="root")
    invalid_step = replace(
        compilation.steps[0],
        after=(CompiledStepRef(StepPath((99,))),),
    )

    with pytest.raises(GraphError, match="unknown node"):
        GraphCompilation(
            source_graph=source_graph,
            label=compilation.label,
            steps=(invalid_step,),
            composites=(),
            roots=(invalid_step.ref,),
        )


def test_graph_compilation_rejects_missing_composite_child() -> None:
    source_graph = _make_nested_graph(stage_graph=_make_stage_graph())
    compilation = compile_graph(source_graph, root_label="root")
    invalid_composite = replace(compilation.composites[0], children=())

    with pytest.raises(GraphError, match="children do not match owned nodes"):
        GraphCompilation(
            source_graph=source_graph,
            label=compilation.label,
            steps=compilation.steps,
            composites=(invalid_composite,),
            roots=compilation.roots,
        )


def test_graph_compilation_rejects_dependency_cycle() -> None:
    source_graph = Graph.from_wiring(
        wiring=(
            Step(label="prepare", node=Leaf(action=lambda: None)).wire(),
            Step(label="build", node=Leaf(action=lambda: None)).wire(),
        ),
    )
    compilation = compile_graph(source_graph, root_label="root")
    prepare, build = compilation.steps

    with pytest.raises(GraphError, match="dependency cycle"):
        GraphCompilation(
            source_graph=source_graph,
            label=compilation.label,
            steps=(
                replace(prepare, after=(build.ref,)),
                replace(build, after=(prepare.ref,)),
            ),
            composites=(),
            roots=compilation.roots,
        )


def test_graph_rejects_runtime_condition_read_cycle() -> None:
    build = Step(label="build", node=lambda: None)
    prepare = Step(
        label="prepare",
        condition=(_prepare_condition := (lambda _ctx: True, build)),
        node=lambda _ctx, *_inputs: None,
    )
    with pytest.raises(GraphError, match="dependency cycle"):
        Graph.from_wiring(
            wiring=(
                prepare.wire(after=(Results(build),)),
                build.wire(after=(prepare,)),
            ),
        )


def test_graph_compilation_rejects_runtime_condition_read_cycle() -> None:
    source_graph = Graph.from_wiring(
        wiring=(
            Step(label="prepare", node=lambda: None).wire(),
            Step(label="build", node=lambda: None).wire(),
        ),
    )
    compilation = compile_graph(source_graph, root_label="root")
    prepare, build = compilation.steps

    with pytest.raises(GraphError, match="dependency cycle"):
        GraphCompilation(
            source_graph=source_graph,
            label=compilation.label,
            steps=(
                replace(
                    prepare,
                    condition=CompiledRuntimeCondition(
                        inputs=(build.ref,),
                        when=lambda _ctx: True,
                    ),
                ),
                replace(build, after=(prepare.ref,)),
            ),
            composites=(),
            roots=compilation.roots,
        )


def test_run_compiled_matches_dynamic_run_and_preserves_local_step_lookup() -> None:
    dynamic_state = ExecutionState(results=ResultStore())
    compiled_state = ExecutionState(results=ResultStore())

    dynamic_plan = Execution(
        root=Step(_make_value_graph()), execution_state=dynamic_state
    )
    compiled_plan = Execution(
        root=Step(_make_value_graph()), execution_state=compiled_state
    )

    dynamic_plan.run(options=ThreadRunOptions(workers=1))
    compiled_plan.run(options=ThreadRunOptions(workers=1), mode="compiled")

    assert dynamic_state.executed_steps == [
        StepPath((0,)),
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath(
            (
                0,
                1,
            )
        ),
        StepPath((0, 1, 0)),
        StepPath((0, 1, 1)),
        StepPath(
            (
                0,
                2,
            )
        ),
    ]
    assert compiled_state.executed_steps == [
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath((0, 1, 0)),
        StepPath((0, 1, 1)),
        StepPath(
            (
                0,
                2,
            )
        ),
    ]
    assert compiled_state.entered_composites == {
        StepPath((0,)),
        StepPath(
            (
                0,
                1,
            )
        ),
    }
    assert compiled_state.completed_composites == {
        StepPath((0,)),
        StepPath(
            (
                0,
                1,
            )
        ),
    }
    assert "stage [graph] [completed]" in render_execution(
        Step(_make_value_graph(), label="root"),
        compiled_state,
        mode="compiled",
    )
    assert dynamic_state.results is not None
    assert compiled_state.results is not None
    assert dynamic_state.results.require(StepPath((0, 1, 1))).value == "seed:configured"
    assert (
        compiled_state.results.require(StepPath((0, 1, 1))).value == "seed:configured"
    )
    assert (
        dynamic_state.results.require(
            StepPath(
                (
                    0,
                    2,
                )
            )
        ).value
        == "packaged"
    )
    assert (
        compiled_state.results.require(
            StepPath(
                (
                    0,
                    2,
                )
            )
        ).value
        == "packaged"
    )


def test_run_compiled_skips_composite_step_by_prefix() -> None:
    state = ExecutionState(
        initial_skip_requests={
            StepPath(
                (
                    0,
                    1,
                )
            )
        },
        results=ResultStore(),
    )
    plan = Execution(root=Step(_make_value_graph()), execution_state=state)

    plan.run(options=ThreadRunOptions(workers=1), mode="compiled")

    assert state.executed_steps == [
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath(
            (
                0,
                2,
            )
        ),
    ]
    assert state.results is not None
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    0,
                )
            )
        ).value
        == "seed"
    )
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    2,
                )
            )
        ).value
        == "packaged"
    )
    assert not state.results.has(StepPath((0, 1, 0)))
    assert not state.results.has(StepPath((0, 1, 1)))


def test_render_graph_renders_terminal_tree() -> None:
    rendered = render_graph(
        _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
    )

    assert rendered == "\n".join(
        [
            "root",
            "├─ prepare",
            "├─ stage [graph] [after: prepare]",
            "│  ├─ configure",
            "│  └─ build [after: configure]",
            "└─ package [after: stage]",
        ]
    )


def test_render_graph_accepts_graph_compilation_as_structure_view() -> None:
    rendered = render_graph(
        compile_graph(
            _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
        ),
        root_label="root",
    )

    assert rendered == "\n".join(
        [
            "root",
            "├─ prepare",
            "├─ stage [graph] [after: prepare]",
            "│  ├─ configure",
            "│  └─ build [after: configure]",
            "└─ package [after: stage]",
        ]
    )


def test_render_graph_can_render_compilation_view_from_graph() -> None:
    rendered = render_graph(
        _make_nested_graph(stage_graph=_make_stage_graph()),
        root_label="root",
        mode="compiled",
    )

    assert rendered == "\n".join(
        [
            "compilation: root",
            "compiled_steps",
            "- prepare",
            "- stage.configure",
            "- stage.build [after: stage.configure]",
            "- package [after: stage]",
            "compiled_composites",
            "- stage [children: stage.configure, stage.build] [after: prepare]",
        ]
    )


def test_render_graph_can_render_compilation_view_from_compilation() -> None:
    rendered = render_graph(
        compile_graph(
            _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
        ),
        root_label="root",
        mode="compiled",
    )

    assert rendered == "\n".join(
        [
            "compilation: root",
            "compiled_steps",
            "- prepare",
            "- stage.configure",
            "- stage.build [after: stage.configure]",
            "- package [after: stage]",
            "compiled_composites",
            "- stage [children: stage.configure, stage.build] [after: prepare]",
        ]
    )


def test_render_graph_mermaid_renders_graph_structure() -> None:
    rendered = render_graph_mermaid(
        _make_nested_graph(stage_graph=_make_stage_graph()),
        root_label="root",
    )

    assert 'subgraph graph_root["root"]' in rendered
    assert 'subgraph graph_root_stage["stage"]' in rendered
    assert 'step_root_1_stage["stage"]' in rendered
    assert "step_root_0_prepare --> step_root_1_stage" in rendered
    assert "step_root_stage_0_configure --> step_root_stage_1_build" in rendered


def test_render_graph_mermaid_accepts_graph_compilation_as_structure_view() -> None:
    rendered = render_graph_mermaid(
        compile_graph(
            _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
        ),
        root_label="root",
    )

    assert 'subgraph graph_root_stage["stage"]' in rendered
    assert "step_root_stage_0_configure --> step_root_stage_1_build" in rendered


def test_render_graph_compilation_mermaid_shows_compiled_graph_and_composites() -> None:
    rendered = render_graph_compilation_mermaid(
        compile_graph(
            _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
        ),
        root_label="root",
    )

    assert "subgraph compiled_graph" in rendered
    assert 'compiled_1["stage.configure"]' in rendered
    assert "subgraph compiled_composites" in rendered
    assert 'compiled_4["stage [composite]"]' in rendered


def test_render_graph_mermaid_can_render_compilation_view_from_graph() -> None:
    rendered = render_graph_mermaid(
        _make_nested_graph(stage_graph=_make_stage_graph()),
        root_label="root",
        mode="compiled",
    )

    assert "subgraph compiled_graph" in rendered
    assert 'compiled_1["stage.configure"]' in rendered
    assert 'compiled_4["stage [composite]"]' in rendered


def test_render_graph_mermaid_can_render_compilation_view_from_compilation() -> None:
    rendered = render_graph_mermaid(
        compile_graph(
            _make_nested_graph(stage_graph=_make_stage_graph()), root_label="root"
        ),
        root_label="root",
        mode="compiled",
    )

    assert "subgraph compiled_graph" in rendered
    assert 'compiled_4["stage [composite]"]' in rendered


def test_render_execution_overlays_runtime_status() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: True))
    build = Step(
        label="build",
        condition=(
            _build_condition := (lambda result: not bool(result.unwrap()), probe)
        ),
        node=Leaf(action=lambda _ctx, *_inputs: None),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            build.wire(after=(Results(probe),)),
        ),
    )
    state = ExecutionState(results=ResultStore())

    Execution(root=Step(graph, label="root"), execution_state=state).run(
        options=ThreadRunOptions(workers=1)
    )

    rendered = render_execution(
        Step(graph, label="root"),
        state,
    )

    assert rendered == "\n".join(
        [
            "execution: root (mode=dynamic)",
            "└─ root [graph] [completed]",
            "   ├─ probe [executed] [result=true]",
            "   └─ build [condition=false] [skipped] [after: probe (action input: 0), probe (condition input: 0)]",
        ]
    )


def test_render_live_execution_hides_not_reached_steps() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: True))
    build = Step(
        label="build",
        condition=(
            _build_condition := (lambda result: not bool(result.unwrap()), probe)
        ),
        node=Leaf(action=lambda _ctx, *_inputs: None),
    )
    package = Step(label="package", node=Leaf(action=lambda _ctx: None))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            build.wire(after=(Results(probe),)),
            package.wire(after=(build,)),
        ),
    )
    state = ExecutionState(results=ResultStore())

    rendered = render_live_execution(
        Step(graph, label="root"),
        state,
    )

    assert rendered == "graph=root mode=dynamic running=idle last=- remaining=3"


def test_compile_graph_resolves_static_conditions() -> None:
    prepare = Step(label="prepare", node=Leaf(action=lambda: None))
    android_shim = Step(label="android_shim", node=Leaf(action=lambda: None))
    archive = Step(label="archive", node=Leaf(action=lambda: None))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            android_shim.wire(),
            archive.wire(
                after=(
                    prepare,
                    android_shim,
                )
            ),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"android"})),
            conditions={(android_shim, archive): _condition("android")},
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert compilation.steps[2].after == (
        CompiledStepRef(StepPath((0,))),
        CompiledStepRef(StepPath((1,))),
    )


def test_compile_graph_removes_static_condition_edge_when_false() -> None:
    prepare = Step(label="prepare", node=lambda: None)
    android_shim = Step(label="android_shim", node=lambda: None)
    archive = Step(label="archive", node=lambda: None)
    graph = Graph.from_wiring(
        (
            prepare.wire(),
            android_shim.wire(),
            archive.wire(
                after=(
                    prepare,
                    android_shim,
                )
            ),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"ios"})),
            conditions={(android_shim, archive): _condition("android")},
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert compilation.steps[2].after == (CompiledStepRef(StepPath((0,))),)


def test_compile_graph_preserves_runtime_condition_edge() -> None:
    probe = Step(label="probe", node=lambda: "miss")
    configure = Step(label="configure", node=lambda: None)

    def condition(result):
        return result.unwrap() == "miss"

    build = Step(label="build", node=lambda _ctx, *_inputs: None)
    graph = Graph.from_wiring(
        (
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure, condition=(condition, probe)
                    ),
                    Results(probe),
                )
            ),
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert compilation.steps[2].after == (
        CompiledStepRef(StepPath((0,))),
        CompiledConditionalDependency(
            node=CompiledStepRef(StepPath((1,))),
            condition=CompiledRuntimeCondition(
                inputs=(CompiledStepRef(StepPath((0,))),),
                when=condition,
            ),
        ),
    )


def test_compile_graph_accepts_static_condition_eval_function() -> None:
    def _eval(condition: StaticCondition) -> bool:
        return condition.payload == _condition("android").payload

    prepare = Step(label="prepare", node=Leaf(action=lambda: None))
    android_shim = Step(label="android_shim", node=Leaf(action=lambda: None))
    archive = Step(label="archive", node=Leaf(action=lambda: None))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            android_shim.wire(),
            archive.wire(
                after=(
                    prepare,
                    android_shim,
                )
            ),
        ),
        static=StaticConditions(
            evaluator=_eval, conditions={(android_shim, archive): _condition("android")}
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert compilation.steps[2].after == (
        CompiledStepRef(StepPath((0,))),
        CompiledStepRef(StepPath((1,))),
    )


def test_compile_graph_handles_builtin_static_condition_combinators() -> None:
    prepare = Step(label="prepare", node=Leaf(action=lambda: None))
    android_shim = Step(label="android_shim", node=Leaf(action=lambda: None))
    shared_only = Step(label="shared_only", node=Leaf(action=lambda: None))
    archive = Step(label="archive", node=Leaf(action=lambda: None))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            android_shim.wire(),
            shared_only.wire(),
            archive.wire(
                after=(
                    prepare,
                    android_shim,
                    shared_only,
                )
            ),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"android"})),
            conditions={
                (android_shim, archive): all_(
                    _condition("android"),
                    not_(_condition("ios")),
                ),
                (shared_only, archive): any_(
                    _condition("ios"),
                    _condition("android"),
                ),
            },
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert compilation.steps[3].after == (
        CompiledStepRef(StepPath((0,))),
        CompiledStepRef(StepPath((1,))),
        CompiledStepRef(StepPath((2,))),
    )


def test_compile_graph_preserves_conditional_dependency_without_gate_step() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda: "miss"))
    configure = Step(label="configure", node=Leaf(action=lambda: "configured"))
    build = Step(label="build", node=Leaf(action=lambda _ctx, *_inputs: "built"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_687 := (
                                lambda result: result.unwrap() == "miss",
                                probe,
                            )
                        ),
                    ),
                    Results(probe),
                )
            ),
        ),
    )

    compilation = Execution(root=Step(graph, label="root")).compile()

    assert [step.path for step in compilation.steps] == [
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath(
            (
                0,
                1,
            )
        ),
        StepPath(
            (
                0,
                2,
            )
        ),
    ]
    build_step = compilation.steps[2]
    assert build_step.after[0] == CompiledStepRef(
        StepPath(
            (
                0,
                0,
            )
        )
    )
    conditional = build_step.after[1]
    assert isinstance(conditional, CompiledConditionalDependency)
    assert conditional.node == CompiledStepRef(
        StepPath(
            (
                0,
                1,
            )
        )
    )
    assert conditional.condition.inputs == (
        CompiledStepRef(
            StepPath(
                (
                    0,
                    0,
                )
            )
        ),
    )


def test_execution_compile_supports_static_and_runtime_conditions() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda: "miss"))
    configure = Step(label="configure", node=Leaf(action=lambda: "configured"))
    android_shim = Step(label="android_shim", node=Leaf(action=lambda: None))
    build = Step(label="build", node=Leaf(action=lambda _ctx, *_inputs: "built"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            android_shim.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_727 := (
                                lambda result: result.unwrap() == "miss",
                                probe,
                            )
                        ),
                    ),
                    android_shim,
                    Results(probe),
                )
            ),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"android"})),
            conditions={(android_shim, build): _condition("android")},
        ),
    )

    compilation = Execution(root=Step(graph, label="root")).compile()

    assert [step.path for step in compilation.steps] == [
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath(
            (
                0,
                1,
            )
        ),
        StepPath(
            (
                0,
                2,
            )
        ),
        StepPath(
            (
                0,
                3,
            )
        ),
    ]
    build_after = compilation.steps[3].after
    assert build_after[0] == CompiledStepRef(
        StepPath(
            (
                0,
                0,
            )
        )
    )
    assert isinstance(build_after[1], CompiledConditionalDependency)
    assert build_after[2] == CompiledStepRef(
        StepPath(
            (
                0,
                2,
            )
        )
    )


def test_render_graph_compiled_shows_conditional_dependency() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda: "miss"))
    configure = Step(label="configure", node=Leaf(action=lambda: "configured"))
    build = Step(label="build", node=Leaf(action=lambda _ctx, *_inputs: "built"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_769 := (
                                lambda result: result.unwrap() == "miss",
                                probe,
                            )
                        ),
                    ),
                    Results(probe),
                )
            ),
        ),
    )

    rendered = render_graph(graph, root_label="root", mode="compiled")

    assert "- build [after: probe, configure (条件边: runtime:" in rendered


def test_compile_graph_removes_static_disabled_step_and_dependency() -> None:
    prepare = Step(label="prepare", node=Leaf(action=lambda: None))
    android_only = Step(
        label="android_only",
        node=Leaf(action=lambda: None),
    )
    archive = Step(label="archive", node=Leaf(action=lambda: None))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            android_only.wire(),
            archive.wire(after=(prepare, android_only)),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"ios"})),
            conditions={android_only: _condition("android")},
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert [step.path for step in compilation.steps] == [StepPath((0,)), StepPath((2,))]
    assert compilation.steps[1].after == (CompiledStepRef(StepPath((0,))),)


def test_compile_graph_preserves_runtime_when_on_leaf_step() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda: False))
    write_cache = Step(
        label="write_cache",
        node=Leaf(action=lambda _ctx, *_inputs: None),
        condition=(
            _write_cache_condition := (lambda result: bool(result.unwrap()), probe)
        ),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            write_cache.wire(after=(Results(probe),)),
        ),
    )

    compilation = Execution(root=Step(graph, label="root")).compile()

    assert compilation.steps[1].path == StepPath(
        (
            0,
            1,
        )
    )
    assert compilation.steps[1].condition is not None


def test_compile_graph_flattens_runtime_conditioned_composite() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda: False))
    configure = Step(label="configure", node=Leaf(action=lambda: None))
    stage = Step(
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
        node=Graph.from_wiring(
            wiring=(configure.wire(),),
        ),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            stage.wire(
                after=(),
            ),
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert [step.path for step in compilation.steps] == [
        StepPath((0,)),
        StepPath((1, 0)),
    ]
    assert len(compilation.composites) == 1
    stage = compilation.composites[0]
    assert stage.path.labels == ("stage",)
    assert stage.after == ()
    assert stage.condition is not None
    assert stage.condition.inputs == (CompiledStepRef(StepPath((0,))),)
    assert stage.children == (CompiledStepRef(StepPath((1, 0))),)


def test_compile_graph_flattens_child_with_conditional_dependency() -> None:
    source = Step(label="source", node=lambda: None)
    target = Step(label="target", node=lambda: None)
    child = Graph.from_wiring(
        wiring=(
            source.wire(),
            target.wire(
                after=(
                    ConditionalPrerequisite(
                        step=source,
                        condition=lambda: False,
                    ),
                )
            ),
        ),
    )
    child_step = Step(label="child", node=child)
    package = Step(label="package", node=lambda: None)
    graph = Graph.from_wiring(
        wiring=(
            child_step.wire(),
            package.wire(after=(child_step,)),
        ),
    )

    compilation = compile_graph(graph, root_label="root")

    assert [step.path for step in compilation.steps] == [
        StepPath((0, 0)),
        StepPath((0, 1)),
        StepPath((1,)),
    ]
    conditional = compilation.steps[1].after[0]
    assert isinstance(conditional, CompiledConditionalDependency)
    assert conditional.node == CompiledStepRef(StepPath((0, 0)))
    assert compilation.steps[2].after == (CompiledCompositeRef(StepPath((0,))),)
    assert compilation.composites[0].children == (
        CompiledStepRef(StepPath((0, 0))),
        CompiledStepRef(StepPath((0, 1))),
    )
    rendered = render_graph(compilation, root_label="root", mode="compiled")
    assert "- child.target [after: child.source (条件边:" in rendered
    assert "- child [children: child.source, child.target]" in rendered


def _make_nested_graph(*, stage_graph: Graph) -> Graph:
    prepare = Step(label="prepare", node=Leaf(action=lambda: None))
    stage = Step(label="stage", node=stage_graph)
    package = Step(label="package", node=Leaf(action=lambda: None))
    return Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
            package.wire(after=(stage,)),
        ),
    )


def _make_stage_graph() -> Graph:
    configure = Step(label="configure", node=Leaf(action=lambda: None))
    build = Step(label="build", node=Leaf(action=lambda: None))
    return Graph.from_wiring(
        wiring=(
            configure.wire(),
            build.wire(after=(configure,)),
        ),
    )


def _make_value_graph() -> Graph:
    seed = "seed"
    prepare = Step(label="prepare", node=Leaf(action=lambda: seed))
    configure = Step(
        label="configure",
        node=Leaf(action=lambda: f"{seed}:configured"),
    )
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx, result: result.unwrap()),
    )
    stage = Step(
        label="stage",
        node=Graph.from_wiring(
            wiring=(configure.wire(), build.wire(after=(Results(configure),))),
        ),
    )
    package = Step(label="package", node=Leaf(action=lambda: "packaged"))
    return Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
            package.wire(after=(stage,)),
        ),
    )
