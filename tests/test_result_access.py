from __future__ import annotations

import pytest

from rivlet import (
    Graph,
    GraphExecution,
    GraphExecutionState,
    GraphObservationEvent,
    GraphObservationMessages,
    GraphResultStore,
    GraphResultView,
    Option,
    Step,
    StepPath,
    StepRunContext,
    ThreadGraphRunOptions,
)


def test_result_view_is_live_and_preserves_none_results() -> None:
    store = GraphResultStore()
    view = store.view
    step_path = StepPath((0,))
    assert not view.has(step_path)
    assert view.get(step_path) is None

    store.record(step_path, None)
    assert view is store.view
    assert view.has(step_path)
    assert view.require(step_path).value is None


def test_record_rejects_overwriting_an_existing_result() -> None:
    store = GraphResultStore()
    step_path = StepPath((0,))
    store.record(step_path, "first")
    with pytest.raises(RuntimeError, match="step result already recorded"):
        store.record(step_path, "second")
    assert store.view.require(step_path).value == "first"


def test_state_snapshot_keeps_its_result_records() -> None:
    state = GraphExecutionState(results=GraphResultStore())
    first, second = StepPath((0,)), StepPath((1,))
    state.record_step(first, ("first",))
    state.record_result(first, "first")
    snapshot = state.snapshot()
    state.record_step(second, ("second",))
    state.record_result(second, "second")

    assert isinstance(state.results, GraphResultView)
    assert isinstance(snapshot.results, GraphResultView)
    assert snapshot.results.require(first).value == "first"
    assert not snapshot.results.has(second)
    assert state.results.has(second)


def test_actions_receive_bound_results_and_observers_receive_read_only_view() -> None:
    state = GraphExecutionState(results=GraphResultStore())
    received: list[GraphResultView] = []
    prepare = Step(lambda: "source")

    def consume(_ctx: StepRunContext, source: Option[object]) -> str:
        assert source.unwrap() == "source"
        return "output"

    finish = Step(consume)

    class Observer:
        def emit(self, event: GraphObservationEvent) -> None:
            assert isinstance(event.results, GraphResultView)
            received.append(event.results)
            result = event.result(finish)
            assert result is not None
            assert result.value == "output"

    execution = GraphExecution(
        graph=Graph.from_wiring(
            (prepare.wire(), finish.wire(inputs={finish.action: (prepare,)})),
            observation=GraphObservationMessages(after_success="done"),
        ),
        execution_state=state,
    )
    execution.run(ThreadGraphRunOptions(workers=2, observer=Observer()))
    assert len(received) == 1
    assert all(view is state.results for view in received)
    assert execution.require_result(finish).value == "output"
