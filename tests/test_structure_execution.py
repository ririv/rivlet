from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from execution_support import run_async_execution

from rivlet import (
    Execution,
    Graph,
    GraphError,
    GraphStructure,
    StaticCondition,
    StaticConditions,
    Step,
    compile_graph,
    export_graph,
)
from rivlet import arun as arun_execution
from rivlet import run as run_execution


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_run_compilation_is_available_before_actions(asynchronous) -> None:
    evaluations = []
    observed = []

    def evaluate(_condition):
        evaluations.append(True)
        return True

    def action():
        observed.append(execution.compilation)

    async def async_action():
        observed.append(execution.compilation)

    step = Step(async_action if asynchronous else action)
    graph = (
        StaticConditions(evaluator=evaluate)
        .when(StaticCondition(object(), "private"), step)
        .graph()
    )
    execution = Execution(Graph(structure=graph.structure))
    assert execution.compilation is None
    prepared = execution.compile()
    assert execution.compilation is prepared
    assert len(evaluations) == 1
    if asynchronous:
        asyncio.run(arun_execution(execution))
    else:
        run_execution(execution)
    actual = execution.compilation
    assert actual is prepared
    assert observed == [actual]
    assert len(evaluations) == 1
    export_graph(actual)
    assert len(evaluations) == 1
    execution.compile()
    assert execution.compilation is actual
    assert len(evaluations) == 1


def test_failed_preparation_is_not_committed_and_can_be_retried() -> None:
    evaluations = []

    def evaluate(_condition):
        evaluations.append(True)
        if len(evaluations) == 1:
            raise ValueError("preparation failed")
        return True

    graph = (
        StaticConditions(evaluator=evaluate)
        .when(StaticCondition(object(), "condition"), Step(lambda: None))
        .graph()
    )
    execution = Execution(Graph(structure=graph.structure))
    with pytest.raises(GraphError, match="preparation failed"):
        execution.compile()
    assert execution.compilation is None
    prepared = execution.compile()
    run_execution(execution)
    assert execution.compilation is prepared
    assert len(evaluations) == 2


def test_new_execution_and_standalone_compilation_make_fresh_static_decisions() -> None:
    enabled = [True]
    graph = (
        StaticConditions(evaluator=lambda _condition: enabled[0])
        .when(StaticCondition(object(), "condition"), Step(lambda: None))
        .graph()
    )
    first = Execution(Graph(structure=graph.structure))
    prepared = first.compile()
    assert len(prepared.steps) == 1
    enabled[0] = False
    run_execution(first)
    assert first.compile() is prepared
    assert len(first.compile().steps) == 1
    assert Execution(Graph(structure=graph.structure)).compile().steps == ()
    assert compile_graph(graph.structure, root_label="independent").steps == ()


def test_failed_action_keeps_this_run_compilation() -> None:
    def fail():
        raise ValueError("private failure")

    execution = Execution(Graph(structure=GraphStructure(nodes=(Step(fail),))))
    with pytest.raises(ValueError, match="private failure"):
        run_execution(execution)
    assert execution.compilation is not None
    document = export_graph(execution.compilation)
    assert "private failure" not in str(document)
    assert len(document["nodes"]) == 2


def test_run_with_async_options_records_same_arun_compilation() -> None:
    execution = Execution(Graph(structure=GraphStructure()))
    run_async_execution(execution)
    assert execution.compilation is not None


def test_concurrent_preparation_commits_one_successful_compilation() -> None:
    evaluations = []

    def evaluate(_condition):
        evaluations.append(True)
        time.sleep(0.01)
        return True

    graph = (
        StaticConditions(evaluator=evaluate)
        .when(StaticCondition(object(), "condition"), Step(lambda: None))
        .graph()
    )
    execution = Execution(Graph(structure=graph.structure))
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared = list(pool.map(lambda _: execution.compile(), range(4)))
    assert all(compilation is prepared[0] for compilation in prepared)
    assert evaluations == [True]
