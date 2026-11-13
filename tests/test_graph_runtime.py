from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture

from rivlet import (
    AsyncRunOptions,
    ConditionalPrerequisite,
    Execution,
    ExecutionControl,
    ExecutionState,
    ExecutionStopped,
    Graph,
    GraphMode,
    GraphObservationEvent,
    GraphObservationMessages,
    Leaf,
    Option,
    Results,
    ResultStore,
    SerialGraphExecutor,
    StaticCondition,
    StaticConditionEvaluator,
    StaticConditions,
    Step,
    StepPath,
    StepRunContext,
    ThreadRunOptions,
    TreeFileExecutionOutput,
    render_execution,
    render_live_execution,
)
from rivlet._native import NativeGraph, _native_process_graph
from rivlet.compile import compile_graph
from rivlet.runtime.compiled import CompiledExecutionHost


@dataclass(frozen=True, slots=True)
class _ConditionEvaluator(StaticConditionEvaluator):
    active: frozenset[str]

    def evaluate(self, condition: StaticCondition) -> bool:
        return condition.payload in self.active


def _condition(name: str) -> StaticCondition:
    return StaticCondition(payload=name, description=name)


def _process_graph(
    *,
    label: str,
    program: str,
    arguments: list[str],
    cwd: Path,
) -> NativeGraph:
    """Build a native process graph through the private adapter test boundary."""

    return _native_process_graph(
        [(label, program, arguments, str(cwd), dict(os.environ))],
        [],
    )


def test_compiled_host_forwards_only_new_skip_requests() -> None:
    first = Step(lambda: None, label="first")
    second = Step(lambda: None, label="second")
    compilation = compile_graph(Graph(steps=(first, second)), root_label="root")
    state = ExecutionState(initial_skip_requests={StepPath((0,))})
    host = CompiledExecutionHost(
        compilation,
        state,
        observer=None,
        run_leaf=lambda _step: None,
    )

    assert host.skipped_nodes() == [0]
    assert host.skipped_nodes() == []

    state.request_skip(StepPath((1,)))
    assert host.skipped_nodes() == [1]
    assert host.skipped_nodes() == []


@dataclass(slots=True)
class _RecordingActionLog:
    active_path: Path | None = None
    appended: list[tuple[Path, str]] = field(default_factory=list)

    @contextmanager
    def scope(self, path: Path) -> Iterator[None]:
        if self.active_path is not None:
            raise RuntimeError("action log scope is already active")
        self.active_path = path
        try:
            yield
        finally:
            self.active_path = None

    def append(self, path: Path, text: str) -> None:
        self.appended.append((path, text))


def test_execution_control_requests_stop_once() -> None:
    control = ExecutionControl()
    calls: list[str] = []

    def _handle_stop() -> None:
        calls.append("stop")

    control.register_stop_handler(_handle_stop)
    control.register_stop_handler(_handle_stop)
    control.request_stop()
    control.request_stop()

    assert control.stop_requested()
    assert calls == ["stop"]


def test_execution_rejects_pre_stopped_execution_control() -> None:
    calls: list[str] = []
    control = ExecutionControl()
    control.request_stop()
    plan = Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(Step(node=lambda: calls.append("run")).wire(),),
            ),
            label="root",
        ),
        execution_control=control,
    )

    with pytest.raises(ExecutionStopped):
        plan.run()

    assert calls == []


def test_execution_executes_python_and_native_actions_in_one_graph(
    tmp_path: Path,
) -> None:
    output = tmp_path / "mixed.txt"
    prepare = Step(lambda: output.write_text("python\n"), label="prepare")
    native = Step(
        _process_graph(
            label="native-process",
            program=sys.executable,
            arguments=[
                "-c",
                "from pathlib import Path; import sys; "
                "p = Path(sys.argv[1]); p.write_text(p.read_text() + 'rust\\n')",
                str(output),
            ],
            cwd=tmp_path,
        ),
        label="native",
    )
    verify = Step(
        lambda: output.read_text().splitlines(),
        label="verify",
    )
    plan = Execution(
        root=Step((prepare >> native >> verify).graph(), label="mixed"),
        execution_state=ExecutionState(results=ResultStore()),
    )

    state = plan.run(ThreadRunOptions(workers=2))

    assert plan.require_result(verify).value == ["python", "rust"]
    assert state.completed_steps == {StepPath((0,))} | frozenset(
        {
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
            StepPath(
                (
                    0,
                    2,
                )
            ),
        }
    )


