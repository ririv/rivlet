from __future__ import annotations

from rivlet import Edge, Execution, Graph, GraphStructure, GraphWiring, graph, step
from rivlet import run as run_execution


def test_graph_decorator_builds_once_without_running_actions() -> None:
    calls: list[str] = []

    @graph
    def pipeline() -> GraphWiring:
        calls.append("declare")

        @step
        def prepare() -> None:
            calls.append("prepare")

        @step
        def build() -> None:
            calls.append("build")

        @step
        def unused() -> None:
            calls.append("unused")

        return prepare >> build

    assert isinstance(pipeline, Graph)
    assert pipeline.label == "pipeline"
    assert calls == ["declare"]
    structure = pipeline.structure
    assert isinstance(structure, GraphStructure)
    assert tuple(node.label for node in structure.nodes) == (
        "prepare",
        "build",
    )
    prepare, build = structure.nodes
    assert structure.edges == (Edge(prepare, build),)

    run_execution(Execution(pipeline))
    run_execution(Execution(pipeline))

    assert calls == ["declare", "prepare", "build", "prepare", "build"]


def test_graph_decorator_composes_external_steps_and_graphs() -> None:
    calls: list[str] = []

    @step
    def prepare() -> None:
        calls.append("prepare")

    @step
    def build() -> None:
        calls.append("build")

    @step
    def validate() -> None:
        calls.append("validate")

    @step
    def cleanup() -> None:
        calls.append("cleanup")

    @graph
    def checks() -> GraphWiring:
        return build >> validate

    @graph
    def pipeline() -> GraphWiring:
        return prepare >> checks

    assert pipeline.structure.nodes == (prepare, checks)
    assert checks.structure.nodes == (build, validate)
    assert pipeline.structure.edges == (Edge(prepare, checks),)

    root = Graph.from_wiring((pipeline.wire(), cleanup.wire(after=(pipeline,))))
    run_execution(Execution(root))

    assert calls == ["prepare", "build", "validate", "cleanup"]


def test_decorated_graph_structure_has_independent_mounts() -> None:
    calls: list[str] = []

    @step
    def prepare() -> None:
        calls.append("prepare")

    @step
    def build() -> None:
        calls.append("build")

    @graph
    def pipeline() -> GraphWiring:
        return prepare >> build

    left = Graph(pipeline.structure, label="left")
    right = Graph(pipeline.structure, label="right")

    assert left.id != right.id
    assert left.structure is pipeline.structure
    assert right.structure is pipeline.structure

    run_execution(Execution((left >> right).graph()))

    assert calls == ["prepare", "build", "prepare", "build"]
