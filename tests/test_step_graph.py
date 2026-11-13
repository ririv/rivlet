from __future__ import annotations

import pytest

from rivlet import (
    Edge,
    Execution,
    Graph,
    GraphError,
    GraphWiring,
    Leaf,
    Step,
    StepRunContext,
    StepWiring,
    compile_graph,
    render_graph,
    render_graph_mermaid,
    step,
)


def _noop(_run_ctx: StepRunContext) -> None:
    pass


def test_graph_allows_duplicate_step_labels() -> None:
    graph = Graph.from_wiring(
        wiring=(
            Step(label="build", node=Leaf(action=_noop)).wire(),
            Step(label="build", node=Leaf(action=_noop)).wire(),
        ),
    )

    assert tuple(step.label for step in graph.steps) == ("build", "build")


def test_graph_rejects_duplicate_step_objects() -> None:
    step = Step(label="build", node=Leaf(action=_noop))
    with pytest.raises(GraphError):
        Graph.from_wiring(
            wiring=(
                step.wire(),
                step.wire(),
            ),
        )


def test_graph_rejects_unknown_dependencies() -> None:
    foreign = Step(label="prepare", node=Leaf(action=_noop))
    with pytest.raises(GraphError):
        Graph.from_wiring(
            wiring=(
                Step(
                    label="build",
                    node=Leaf(action=_noop),
                ).wire(after=(foreign,)),
            ),
        )


def test_graph_rejects_dependency_cycles() -> None:
    configure = Step(label="configure", node=Leaf(action=_noop))
    build = Step(label="build", node=Leaf(action=_noop))
    with pytest.raises(GraphError, match="dependency cycle"):
        Graph.from_wiring(
            wiring=(
                configure.wire(after=(build,)),
                build.wire(after=(configure,)),
            ),
        )


def test_graph_allows_nested_graphs() -> None:
    configure = Step(label="configure", node=Leaf(action=_noop))
    build = Step(label="build", node=Leaf(action=_noop))
    nested = Graph.from_wiring(
        wiring=(
            configure.wire(),
            build.wire(after=(configure,)),
        ),
    )
    prepare = Step(label="prepare", node=Leaf(action=_noop))
    stage = Step(label="stage", node=nested)
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
        ),
    )

    assert root.steps[1].node is nested


def test_step_can_be_declared_with_different_predecessors() -> None:
    configure = Step(label="configure", node=Leaf(action=_noop))
    restore_cache = Step(label="restore_cache", node=Leaf(action=_noop))
    compile_step = Step(label="compile", node=Leaf(action=_noop))

    normal_graph = Graph.from_wiring(
        wiring=(
            configure.wire(),
            StepWiring(step=compile_step, after=(configure,)),
        )
    )
    cached_graph = Graph.from_wiring(
        wiring=(
            restore_cache.wire(),
            StepWiring(step=compile_step, after=(restore_cache,)),
        )
    )

    assert normal_graph.steps[1] is compile_step
    assert cached_graph.steps[1] is compile_step
    assert normal_graph.edges == (Edge(source=configure, target=compile_step),)
    assert cached_graph.edges == (Edge(source=restore_cache, target=compile_step),)


def test_graph_wiring_sequences_steps() -> None:
    prepare = Step(label="prepare", node=Leaf(action=_noop))
    compile_step = Step(label="compile", node=Leaf(action=_noop))
    install = Step(label="install", node=Leaf(action=_noop))

    wiring = prepare >> compile_step >> install
    graph = wiring.graph()

    assert isinstance(wiring, GraphWiring)
    assert graph.steps == (prepare, compile_step, install)
    assert graph.edges == (
        Edge(source=prepare, target=compile_step),
        Edge(source=compile_step, target=install),
    )


def test_graph_wiring_fans_out_and_in() -> None:
    prepare = Step(label="prepare", node=Leaf(action=_noop))
    compile_a = Step(label="compile_a", node=Leaf(action=_noop))
    compile_b = Step(label="compile_b", node=Leaf(action=_noop))
    install = Step(label="install", node=Leaf(action=_noop))

    graph = (prepare >> (compile_a | compile_b) >> install).graph()

    assert graph.steps == (prepare, compile_a, compile_b, install)
    assert graph.edges == (
        Edge(source=prepare, target=compile_a),
        Edge(source=prepare, target=compile_b),
        Edge(source=compile_a, target=install),
        Edge(source=compile_b, target=install),
    )