def test_native_action_writes_to_bound_component_log(tmp_path: Path) -> None:
    log_path = tmp_path / "component.log"
    native = Step(
        _process_graph(
            label="native-process",
            program=sys.executable,
            arguments=[
                "-u",
                "-c",
                "import sys; print('native stdout'); print('native stderr', file=sys.stderr)",
            ],
            cwd=tmp_path,
        ),
        label="native",
    )
    component = Graph(steps=(native,)).step(label="component")
    plan = Execution(
        root=Step(
            Graph(
                steps=(component,),
            ),
            label="native-log",
        ),
        step_log_paths={component: log_path},
    )

    plan.run()

    assert log_path.read_text().splitlines() == ["native stdout", "native stderr"]


def test_step_log_paths_resolve_at_construction(tmp_path: Path) -> None:
    first = Step(lambda: None, label="first")
    second = Step(lambda: None, label="second")
    first_log = tmp_path / "first.log"
    second_log = tmp_path / "second.log"

    plan = Execution(
        root=Step(
            Graph(
                steps=(first, second),
            ),
            label="logs",
        ),
        step_log_paths={
            first: first_log,
            second: second_log,
        },
    )

    assert (
        plan.execution_state.get_step_log_path(
            StepPath(
                (
                    0,
                    0,
                )
            )
        )
        == first_log
    )
    assert (
        plan.execution_state.get_step_log_path(
            StepPath(
                (
                    0,
                    1,
                )
            )
        )
        == second_log
    )


def test_native_action_failure_is_a_graph_action_failure(tmp_path: Path) -> None:
    native = Step(
        _process_graph(
            label="native-process",
            program=sys.executable,
            arguments=["-c", "raise SystemExit(7)"],
            cwd=tmp_path,
        ),
        label="native",
    )

    with pytest.raises(RuntimeError, match=r"exited with status .*7"):
        Execution(
            root=Step(
                Graph(
                    steps=(native,),
                ),
                label="native-failure",
            )
        ).run()


def test_async_graph_executor_runs_native_action_without_blocking_model_changes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "async-native.txt"
    native = Step(
        _process_graph(
            label="native-process",
            program=sys.executable,
            arguments=[
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('async')",
                str(output),
            ],
            cwd=tmp_path,
        ),
        label="native",
    )

    Execution(
        root=Step(
            Graph(
                steps=(native,),
            ),
            label="async-native",
        )
    ).run(AsyncRunOptions(concurrency=1))

    assert output.read_text() == "async"


def test_execution_run_executes_nested_graphs_in_topological_order() -> None:
    calls: list[str] = []
    configure = Step(
        label="configure",
        node=Leaf(action=lambda _ctx: calls.append("configure")),
    )
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx: calls.append("build")),
    )
    nested = Graph.from_wiring(
        wiring=(
            configure.wire(),
            build.wire(after=(configure,)),
        ),
    )
    prepare = Step(
        label="prepare",
        node=Leaf(action=lambda _ctx: calls.append("prepare")),
    )
    stage = Step(label="stage", node=nested)
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
        ),
    )

    state = Execution(
        root=Step(root, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert calls == ["prepare", "configure", "build"]
    assert state.executed_steps == [
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
    ]


def test_execution_state_cannot_be_reused() -> None:
    plan = Execution(root=Step(Graph(), label="root"))

    plan.run()

    with pytest.raises(RuntimeError, match="graph execution state cannot be reused"):
        plan.run()


def test_step_action_preserves_default_argument_capture() -> None:
    captured: list[str] = []

    def _capture(value: str = "captured") -> None:
        captured.append(value)

    Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(
                    Step(
                        label="capture",
                        node=_capture,
                    ).wire(),
                ),
            ),
            label="root",
        ),
    ).run()

    assert captured == ["captured"]


def test_execution_run_skips_marked_steps() -> None:
    calls: list[str] = []
    prepare = Step(
        label="prepare",
        node=Leaf(action=lambda _ctx: calls.append("prepare")),
    )
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx: calls.append("build")),
    )
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            build.wire(after=(prepare,)),
        ),
    )

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
    Execution(
        root=Step(graph, label="root"),
        execution_state=state,
    ).run(options=ThreadRunOptions(workers=1))

    assert calls == ["prepare"]
    assert state.executed_steps == [
        StepPath((0,)),
        StepPath(
            (
                0,
                0,
            )
        ),
    ]
    assert state.skipped_steps == {
        StepPath(
            (
                0,
                1,
            )
        )
    }


