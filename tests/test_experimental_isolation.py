"""Ordinary Python execution must not depend on the experimental mixed coordinator."""

from rivlet import Execution, Graph, GraphStructure, Step, run
from rivlet._native import (
    HostExecutionSession,
    MixedExecutionSession,
    _rust_condition_fixture,
)
from rivlet.compile import CompiledRustStep


def engine(execution: Execution) -> HostExecutionSession | MixedExecutionSession:
    compilation = execution.compile()
    return compilation._execution_graph.mixed_session(
        [],
        compilation._rust_actions,
        [
            (index, None)
            for index, node in enumerate(compilation._execution_nodes)
            if isinstance(node, CompiledRustStep)
        ],
    )


def test_python_only_session_uses_core_without_mixed_coordination() -> None:
    seen: list[str] = []
    root = Step(lambda: seen.append("python"), condition=lambda: True)
    execution = Execution(root)
    assert isinstance(engine(execution), HostExecutionSession)
    run(execution)
    assert seen == ["python"]


def test_python_dynamic_graph_stays_on_the_core_only_path() -> None:
    seen: list[str] = []
    child = Graph(GraphStructure(nodes=(Step(lambda: seen.append("child")),)))
    execution = Execution(Step(lambda: child))
    assert isinstance(engine(execution), HostExecutionSession)
    run(execution)
    assert seen == ["child"]


def test_native_declarations_explicitly_select_experimental_mixed_path() -> None:
    execution = Execution(Graph(_rust_condition_fixture()))
    assert isinstance(engine(execution), MixedExecutionSession)
    run(execution)
