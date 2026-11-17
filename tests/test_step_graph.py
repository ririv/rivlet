from __future__ import annotations

import pytest

from rivlet import (
    Edge,
    Execution,
    Graph,
    GraphError,
    GraphWiring,
    NodeRunContext,
    NodeWiring,
    Step,
    render_graph,
    render_graph_mermaid,
    step,
)
from rivlet import run as run_execution
from rivlet.compile import _compile_graph_scope


def _noop(_run_ctx: NodeRunContext) -> None:
    pass


def test_graph_allows_duplicate_step_labels() -> None:
    graph = Graph.from_wiring(
        wiring=(
            Step(_noop, label=("build")).wire(),
            Step(_noop, label=("build")).wire(),
        ),
    )

    assert tuple(step.label for step in graph.nodes) == ("build", "build")


def test_graph_rejects_duplicate_step_objects() -> None:
    step = Step(_noop, label=("build"))
    with pytest.raises(GraphError):
        Graph.from_wiring(
            wiring=(
                step.wire(),
                step.wire(),
            ),
        )


def test_graph_rejects_unknown_dependencies() -> None:
    foreign = Step(_noop, label=("prepare"))
    with pytest.raises(GraphError):
        Graph.from_wiring(
            wiring=(Step(_noop, label=("build")).wire(after=(foreign,)),),
        )


def test_graph_rejects_dependency_cycles() -> None:
    configure = Step(_noop, label=("configure"))
    build = Step(_noop, label=("build"))
    with pytest.raises(GraphError, match="dependency cycle"):
        Graph.from_wiring(
            wiring=(
                configure.wire(after=(build,)),
                build.wire(after=(configure,)),
            ),
        )


def test_graph_allows_nested_graphs() -> None:
    configure = Step(_noop, label=("configure"))
    build = Step(_noop, label=("build"))
    nested = Graph.from_wiring(
        wiring=(
            configure.wire(),
            build.wire(after=(configure,)),
        ),
    )
    prepare = Step(_noop, label=("prepare"))
    stage = Graph(
        nodes=nested.nodes,
        edges=nested.edges,
        observation=nested.observation,
        static=nested.static,
        static_selections=nested.static_selections,
        interface=nested.interface.declaration,
        label=("stage"),
    )
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
        ),
    )

    assert root.nodes[1] is stage
    assert stage.nodes is nested.nodes


def test_step_can_be_declared_with_different_predecessors() -> None:
    configure = Step(_noop, label=("configure"))
    restore_cache = Step(_noop, label=("restore_cache"))
    compile_step = Step(_noop, label=("compile"))

    normal_graph = Graph.from_wiring(
        wiring=(
            configure.wire(),
            NodeWiring(node=compile_step, after=(configure,)),
        )
    )
    cached_graph = Graph.from_wiring(
        wiring=(
            restore_cache.wire(),
            NodeWiring(node=compile_step, after=(restore_cache,)),
        )
    )

    assert normal_graph.nodes[1] is compile_step
    assert cached_graph.nodes[1] is compile_step
    assert normal_graph.edges == (Edge(source=configure, target=compile_step),)
    assert cached_graph.edges == (Edge(source=restore_cache, target=compile_step),)


def test_graph_wiring_sequences_steps() -> None:
    prepare = Step(_noop, label=("prepare"))
    compile_step = Step(_noop, label=("compile"))
    install = Step(_noop, label=("install"))

    wiring = prepare >> compile_step >> install
    graph = wiring.graph()

    assert isinstance(wiring, GraphWiring)
    assert graph.nodes == (prepare, compile_step, install)
    assert graph.edges == (
        Edge(source=prepare, target=compile_step),
        Edge(source=compile_step, target=install),
    )


def test_graph_wiring_fans_out_and_in() -> None:
    prepare = Step(_noop, label=("prepare"))
    compile_a = Step(_noop, label=("compile_a"))
    compile_b = Step(_noop, label=("compile_b"))
    install = Step(_noop, label=("install"))

    graph = (prepare >> (compile_a | compile_b) >> install).graph()

    assert graph.nodes == (prepare, compile_a, compile_b, install)
    assert graph.edges == (
        Edge(source=prepare, target=compile_a),
        Edge(source=prepare, target=compile_b),
        Edge(source=compile_a, target=install),
        Edge(source=compile_b, target=install),
    )