def test_execution_rejects_unknown_initial_skip_request() -> None:
    graph = Graph(steps=(Step(lambda: None, label="build"),))

    with pytest.raises(RuntimeError, match="unknown graph skip requests: 0.1"):
        Execution(
            root=Step(graph),
            execution_state=ExecutionState(
                initial_skip_requests={
                    StepPath(
                        (
                            0,
                            1,
                        )
                    )
                }
            ),
        ).run(options=ThreadRunOptions(workers=1))


def test_execution_state_rejects_skip_after_step_start() -> None:
    state = ExecutionState()
    step_path = StepPath((0,))
    state.record_step(step_path, ("root", "build"))

    with pytest.raises(RuntimeError, match="cannot skip an already started step: 0"):
        state.request_skip(step_path)


def test_execution_run_uses_provided_state() -> None:
    calls: list[str] = []
    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="build",
                node=Leaf(action=lambda _ctx: calls.append("build")),
            ).wire(),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert calls == ["build"]
    assert state.executed_steps == [
        StepPath((0,)),
        StepPath(
            (
                0,
                0,
            )
        ),
    ]


def test_execution_run_records_sync_step_results() -> None:
    prepare = Step(label="prepare", node=Leaf(action=lambda _ctx: "prepared"))
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx, result: f"{result.unwrap()}:built"),
    )
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            build.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert state.results is not None
    assert (
        state.results.get(
            StepPath(
                (
                    0,
                    0,
                )
            )
        )
        is not None
    )
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    0,
                )
            )
        ).value
        == "prepared"
    )
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    1,
                )
            )
        ).value
        == "prepared:built"
    )


def test_execution_run_records_async_step_results() -> None:
    async def _build(_run_ctx: StepRunContext) -> str:
        return "built"

    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="build",
                node=Leaf(action=_build),
            ).wire(),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

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
        == "built"
    )


def test_execution_run_records_none_result() -> None:
    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="build",
                node=Leaf(action=lambda _ctx: None),
            ).wire(),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert state.results is not None
    assert state.results.has(
        StepPath(
            (
                0,
                0,
            )
        )
    )
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    0,
                )
            )
        ).value
        is None
    )


def test_execution_run_does_not_record_results_for_skipped_steps() -> None:
    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="build",
                node=Leaf(action=lambda _ctx: "built"),
            ).wire(),
        ),
    )
    state = ExecutionState(
        initial_skip_requests={
            StepPath(
                (
                    0,
                    0,
                )
            )
        },
        results=ResultStore(),
    )

    Execution(
        root=Step(graph, label="root"),
        execution_state=state,
    ).run()

    assert state.results is not None
    assert not state.results.has(
        StepPath(
            (
                0,
                0,
            )
        )
    )


def test_execution_run_does_not_record_results_for_failed_steps() -> None:
    def _fail(_run_ctx: StepRunContext) -> object:
        raise RuntimeError("boom")

    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="build",
                node=Leaf(action=_fail),
            ).wire(),
        ),
    )

    state = ExecutionState(results=ResultStore())
    try:
        Execution(
            root=Step(graph, label="root"),
            execution_state=state,
        ).run()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert state.results is not None
    assert not state.results.has(
        StepPath(
            (
                0,
                0,
            )
        )
    )


def test_parallel_graph_failure_does_not_request_global_stop() -> None:
    execution_control = ExecutionControl()
    survivor_completed = threading.Event()

    def _fail(_run_ctx: StepRunContext) -> object:
        raise RuntimeError("boom")

    def _survive(_run_ctx: StepRunContext) -> object:
        assert not execution_control.stop_requested()
        survivor_completed.set()
        return None

    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="fail",
                node=Leaf(action=_fail),
            ).wire(),
            Step(
                label="wait",
                node=Leaf(action=_survive),
            ).wire(),
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        Execution(
            root=Step(graph, label="root"),
            execution_state=ExecutionState(results=ResultStore()),
            execution_control=execution_control,
        ).run(options=ThreadRunOptions(workers=2))

    assert survivor_completed.is_set()
    assert not execution_control.stop_requested()


def test_async_graph_failure_waits_for_started_steps() -> None:
    survivor_started = asyncio.Event()
    survivor_completed = False

    async def _fail() -> None:
        await survivor_started.wait()
        raise RuntimeError("boom")

    async def _survive() -> None:
        nonlocal survivor_completed
        survivor_started.set()
        await asyncio.sleep(0.01)
        survivor_completed = True

    graph = Graph.from_wiring(
        wiring=(
            Step(label="fail", node=_fail).wire(),
            Step(label="survive", node=_survive).wire(),
        ),
    )

    plan = Execution(root=Step(graph, label="root"))
    with pytest.raises(RuntimeError, match="boom"):
        plan.run(options=AsyncRunOptions(concurrency=2))

    assert survivor_completed
    assert not plan.execution_control.stop_requested()