def test_graph_wiring_preserves_independent_branches() -> None:
    prepare_a = Step(label="prepare_a", node=Leaf(action=_noop))
    compile_a = Step(label="compile_a", node=Leaf(action=_noop))
    prepare_b = Step(label="prepare_b", node=Leaf(action=_noop))
    compile_b = Step(label="compile_b", node=Leaf(action=_noop))

    graph = ((prepare_a >> compile_a) | (prepare_b >> compile_b)).graph()

    assert graph.steps == (prepare_a, compile_a, prepare_b, compile_b)
    assert graph.edges == (
        Edge(source=prepare_a, target=compile_a),
        Edge(source=prepare_b, target=compile_b),
    )


def test_result_edges_define_wiring_boundaries() -> None:
    probe = Step(label="probe", node=Leaf(action=_noop))
    conditional = Step(
        label="conditional",
        node=Leaf(action=_noop),
        condition=(
            _conditional_condition := (lambda result: bool(result.unwrap()), probe)
        ),
    )

    wiring = GraphWiring(
        steps=(probe, conditional),
        edges=(*Edge.results((probe,), conditional),),
    )

    assert wiring.entries == (probe,)
    assert wiring.exits == (conditional,)


def test_graph_wiring_wraps_graph_operand_without_expanding_it() -> None:
    child_prepare = Step(label="child_prepare", node=Leaf(action=_noop))
    child_compile = Step(label="child_compile", node=Leaf(action=_noop))
    child = (child_prepare >> child_compile).graph()
    before = Step(label="before", node=Leaf(action=_noop))
    after = Step(label="after", node=Leaf(action=_noop))

    outer = (before >> child >> after).graph()

    assert len(outer.steps) == 3
    mounted_child = outer.steps[1]
    assert mounted_child.label is None
    assert mounted_child.node is child
    assert child_prepare not in outer.steps
    assert child_compile not in outer.steps
    assert outer.edges == (
        Edge(source=before, target=mounted_child),
        Edge(source=mounted_child, target=after),
    )


def test_graph_wiring_wraps_unlabeled_graph_operand() -> None:
    before = Step(label="before", node=Leaf(action=_noop))
    child = Graph(steps=(Step(node=Leaf(action=_noop)),))
    after = Step(label="after", node=Leaf(action=_noop))

    graph = (before >> child >> after).graph()
    compilation = compile_graph(graph, root_label="root")

    mounted_child = graph.steps[1]
    assert mounted_child.label is None
    assert mounted_child.node is child
    assert compilation.composites[0].graph_label == "1"
    assert compilation.steps[1].path.labels == ("1", "1.0")


@pytest.mark.parametrize("left", (Step(_noop), Leaf(_noop), Graph()))
@pytest.mark.parametrize("right", (Step(_noop), Leaf(_noop), Graph()))
def test_graph_wiring_composes_each_node_kind(
    left: Step | Leaf | Graph, right: Step | Leaf | Graph
) -> None:
    sequence = (left >> right).graph()
    parallel = (left | right).graph()

    for graph in (sequence, parallel):
        assert len(graph.steps) == 2
        for declared, mounted in zip((left, right), graph.steps, strict=True):
            if isinstance(declared, Step):
                assert mounted is declared
            else:
                assert mounted.node is declared
                assert mounted.label is None
                assert mounted.condition is None
    assert sequence.edges == (Edge(*sequence.steps),)
    assert parallel.edges == ()


def test_repeated_bare_nodes_have_distinct_mounts_and_execute_each_time() -> None:
    calls: list[str] = []
    leaf = Leaf(lambda: calls.append("leaf"))
    child = Graph(steps=(Step(lambda: calls.append("child")),))

    graph = (leaf >> child >> leaf >> child).graph()

    assert len({mounted.id for mounted in graph.steps}) == 4
    assert tuple(mounted.node for mounted in graph.steps) == (leaf, child, leaf, child)
    Execution(Step(graph)).run()
    assert calls == ["leaf", "child", "leaf", "child"]