def test_graph_wiring_preserves_independent_branches() -> None:
    prepare_a = Step(_noop, label=("prepare_a"))
    compile_a = Step(_noop, label=("compile_a"))
    prepare_b = Step(_noop, label=("prepare_b"))
    compile_b = Step(_noop, label=("compile_b"))

    graph = ((prepare_a >> compile_a) | (prepare_b >> compile_b)).graph()

    assert graph.nodes == (prepare_a, compile_a, prepare_b, compile_b)
    assert graph.edges == (
        Edge(source=prepare_a, target=compile_a),
        Edge(source=prepare_b, target=compile_b),
    )


def test_result_edges_define_wiring_boundaries() -> None:
    probe = Step(_noop, label=("probe"))
    conditional = Step(
        _noop,
        label=("conditional"),
        condition=(
            _conditional_condition := (lambda result: bool(result.unwrap()), probe)
        ),
    )

    wiring = GraphWiring(
        nodes=(probe, conditional),
        edges=(*Edge.results((probe,), conditional),),
    )

    assert wiring.entries == (probe,)
    assert wiring.exits == (conditional,)


def test_graph_wiring_wraps_graph_operand_without_expanding_it() -> None:
    child_prepare = Step(_noop, label=("child_prepare"))
    child_compile = Step(_noop, label=("child_compile"))
    child = (child_prepare >> child_compile).graph()
    before = Step(_noop, label=("before"))
    after = Step(_noop, label=("after"))

    outer = (before >> child >> after).graph()

    assert len(outer.nodes) == 3
    mounted_child = outer.nodes[1]
    assert mounted_child.label is None
    assert mounted_child is child
    assert child_prepare not in outer.nodes
    assert child_compile not in outer.nodes
    assert outer.edges == (
        Edge(source=before, target=mounted_child),
        Edge(source=mounted_child, target=after),
    )


def test_graph_wiring_wraps_unlabeled_graph_operand() -> None:
    before = Step(_noop, label=("before"))
    child = Graph(nodes=(Step(_noop),))
    after = Step(_noop, label=("after"))

    graph = (before >> child >> after).graph()
    compilation = _compile_graph_scope(graph, root_label="root")

    mounted_child = graph.nodes[1]
    assert mounted_child.label is None
    assert mounted_child is child
    assert compilation.composites[0].graph_label == "1"
    assert compilation.steps[1].path.labels == ("1", "1.0")


@pytest.mark.parametrize("left", (Step(_noop), Graph()))
@pytest.mark.parametrize("right", (Step(_noop), Graph()))
def test_graph_wiring_composes_each_node_kind(
    left: Step | Graph, right: Step | Graph
) -> None:
    sequence = (left >> right).graph()
    parallel = (left | right).graph()

    for graph in (sequence, parallel):
        assert len(graph.nodes) == 2
        for declared, mounted in zip((left, right), graph.nodes, strict=True):
            assert mounted is declared
    assert sequence.edges == (Edge(*sequence.nodes),)
    assert parallel.edges == ()


def test_shared_structure_has_distinct_graph_nodes_and_executes_each_time() -> None:
    calls: list[str] = []

    def action() -> None:
        calls.append("leaf")

    structure = Graph(nodes=(Step(lambda: calls.append("child")),))
    left = Graph(
        nodes=structure.nodes,
        edges=structure.edges,
        observation=structure.observation,
        static=structure.static,
        static_selections=structure.static_selections,
        interface=structure.interface.declaration,
    )
    right = Graph(
        nodes=structure.nodes,
        edges=structure.edges,
        observation=structure.observation,
        static=structure.static,
        static_selections=structure.static_selections,
        interface=structure.interface.declaration,
    )
    graph = (Step(action) >> left >> Step(action) >> right).graph()

    assert len({mounted.id for mounted in graph.nodes}) == 4
    assert left is not right
    assert left.nodes is right.nodes
    run_execution(Execution(graph))
    assert calls == ["leaf", "child", "leaf", "child"]