@pytest.mark.parametrize(
    "options",
    [
        ThreadRunOptions(workers=1),
        AsyncRunOptions(concurrency=1),
    ],
    ids=["thread", "async"],
)
def test_execution_stop_does_not_dispatch_ready_successor(
    options: ThreadRunOptions | AsyncRunOptions,
) -> None:
    calls: list[str] = []
    execution_control = ExecutionControl()

    def _request_stop() -> None:
        calls.append("first")
        execution_control.request_stop()

    first = Step(label="first", node=_request_stop)
    second = Step(label="second", node=lambda: calls.append("second"))
    graph = Graph.from_wiring(
        wiring=(
            first.wire(),
            second.wire(after=(first,)),
        ),
    )

    with pytest.raises(ExecutionStopped):
        Execution(
            root=Step(graph),
            execution_control=execution_control,
        ).run(options=options)

    assert calls == ["first"]


def test_step_failure_does_not_cancel_shared_execution_control() -> None:
    calls: list[str] = []
    execution_control = ExecutionControl()

    def _fail() -> None:
        raise RuntimeError("boom")

    first_plan = Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(Step(node=_fail).wire(),),
            ),
            label="build",
        ),
        execution_control=execution_control,
    )
    second_plan = Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(Step(node=lambda: calls.append("package")).wire(),),
            ),
            label="package",
        ),
        execution_control=execution_control,
    )

    with pytest.raises(RuntimeError, match="boom"):
        first_plan.run()
    second_plan.run()

    assert calls == ["package"]