def test_composing_normalized_wiring_preserves_existing_mounts() -> None:
    leaf = Leaf(_noop)
    child = Graph()
    wiring = leaf >> child
    finish = Step(_noop, label="finish")

    combined = (wiring >> finish).graph()

    assert combined.steps == (*wiring.steps, finish)
    assert combined.edges == (*wiring.edges, Edge(wiring.steps[-1], finish))


@pytest.mark.parametrize("enabled", (True, False))
def test_graph_wiring_step_mounts_complete_graph_with_its_own_condition(
    enabled: bool,
) -> None:
    calls: list[str] = []
    prepare = Step(lambda: calls.append("prepare"), label="prepare")
    cleanup = Leaf(lambda: calls.append("cleanup"))
    wiring = prepare >> cleanup

    root = wiring.step(label="pipeline", condition=lambda: enabled)
    another_mount = wiring.step()

    assert root.label == "pipeline"
    assert isinstance(root.node, Graph)
    assert root.node.steps == wiring.steps
    assert root.node.edges == wiring.edges
    assert root.id != another_mount.id
    assert another_mount.label is None
    assert another_mount.condition is None
    Execution(root).run()
    assert calls == (["prepare", "cleanup"] if enabled else [])


def test_graph_from_wiring_accepts_composed_wiring() -> None:
    prepare = Step(label="prepare", node=Leaf(action=_noop))
    compile_step = Step(label="compile", node=Leaf(action=_noop))

    graph = Graph.from_wiring(
        prepare >> compile_step,
    )

    assert graph.steps == (prepare, compile_step)
    assert graph.edges == (Edge(source=prepare, target=compile_step),)


def test_graph_wiring_rejects_duplicate_step_object() -> None:
    step = Step(label="shared", node=Leaf(action=_noop))

    with pytest.raises(GraphError, match="contains the same step more than once"):
        (step | step).graph()


def test_step_decorator_uses_function_name_and_composes() -> None:
    @step
    def prepare() -> None:
        pass

    @step
    def compile_step() -> None:
        pass

    graph = (prepare >> compile_step).graph()

    assert prepare.label == "prepare"
    assert compile_step.label == "compile_step"
    assert graph.steps == (prepare, compile_step)
    assert graph.edges == (Edge(source=prepare, target=compile_step),)


def test_step_function_accepts_normalized_leaf_action() -> None:
    action = Leaf(action=_noop)

    declaration = step(action)

    assert declaration.label == "_noop"
    assert declaration.node is action


def test_step_function_wraps_structure_without_inventing_a_label() -> None:
    child = Graph()

    declaration = step(child)

    assert declaration.label is None
    assert declaration.node is child


def test_graph_step_wraps_graph_with_mount_parameters() -> None:
    child = Graph()

    def condition():
        return True

    default_mount = child.step(label="child")
    configured_mount = child.step(label="build", condition=condition)

    assert default_mount.label == "child"
    assert default_mount.node is child
    assert configured_mount.label == "build"
    assert configured_mount.condition is not None
    assert configured_mount.condition.when is condition
    assert configured_mount.node is child


def test_step_function_leaves_action_without_a_name_unlabeled() -> None:
    class CallableAction:
        def __call__(self) -> None:
            pass

    declaration = step(CallableAction())

    assert declaration.label is None


def test_step_function_leaves_lambda_unlabeled() -> None:
    declaration = step(lambda: None)

    assert declaration.label is None


def test_unlabeled_steps_use_mounted_ids_in_compiled_and_rendered_graphs() -> None:
    first = Step(node=lambda: None)
    second = Step(node=lambda: None)
    graph = (first >> second).graph()

    compilation = compile_graph(graph, root_label="root")
    tree = render_graph(graph, root_label="root")
    mermaid = render_graph_mermaid(graph, root_label="root")

    assert tuple(item.path.labels[-1] for item in compilation.steps) == (
        "0",
        "1",
    )
    assert "├─ 0" in tree
    assert "└─ 1 [after: 0]" in tree
    assert '["0"]' in mermaid
    assert '["1"]' in mermaid