def test_composing_normalized_wiring_preserves_existing_mounts() -> None:
    step = Step(_noop)
    child = Graph()
    wiring = step >> child
    finish = Step(_noop, label=("finish"))

    combined = (wiring >> finish).graph()

    assert combined.nodes == (*wiring.nodes, finish)
    assert combined.edges == (*wiring.edges, Edge(wiring.nodes[-1], finish))


@pytest.mark.parametrize("enabled", (True, False))
def test_graph_wiring_step_mounts_complete_graph_with_its_own_condition(
    enabled: bool,
) -> None:
    calls: list[str] = []
    prepare = Step(lambda: calls.append("prepare"), label=("prepare"))
    cleanup = Step(lambda: calls.append("cleanup"))
    wiring = prepare >> cleanup

    root = wiring.graph(label=("pipeline"), condition=(lambda: enabled))
    another_mount = wiring.graph()

    assert root.label == "pipeline"
    assert isinstance(root, Graph)
    assert root.nodes == wiring.nodes
    assert root.edges == wiring.edges
    assert root.id != another_mount.id
    assert another_mount.label is None
    assert another_mount.condition is None
    run_execution(Execution(root))
    assert calls == (["prepare", "cleanup"] if enabled else [])


def test_graph_from_wiring_accepts_composed_wiring() -> None:
    prepare = Step(_noop, label=("prepare"))
    compile_step = Step(_noop, label=("compile"))

    graph = Graph.from_wiring(
        prepare >> compile_step,
    )

    assert graph.nodes == (prepare, compile_step)
    assert graph.edges == (Edge(source=prepare, target=compile_step),)


def test_graph_wiring_rejects_duplicate_step_object() -> None:
    step = Step(_noop, label=("shared"))

    with pytest.raises(GraphError, match="contains the same node more than once"):
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
    assert graph.nodes == (prepare, compile_step)
    assert graph.edges == (Edge(source=prepare, target=compile_step),)


def test_step_function_accepts_normalized_step_action() -> None:
    declaration = step(_noop)

    assert declaration.label == "_noop"
    assert declaration.action is _noop


def test_step_function_wraps_structure_without_inventing_a_label() -> None:
    child = Graph()

    declaration = Graph(
        nodes=child.nodes,
        edges=child.edges,
        observation=child.observation,
        static=child.static,
        static_selections=child.static_selections,
        interface=child.interface.declaration,
    )

    assert declaration.label is None
    assert declaration is not child
    assert declaration.nodes is child.nodes


def test_graph_step_wraps_graph_with_mount_parameters() -> None:
    child = Graph()

    def condition():
        return True

    default_mount = Graph(
        nodes=child.nodes,
        edges=child.edges,
        observation=child.observation,
        static=child.static,
        static_selections=child.static_selections,
        interface=child.interface.declaration,
        label=("child"),
    )
    configured_mount = Graph(
        nodes=child.nodes,
        edges=child.edges,
        observation=child.observation,
        static=child.static,
        static_selections=child.static_selections,
        interface=child.interface.declaration,
        label=("build"),
        condition=(condition),
    )

    assert default_mount.label == "child"
    assert default_mount.nodes is child.nodes
    assert configured_mount.label == "build"
    assert configured_mount.condition is not None
    assert configured_mount.condition.when is condition
    assert configured_mount.nodes is child.nodes


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
    first = Step(lambda: None)
    second = Step(lambda: None)
    graph = (first >> second).graph()

    compilation = _compile_graph_scope(graph, root_label="root")
    tree = render_graph(graph, root_label="root")
    mermaid = render_graph_mermaid(graph, root_label="root")

    assert tuple(item.path.labels[-1] for item in compilation.steps) == (
        "0",
        "1",
    )
    assert "├─ 0" in tree
    assert "└─ 0.1 [after: 0.0]" in tree
    assert '["0"]' in mermaid
    assert '["0.1"]' in mermaid