def test_execution_reads_nested_result_by_step_ref() -> None:
    def _nested(_run_ctx: StepRunContext) -> str:
        return "nested-value"

    nested_step = Step(label="nested", node=Leaf(action=_nested))
    nested = Graph.from_wiring(
        wiring=(nested_step.wire(),),
    )
    stage = Step(label="stage", node=nested)
    root = Graph.from_wiring(
        wiring=(stage.wire(),),
    )

    plan = Execution(
        root=Step(root, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    )
    plan.run()

    assert plan.require_result(nested_step).value == "nested-value"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_reused_subgraph_mounts_have_distinct_step_instances(
    mode: GraphMode,
) -> None:
    shared_step = Step(
        label="shared",
        node=lambda run_ctx: str(run_ctx.step_path),
    )
    shared_graph = Graph.from_wiring(
        wiring=(shared_step.wire(),),
    )
    first = Step(label="first", node=shared_graph)
    second = Step(label="second", node=shared_graph)
    root = Graph.from_wiring(
        wiring=(first.wire(), second.wire()),
    )
    plan = Execution(
        root=Step(root, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    )

    state = plan.run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert state.results is not None
    assert state.results.require(StepPath((0, 0, 0))).value == "0.0.0"
    assert state.results.require(StepPath((0, 1, 0))).value == "0.1.0"
    with pytest.raises(RuntimeError, match="mounted more than once"):
        plan.require_result(shared_step)


def test_dynamic_subgraph_captures_explicit_result_input() -> None:
    def _prepare(_run_ctx: StepRunContext) -> str:
        return "prepared"

    prepare = Step(label="prepare", node=Leaf(action=_prepare))

    def expand(_ctx: StepRunContext, result: Option[object]) -> Graph:
        nested_step = Step(label="nested", node=lambda: f"nested:{result.unwrap()}")
        return Graph.from_wiring(
            (nested_step.wire(),),
        )

    stage = Step(label="stage", node=expand)
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Step(root, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert state.results is not None
    assert state.results.require(StepPath((0, 1, 0))).value == "nested:prepared"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_condition_true_waits_for_source_step(mode: GraphMode) -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: "miss"))
    setup = Step(label="setup", node=Leaf(action=lambda _ctx: "setup"))
    configure = Step(
        label="configure",
        node=Leaf(action=lambda _ctx: "configured"),
    )
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx, result: f"{result.unwrap()}:built"),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            setup.wire(),
            configure.wire(after=(setup,)),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_764 := (
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

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    3,
                )
            )
        ).value
        == "miss:built"
    )
    assert state.executed_steps.index(
        StepPath(
            (
                0,
                2,
            )
        )
    ) < state.executed_steps.index(
        StepPath(
            (
                0,
                3,
            )
        )
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_step_condition_is_evaluated_once_per_run(mode: GraphMode) -> None:
    evaluations = 0

    def _condition(_ctx: object) -> bool:
        nonlocal evaluations
        evaluations += 1
        return True

    probe = Step(label="probe", node=lambda: "ready")
    first = Step(label="first", node=lambda: None)
    second = Step(label="second", node=lambda: None)
    target = Step(
        label="target",
        condition=(_target_condition := (_condition, probe)),
        node=lambda _ctx, *_inputs: None,
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            first.wire(),
            second.wire(),
            target.wire(
                after=(
                    first,
                    second,
                    Results(probe),
                )
            ),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert evaluations == 1


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_edge_condition_is_evaluated_once_per_run(mode: GraphMode) -> None:
    evaluations = 0

    def _condition(_ctx: object) -> bool:
        nonlocal evaluations
        evaluations += 1
        return True

    probe = Step(label="probe", node=lambda: None)
    first = Step(label="first", node=lambda: None)
    second = Step(label="second", node=lambda: None)
    source = Step(label="source", node=lambda: None)
    target = Step(label="target", node=lambda _ctx, *_inputs: None)
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            first.wire(),
            second.wire(),
            source.wire(),
            target.wire(
                after=(
                    first,
                    second,
                    ConditionalPrerequisite(
                        step=source,
                        condition=(_edge_condition_848 := (_condition, probe)),
                    ),
                    Results(probe),
                )
            ),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert evaluations == 1


def test_execution_render_does_not_reevaluate_runtime_condition() -> None:
    evaluations = 0

    def _condition() -> bool:
        nonlocal evaluations
        evaluations += 1
        return True

    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="target",
                condition=_condition,
                node=lambda: None,
            ).wire(),
        ),
    )
    plan = Execution(root=Step(graph, label="root"))

    state = plan.run()
    rendered = render_execution(
        Step(graph, label="root"),
        state,
    )

    assert evaluations == 1
    assert "target [condition=true] [executed]" in rendered


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_condition_false_does_not_require_source_result(
    mode: GraphMode,
) -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: "hit"))
    setup = Step(label="setup", node=Leaf(action=lambda _ctx: "setup"))
    configure = Step(
        label="configure",
        node=Leaf(action=lambda _ctx: "configured"),
    )
    build = Step(
        label="build",
        node=Leaf(action=lambda _ctx, result: result.unwrap()),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            setup.wire(),
            configure.wire(after=(setup,)),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_917 := (
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

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert (
        state.results.require(
            StepPath(
                (
                    0,
                    3,
                )
            )
        ).value
        == "hit"
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_condition_false_does_not_wait_for_source(mode: GraphMode) -> None:
    build_started = threading.Event()

    def _configure() -> str:
        if not build_started.wait(timeout=1):
            raise AssertionError("build incorrectly waited for configure")
        return "configured"

    def _build(_ctx: StepRunContext, _result: Option[object]) -> str:
        build_started.set()
        return "built"

    probe = Step(label="probe", node=lambda: "hit")
    configure = Step(label="configure", node=_configure)
    build = Step(label="build", node=_build)
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_961 := (
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

    Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(
        options=ThreadRunOptions(workers=3),
        mode=mode,
    )

    assert build_started.is_set()


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_parent_waits_for_all_steps_in_conditionally_connected_child(
    mode: GraphMode,
) -> None:
    source_finished = threading.Event()

    def _source() -> None:
        time.sleep(0.1)
        source_finished.set()

    def _package() -> None:
        if not source_finished.is_set():
            raise AssertionError("parent completed before all child steps")

    source = Step(label="source", node=_source)
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
    package = Step(label="package", node=_package)
    graph = Graph.from_wiring(
        wiring=(
            child_step.wire(),
            package.wire(after=(child_step,)),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=ThreadRunOptions(workers=4),
        mode=mode,
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_parent_prerequisite_blocks_all_conditionally_connected_child_steps(
    mode: GraphMode,
) -> None:
    prepare_finished = threading.Event()

    def _prepare() -> None:
        time.sleep(0.1)
        prepare_finished.set()

    def _target() -> None:
        if not prepare_finished.is_set():
            raise AssertionError("child step bypassed parent prerequisite")

    source = Step(label="source", node=lambda: None)
    target = Step(label="target", node=_target)
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
    prepare = Step(label="prepare", node=_prepare)
    child_step = Step(label="child", node=child)
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            child_step.wire(after=(prepare,)),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=ThreadRunOptions(workers=4),
        mode=mode,
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_empty_child_graph_preserves_parent_dependency_boundary(
    mode: GraphMode,
) -> None:
    release_prepare = threading.Event()
    prepare_completed = threading.Event()
    package_started_early = False

    def _prepare() -> None:
        if not release_prepare.wait(timeout=1):
            raise RuntimeError("prepare release timed out")
        prepare_completed.set()

    def _package() -> None:
        nonlocal package_started_early
        package_started_early = not prepare_completed.is_set()

    timer = threading.Timer(0.05, release_prepare.set)
    timer.start()
    try:
        prepare = Step(label="prepare", node=_prepare)
        empty_stage = Step(label="empty_stage", node=Graph())
        package = Step(label="package", node=_package)
        Execution(
            root=Step(
                Graph.from_wiring(
                    wiring=(
                        prepare.wire(),
                        empty_stage.wire(after=(prepare,)),
                        package.wire(after=(empty_stage,)),
                    ),
                ),
                label="root",
            ),
        ).run(options=ThreadRunOptions(workers=2), mode=mode)
    finally:
        timer.cancel()

    assert not package_started_early


def test_compiled_skipped_composite_preserves_prerequisite_boundary() -> None:
    prepare_completed = threading.Event()

    def _prepare() -> None:
        time.sleep(0.05)
        prepare_completed.set()

    def _package() -> None:
        if not prepare_completed.is_set():
            raise AssertionError("skipped composite bypassed its prerequisite")

    prepare = Step(label="prepare", node=_prepare)
    stage = Step(label="stage", node=Graph())
    package = Step(label="package", node=_package)
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
            package.wire(after=(stage,)),
        ),
    )
    Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(
            initial_skip_requests={
                StepPath(
                    (
                        0,
                        1,
                    )
                )
            }
        ),
    ).run(
        options=ThreadRunOptions(workers=2),
        mode="compiled",
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_parent_waits_for_all_steps_in_child_with_disabled_step(
    mode: GraphMode,
) -> None:
    source_finished = threading.Event()

    def _source() -> None:
        time.sleep(0.1)
        source_finished.set()

    def _package() -> None:
        if not source_finished.is_set():
            raise AssertionError("parent completed before disabled-step child")

    source = Step(label="source", node=_source)
    disabled = Step(
        label="disabled",
        condition=lambda: False,
        node=lambda: None,
    )
    target = Step(label="target", node=lambda: None)
    child = Graph.from_wiring(
        wiring=(
            source.wire(),
            disabled.wire(after=(source,)),
            target.wire(after=(disabled,)),
        ),
    )
    child_step = Step(label="child", node=child)
    package = Step(label="package", node=_package)
    graph = Graph.from_wiring(
        wiring=(
            child_step.wire(),
            package.wire(after=(child_step,)),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=ThreadRunOptions(workers=4),
        mode=mode,
    )


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        ThreadRunOptions(workers=1),
        AsyncRunOptions(concurrency=1),
    ],
)
def test_child_graph_observation_is_preserved(
    mode: GraphMode,
    options: ThreadRunOptions | AsyncRunOptions,
) -> None:
    events: list[tuple[str, tuple[str, ...], str]] = []

    class _Observer:
        def emit(self, event: GraphObservationEvent) -> None:
            events.append((event.graph_label, event.graph_label_path, event.phase))

    child = Graph.from_wiring(
        wiring=(Step(label="run", node=lambda: None).wire(),),
        observation=GraphObservationMessages(
            before="before child",
            after_success="after child",
        ),
    )
    graph = Graph.from_wiring(
        wiring=(Step(label="child", node=child).wire(),),
        observation=GraphObservationMessages(
            before="before root",
            after_success="after root",
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=replace(options, observer=_Observer()),
        mode=mode,
    )

    assert events == [
        ("root", ("root",), "before"),
        ("child", ("root", "child"), "before"),
        ("child", ("root", "child"), "after_success"),
        ("root", ("root",), "after_success"),
    ]


def test_serial_executor_runs_compiled_composite() -> None:
    calls: list[str] = []
    graph = Graph.from_wiring(
        wiring=(
            Step(
                label="stage",
                node=Graph.from_wiring(
                    wiring=(
                        Step(label="run", node=lambda: calls.append("run")).wire(),
                    ),
                ),
            ).wire(),
        ),
    )
    state = SerialGraphExecutor().execute(
        Execution(root=Step(graph, label="root")).compile()
    )

    assert calls == ["run"]
    assert state.executed_steps == [StepPath((0, 0, 0))]
    assert state.completed_composites == {StepPath((0,))} | {
        StepPath(
            (
                0,
                0,
            )
        )
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_action_returned_graph_preserves_parent_scope(mode: GraphMode) -> None:
    prepare = Step(label="prepare", node=lambda: "prepared")

    def _make_child(_ctx: StepRunContext, result: Option[object]) -> Graph:
        return Graph.from_wiring(
            wiring=(
                Step(
                    label="consume",
                    node=lambda: result.unwrap(),
                ).wire(),
            ),
        )

    stage = Step(label="stage", node=_make_child)
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert state.results is not None
    assert state.results.require(StepPath((0, 1, 0))).value == "prepared"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_live_render_uses_runtime_path_for_action_returned_graph(
    mode: GraphMode,
) -> None:
    stage = Step(
        label="stage",
        node=lambda: Graph.from_wiring(
            wiring=(Step(label="consume", node=lambda: None).wire(),),
        ),
    )
    graph = Graph.from_wiring(wiring=(stage.wire(),))
    state = ExecutionState(results=ResultStore())

    Execution(root=Step(graph, label="root"), execution_state=state).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert render_live_execution(
        Step(graph, label="root"),
        state,
    ) == ("graph=root mode=dynamic running=idle last=root/stage/consume remaining=0")


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_async_runtime_condition_false_does_not_wait_for_source(
    mode: GraphMode,
) -> None:
    build_started = asyncio.Event()

    async def _configure() -> None:
        await asyncio.wait_for(build_started.wait(), timeout=1)

    async def _build(_ctx: StepRunContext, _result: Option[object]) -> None:
        build_started.set()

    probe = Step(label="probe", node=lambda: "hit")
    configure = Step(label="configure", node=_configure)
    build = Step(label="build", node=_build)
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        step=configure,
                        condition=(
                            _edge_condition_1343 := (
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

    Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(
        options=AsyncRunOptions(concurrency=3),
        mode=mode,
    )

    assert build_started.is_set()


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_async_executor_runs_independent_sync_steps_concurrently(
    mode: GraphMode,
) -> None:
    release_waiter = threading.Event()

    def _wait_for_peer() -> None:
        if not release_waiter.wait(timeout=1):
            raise RuntimeError("independent sync step did not run concurrently")

    def _release_peer() -> None:
        release_waiter.set()

    graph = Graph.from_wiring(
        wiring=(
            Step(label="wait", node=_wait_for_peer).wire(),
            Step(label="release", node=_release_peer).wire(),
        ),
    )

    Execution(root=Step(graph, label="root")).run(
        options=AsyncRunOptions(concurrency=2),
        mode=mode,
    )

    assert release_waiter.is_set()


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
@pytest.mark.parametrize(
    "options",
    [
        ThreadRunOptions(workers=1),
        AsyncRunOptions(concurrency=1),
    ],
)
def test_returned_graph_inherits_step_log_path(
    tmp_path: Path,
    mode: GraphMode,
    options: ThreadRunOptions | AsyncRunOptions,
) -> None:
    step_log_path = tmp_path / "component.log"
    observed_paths: list[Path | None] = []
    action_log = _RecordingActionLog()

    def _make_child() -> Graph:
        return Graph.from_wiring(
            wiring=(
                Step(
                    label="write",
                    node=lambda: observed_paths.append(action_log.active_path),
                ).wire(),
            ),
        )

    component = Step(label="component", node=_make_child)
    plan = Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(component.wire(),),
            ),
            label="root",
        ),
        action_log=action_log,
        step_log_paths={component: step_log_path},
    )
    plan.run(options=options, mode=mode)

    assert observed_paths == [step_log_path]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_static_component_graph_inherits_bound_log_path(
    tmp_path: Path,
    mode: GraphMode,
) -> None:
    step_log_path = tmp_path / "component.log"
    observed_paths: list[Path | None] = []
    action_log = _RecordingActionLog()
    component = Step(
        label="component",
        node=Graph.from_wiring(
            wiring=(
                Step(
                    label="build",
                    node=lambda: observed_paths.append(action_log.active_path),
                ).wire(),
            ),
        ),
    )
    plan = Execution(
        root=Step(
            Graph.from_wiring(
                wiring=(component.wire(),),
            ),
            label="root",
        ),
        action_log=action_log,
        step_log_paths={component: step_log_path},
    )

    plan.run(mode=mode)

    assert observed_paths == [step_log_path]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_run_freezes_static_conditions_before_execution(mode: GraphMode) -> None:
    prepare = Step(label="prepare", node=Leaf(action=lambda _ctx: "prepare"))
    android_shim = Step(
        label="android_shim",
        node=Leaf(action=lambda _ctx: "shim"),
    )
    archive = Step(
        label="archive",
        node=Leaf(
            action=lambda _ctx, prepared, shim: (
                prepared.unwrap(),
                shim.unwrap(),
            )
        ),
    )
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            android_shim.wire(),
            archive.wire(after=(Results(prepare, android_shim),)),
        ),
        static=StaticConditions(
            evaluator=_ConditionEvaluator(active=frozenset({"android"})),
            conditions={(android_shim, archive): _condition("android")},
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert state.results.require(
        StepPath(
            (
                0,
                2,
            )
        )
    ).value == ("prepare", "shim")


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_step_when_false_skips_step_and_allows_downstream_continue(
    mode: GraphMode,
) -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: False))
    optional = Step(
        label="optional",
        condition=(
            _optional_condition := (lambda result: bool(result.unwrap()), probe)
        ),
        node=Leaf(action=lambda _ctx, *_inputs: "optional"),
    )
    package = Step(
        label="package",
        node=Leaf(action=lambda _ctx: "packaged"),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            optional.wire(after=(Results(probe),)),
            package.wire(after=(optional,)),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.executed_steps == ([StepPath((0,))] if mode == "dynamic" else []) + [
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
    assert not state.results.has(
        StepPath(
            (
                0,
                1,
            )
        )
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


def test_dynamic_step_when_false_skips_graph_step() -> None:
    nested_calls: list[str] = []
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: False))
    configure = Step(
        label="configure",
        node=Leaf(action=lambda _ctx: nested_calls.append("configure")),
    )
    stage = Step(
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
        node=Graph.from_wiring(
            wiring=(configure.wire(),),
        ),
    )
    package = Step(label="package", node=Leaf(action=lambda _ctx: "packaged"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            stage.wire(
                after=(),
            ),
            package.wire(after=(stage,)),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert nested_calls == []
    assert state.executed_steps == [
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
                2,
            )
        ),
    ]
    assert state.results is not None
    assert not state.results.has(StepPath((0, 1, 0)))
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


def test_compiled_runtime_when_false_matches_dynamic_for_graph_step() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: False))
    configure = Step(label="configure", node=Leaf(action=lambda _ctx: "cfg"))
    stage = Step(
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
        node=Graph.from_wiring(wiring=(configure.wire(),)),
    )
    package = Step(label="package", node=Leaf(action=lambda _ctx: "packaged"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            stage.wire(
                after=(),
            ),
            package.wire(after=(stage,)),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode="compiled")

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
    assert not state.results.has(StepPath((0, 1, 0)))
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


def test_compiled_runtime_when_true_activates_composite() -> None:
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: "prepared"))
    configure = Step(
        label="configure",
        node=Leaf(action=lambda: "configured"),
    )
    stage = Step(
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
        node=Graph.from_wiring(wiring=(configure.wire(),)),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            stage.wire(
                after=(),
            ),
        ),
    )

    state = Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode="compiled")

    assert state.executed_steps == [
        StepPath(
            (
                0,
                0,
            )
        ),
        StepPath((0, 1, 0)),
    ]
    assert state.entered_composites == {StepPath((0,))} | {
        StepPath(
            (
                0,
                1,
            )
        )
    }
    assert state.completed_composites == {StepPath((0,))} | {
        StepPath(
            (
                0,
                1,
            )
        )
    }
    assert state.results is not None
    assert state.results.require(StepPath((0, 1, 0))).value == "configured"


def test_execution_run_logs_step_skip_by_condition(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="rivlet.runtime.compiled")
    probe = Step(label="probe", node=Leaf(action=lambda _ctx: True))
    build = Step(
        label="build",
        condition=(
            _build_condition := (lambda result: not bool(result.unwrap()), probe)
        ),
        node=Leaf(action=lambda _ctx, *_inputs: "built"),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            build.wire(after=(Results(probe),)),
        ),
    )

    Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert "Condition: skip root.build (inputs=(0, 0); value=false)" in caplog.text


def test_execution_run_writes_execution_render_to_run_log_dir(
    tmp_path: Path,
) -> None:
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

    Execution(
        root=Step(graph, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
        execution_output=TreeFileExecutionOutput(tmp_path),
    ).run(options=ThreadRunOptions(workers=1))

    text = (tmp_path / "graph-execution.root.dynamic.tree").read_text(encoding="utf-8")
    assert "execution: root (mode=dynamic)" in text
    assert "probe [executed] [result=true]" in text
    assert (
        "build [condition=false] [skipped] [after: probe (action input: 0), probe (condition input: 0)]"
        in text
    )
