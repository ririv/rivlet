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
    GraphStructure,
    Leaf,
    NodePath,
    NodeRunContext,
    Option,
    Results,
    ResultStore,
    SerialGraphExecutor,
    StaticCondition,
    StaticConditionEvaluator,
    StaticConditions,
    ThreadRunOptions,
    TreeFileExecutionOutput,
    render_execution,
    render_live_execution,
)
from rivlet._native import NativeGraphStructure, _native_process_graph
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
) -> NativeGraphStructure:
    """Build a native process graph through the private adapter test boundary."""

    return _native_process_graph(
        [(label, program, arguments, str(cwd), dict(os.environ))],
        [],
    )


def test_compiled_host_forwards_only_new_skip_requests() -> None:
    first = Leaf(lambda: None, label=("first"))
    second = Leaf(lambda: None, label=("second"))
    compilation = compile_graph(
        Graph(structure=GraphStructure(nodes=(first, second))).structure,
        root_label="root",
    )
    state = ExecutionState(initial_skip_requests={NodePath((0,))})
    host = CompiledExecutionHost(
        compilation,
        state,
        observer=None,
        run_leaf=lambda _step: None,
    )

    assert host.skipped_nodes() == [0]
    assert host.skipped_nodes() == []

    state.request_skip(NodePath((1,)))
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
        root=Graph(
            structure=Graph.from_wiring(
                wiring=(Leaf(lambda: calls.append("run")).wire(),)
            ).structure,
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
    prepare = Leaf(lambda: output.write_text("python\n"), label=("prepare"))
    native = Graph(
        structure=_process_graph(
            label="native-process",
            program=sys.executable,
            arguments=[
                "-c",
                "from pathlib import Path; import sys; p = Path(sys.argv[1]); p.write_text(p.read_text() + 'rust\\n')",
                str(output),
            ],
            cwd=tmp_path,
        ),
        label="native",
    )
    verify = Leaf(lambda: output.read_text().splitlines(), label=("verify"))
    plan = Execution(
        root=Graph(
            structure=(prepare >> native >> verify).graph().structure, label="mixed"
        ),
        execution_state=ExecutionState(results=ResultStore()),
    )

    state = plan.run(ThreadRunOptions(workers=2))

    assert plan.require_result(verify).value == ["python", "rust"]
    assert state.completed_steps == {NodePath((0,))} | frozenset(
        {
            NodePath(
                (
                    0,
                    0,
                )
            ),
            NodePath(
                (
                    0,
                    1,
                )
            ),
            NodePath((0, 1, 0)),
            NodePath(
                (
                    0,
                    2,
                )
            ),
        }
    )


def test_native_action_writes_to_bound_component_log(tmp_path: Path) -> None:
    log_path = tmp_path / "component.log"
    native = Graph(
        structure=_process_graph(
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
    component = Graph(structure=GraphStructure(nodes=(native,)), label="component")
    plan = Execution(
        root=Graph(structure=GraphStructure(nodes=(component,)), label="native-log"),
        node_log_paths={component: log_path},
    )

    plan.run()

    assert log_path.read_text().splitlines() == ["native stdout", "native stderr"]


def test_step_log_paths_resolve_at_construction(tmp_path: Path) -> None:
    first = Leaf(lambda: None, label=("first"))
    second = Leaf(lambda: None, label=("second"))
    first_log = tmp_path / "first.log"
    second_log = tmp_path / "second.log"

    plan = Execution(
        root=Graph(structure=GraphStructure(nodes=(first, second)), label="logs"),
        node_log_paths={
            first: first_log,
            second: second_log,
        },
    )

    assert (
        plan.execution_state.get_step_log_path(
            NodePath(
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
            NodePath(
                (
                    0,
                    1,
                )
            )
        )
        == second_log
    )


def test_native_action_failure_is_a_graph_action_failure(tmp_path: Path) -> None:
    native = Graph(
        structure=_process_graph(
            label="native-process",
            program=sys.executable,
            arguments=["-c", "raise SystemExit(7)"],
            cwd=tmp_path,
        ),
        label="native",
    )

    with pytest.raises(RuntimeError, match=r"exited with status .*7"):
        Execution(
            root=Graph(
                structure=GraphStructure(nodes=(native,)), label="native-failure"
            )
        ).run()


def test_async_graph_executor_runs_native_action_without_blocking_model_changes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "async-native.txt"
    native = Graph(
        structure=_process_graph(
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
        root=Graph(structure=GraphStructure(nodes=(native,)), label="async-native")
    ).run(AsyncRunOptions(concurrency=1))

    assert output.read_text() == "async"


def test_execution_run_executes_nested_graphs_in_topological_order() -> None:
    calls: list[str] = []
    configure = Leaf(lambda _ctx: calls.append("configure"), label=("configure"))
    build = Leaf(lambda _ctx: calls.append("build"), label=("build"))
    nested = Graph.from_wiring(
        wiring=(
            configure.wire(),
            build.wire(after=(configure,)),
        ),
    )
    prepare = Leaf(lambda _ctx: calls.append("prepare"), label=("prepare"))
    stage = Graph(structure=nested.structure, label="stage")
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
        ),
    )

    state = Execution(
        root=Graph(structure=root.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert calls == ["prepare", "configure", "build"]
    assert state.executed_steps == [
        NodePath((0,)),
        NodePath(
            (
                0,
                0,
            )
        ),
        NodePath(
            (
                0,
                1,
            )
        ),
        NodePath((0, 1, 0)),
        NodePath((0, 1, 1)),
    ]


def test_execution_state_cannot_be_reused() -> None:
    plan = Execution(root=Graph(structure=GraphStructure(), label="root"))

    plan.run()

    with pytest.raises(RuntimeError, match="graph execution state cannot be reused"):
        plan.run()


def test_step_action_preserves_default_argument_capture() -> None:
    captured: list[str] = []

    def _capture(value: str = "captured") -> None:
        captured.append(value)

    Execution(
        root=Graph(
            structure=Graph.from_wiring(
                wiring=(Leaf(_capture, label="capture").wire(),)
            ).structure,
            label="root",
        ),
    ).run()

    assert captured == ["captured"]


def test_execution_run_skips_marked_steps() -> None:
    calls: list[str] = []
    prepare = Leaf(lambda _ctx: calls.append("prepare"), label=("prepare"))
    build = Leaf(lambda _ctx: calls.append("build"), label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            build.wire(after=(prepare,)),
        ),
    )

    state = ExecutionState(
        initial_skip_requests={
            NodePath(
                (
                    0,
                    1,
                )
            )
        },
        results=ResultStore(),
    )
    Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=state,
    ).run(options=ThreadRunOptions(workers=1))

    assert calls == ["prepare"]
    assert state.executed_steps == [
        NodePath((0,)),
        NodePath(
            (
                0,
                0,
            )
        ),
    ]
    assert state.skipped_steps == {
        NodePath(
            (
                0,
                1,
            )
        )
    }


def test_execution_rejects_unknown_initial_skip_request() -> None:
    graph = Graph(structure=GraphStructure(nodes=(Leaf(lambda: None, label="build"),)))

    with pytest.raises(RuntimeError, match="unknown graph skip requests: 0.1"):
        Execution(
            root=Graph(structure=graph.structure),
            execution_state=ExecutionState(
                initial_skip_requests={
                    NodePath(
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
    node_path = NodePath((0,))
    state.record_step(node_path, ("root", "build"))

    with pytest.raises(RuntimeError, match="cannot skip an already started step: 0"):
        state.request_skip(node_path)


def test_execution_run_uses_provided_state() -> None:
    calls: list[str] = []
    graph = Graph.from_wiring(
        wiring=(Leaf(lambda _ctx: calls.append("build"), label=("build")).wire(),),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert calls == ["build"]
    assert state.executed_steps == [
        NodePath((0,)),
        NodePath(
            (
                0,
                0,
            )
        ),
    ]


def test_execution_run_records_sync_step_results() -> None:
    prepare = Leaf(lambda _ctx: "prepared", label=("prepare"))
    build = Leaf(lambda _ctx, result: f"{result.unwrap()}:built", label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            build.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert state.results is not None
    assert (
        state.results.get(
            NodePath(
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
            NodePath(
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
            NodePath(
                (
                    0,
                    1,
                )
            )
        ).value
        == "prepared:built"
    )


def test_execution_run_records_async_step_results() -> None:
    async def _build(_run_ctx: NodeRunContext) -> str:
        return "built"

    graph = Graph.from_wiring(
        wiring=(Leaf(_build, label=("build")).wire(),),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert state.results is not None
    assert (
        state.results.require(
            NodePath(
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
        wiring=(Leaf(lambda _ctx: None, label=("build")).wire(),),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert state.results is not None
    assert state.results.has(
        NodePath(
            (
                0,
                0,
            )
        )
    )
    assert (
        state.results.require(
            NodePath(
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
        wiring=(Leaf(lambda _ctx: "built", label=("build")).wire(),),
    )
    state = ExecutionState(
        initial_skip_requests={
            NodePath(
                (
                    0,
                    0,
                )
            )
        },
        results=ResultStore(),
    )

    Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=state,
    ).run()

    assert state.results is not None
    assert not state.results.has(
        NodePath(
            (
                0,
                0,
            )
        )
    )


def test_execution_run_does_not_record_results_for_failed_steps() -> None:
    def _fail(_run_ctx: NodeRunContext) -> object:
        raise RuntimeError("boom")

    graph = Graph.from_wiring(
        wiring=(Leaf(_fail, label=("build")).wire(),),
    )

    state = ExecutionState(results=ResultStore())
    try:
        Execution(
            root=Graph(structure=graph.structure, label="root"),
            execution_state=state,
        ).run()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert state.results is not None
    assert not state.results.has(
        NodePath(
            (
                0,
                0,
            )
        )
    )


def test_parallel_graph_failure_does_not_request_global_stop() -> None:
    execution_control = ExecutionControl()
    survivor_completed = threading.Event()

    def _fail(_run_ctx: NodeRunContext) -> object:
        raise RuntimeError("boom")

    def _survive(_run_ctx: NodeRunContext) -> object:
        assert not execution_control.stop_requested()
        survivor_completed.set()
        return None

    graph = Graph.from_wiring(
        wiring=(
            Leaf(_fail, label=("fail")).wire(),
            Leaf(_survive, label=("wait")).wire(),
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        Execution(
            root=Graph(structure=graph.structure, label="root"),
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
            Leaf(_fail, label=("fail")).wire(),
            Leaf(_survive, label=("survive")).wire(),
        ),
    )

    plan = Execution(root=Graph(structure=graph.structure, label="root"))
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

    first = Leaf(_request_stop, label=("first"))
    second = Leaf(lambda: calls.append("second"), label=("second"))
    graph = Graph.from_wiring(
        wiring=(
            first.wire(),
            second.wire(after=(first,)),
        ),
    )

    with pytest.raises(ExecutionStopped):
        Execution(
            root=Graph(structure=graph.structure),
            execution_control=execution_control,
        ).run(options=options)

    assert calls == ["first"]


def test_step_failure_does_not_cancel_shared_execution_control() -> None:
    calls: list[str] = []
    execution_control = ExecutionControl()

    def _fail() -> None:
        raise RuntimeError("boom")

    first_plan = Execution(
        root=Graph(
            structure=Graph.from_wiring(wiring=(Leaf(_fail).wire(),)).structure,
            label="build",
        ),
        execution_control=execution_control,
    )
    second_plan = Execution(
        root=Graph(
            structure=Graph.from_wiring(
                wiring=(Leaf(lambda: calls.append("package")).wire(),)
            ).structure,
            label="package",
        ),
        execution_control=execution_control,
    )

    with pytest.raises(RuntimeError, match="boom"):
        first_plan.run()
    second_plan.run()

    assert calls == ["package"]


def test_execution_reads_nested_result_by_step_ref() -> None:
    def _nested(_run_ctx: NodeRunContext) -> str:
        return "nested-value"

    nested_step = Leaf(_nested, label=("nested"))
    nested = Graph.from_wiring(
        wiring=(nested_step.wire(),),
    )
    stage = Graph(structure=nested.structure, label="stage")
    root = Graph.from_wiring(
        wiring=(stage.wire(),),
    )

    plan = Execution(
        root=Graph(structure=root.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    )
    plan.run()

    assert plan.require_result(nested_step).value == "nested-value"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_reused_subgraph_mounts_have_distinct_step_instances(
    mode: GraphMode,
) -> None:
    shared_step = Leaf(lambda run_ctx: str(run_ctx.node_path), label=("shared"))
    shared_graph = Graph.from_wiring(
        wiring=(shared_step.wire(),),
    )
    first = Graph(structure=shared_graph.structure, label="first")
    second = Graph(structure=shared_graph.structure, label="second")
    root = Graph.from_wiring(
        wiring=(first.wire(), second.wire()),
    )
    plan = Execution(
        root=Graph(structure=root.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    )

    state = plan.run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert state.results is not None
    assert state.results.require(NodePath((0, 0, 0))).value == "0.0.0"
    assert state.results.require(NodePath((0, 1, 0))).value == "0.1.0"
    with pytest.raises(RuntimeError, match="mounted more than once"):
        plan.require_result(shared_step)


def test_dynamic_subgraph_captures_explicit_result_input() -> None:
    def _prepare(_run_ctx: NodeRunContext) -> str:
        return "prepared"

    prepare = Leaf(_prepare, label=("prepare"))

    def expand(_ctx: NodeRunContext, result: Option[object]) -> GraphStructure:
        nested_step = Leaf(lambda: f"nested:{result.unwrap()}", label=("nested"))
        return Graph.from_wiring(
            (nested_step.wire(),),
        ).structure

    stage = Leaf(expand, label=("stage"))
    root = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Graph(structure=root.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run()

    assert state.results is not None
    assert state.results.require(NodePath((0, 1, 0))).value == "nested:prepared"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_condition_true_waits_for_source_step(mode: GraphMode) -> None:
    probe = Leaf(lambda _ctx: "miss", label=("probe"))
    setup = Leaf(lambda _ctx: "setup", label=("setup"))
    configure = Leaf(lambda _ctx: "configured", label=("configure"))
    build = Leaf(lambda _ctx, result: f"{result.unwrap()}:built", label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            setup.wire(),
            configure.wire(after=(setup,)),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        node=configure,
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert (
        state.results.require(
            NodePath(
                (
                    0,
                    3,
                )
            )
        ).value
        == "miss:built"
    )
    assert state.executed_steps.index(
        NodePath(
            (
                0,
                2,
            )
        )
    ) < state.executed_steps.index(
        NodePath(
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

    probe = Leaf(lambda: "ready", label=("probe"))
    first = Leaf(lambda: None, label=("first"))
    second = Leaf(lambda: None, label=("second"))
    target = Leaf(
        lambda _ctx, *_inputs: None,
        label=("target"),
        condition=(_target_condition := (_condition, probe)),
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

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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

    probe = Leaf(lambda: None, label=("probe"))
    first = Leaf(lambda: None, label=("first"))
    second = Leaf(lambda: None, label=("second"))
    source = Leaf(lambda: None, label=("source"))
    target = Leaf(lambda _ctx, *_inputs: None, label=("target"))
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
                        node=source,
                        condition=(_edge_condition_848 := (_condition, probe)),
                    ),
                    Results(probe),
                )
            ),
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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
        wiring=(Leaf(lambda: None, label=("target"), condition=(_condition)).wire(),),
    )
    plan = Execution(root=Graph(structure=graph.structure, label="root"))

    state = plan.run()
    rendered = render_execution(
        Graph(structure=graph.structure, label="root"),
        state,
    )

    assert evaluations == 1
    assert "target [condition=true] [executed]" in rendered


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_runtime_condition_false_does_not_require_source_result(
    mode: GraphMode,
) -> None:
    probe = Leaf(lambda _ctx: "hit", label=("probe"))
    setup = Leaf(lambda _ctx: "setup", label=("setup"))
    configure = Leaf(lambda _ctx: "configured", label=("configure"))
    build = Leaf(lambda _ctx, result: result.unwrap(), label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            setup.wire(),
            configure.wire(after=(setup,)),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        node=configure,
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert (
        state.results.require(
            NodePath(
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

    def _build(_ctx: NodeRunContext, _result: Option[object]) -> str:
        build_started.set()
        return "built"

    probe = Leaf(lambda: "hit", label=("probe"))
    configure = Leaf(_configure, label=("configure"))
    build = Leaf(_build, label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        node=configure,
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
        root=Graph(structure=graph.structure, label="root"),
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

    source = Leaf(_source, label=("source"))
    target = Leaf(lambda: None, label=("target"))
    child = Graph.from_wiring(
        wiring=(
            source.wire(),
            target.wire(
                after=(
                    ConditionalPrerequisite(
                        node=source,
                        condition=lambda: False,
                    ),
                )
            ),
        ),
    )
    child_step = Graph(structure=child.structure, label="child")
    package = Leaf(_package, label=("package"))
    graph = Graph.from_wiring(
        wiring=(
            child_step.wire(),
            package.wire(after=(child_step,)),
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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

    source = Leaf(lambda: None, label=("source"))
    target = Leaf(_target, label=("target"))
    child = Graph.from_wiring(
        wiring=(
            source.wire(),
            target.wire(
                after=(
                    ConditionalPrerequisite(
                        node=source,
                        condition=lambda: False,
                    ),
                )
            ),
        ),
    )
    prepare = Leaf(_prepare, label=("prepare"))
    child_step = Graph(structure=child.structure, label="child")
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            child_step.wire(after=(prepare,)),
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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
        prepare = Leaf(_prepare, label=("prepare"))
        empty_stage = Graph(structure=GraphStructure(), label="empty_stage")
        package = Leaf(_package, label=("package"))
        Execution(
            root=Graph(
                structure=Graph.from_wiring(
                    wiring=(
                        prepare.wire(),
                        empty_stage.wire(after=(prepare,)),
                        package.wire(after=(empty_stage,)),
                    )
                ).structure,
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

    prepare = Leaf(_prepare, label=("prepare"))
    stage = Graph(structure=GraphStructure(), label="stage")
    package = Leaf(_package, label=("package"))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(prepare,)),
            package.wire(after=(stage,)),
        ),
    )
    Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(
            initial_skip_requests={
                NodePath(
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

    source = Leaf(_source, label=("source"))
    disabled = Leaf(lambda: None, label=("disabled"), condition=(lambda: False))
    target = Leaf(lambda: None, label=("target"))
    child = Graph.from_wiring(
        wiring=(
            source.wire(),
            disabled.wire(after=(source,)),
            target.wire(after=(disabled,)),
        ),
    )
    child_step = Graph(structure=child.structure, label="child")
    package = Leaf(_package, label=("package"))
    graph = Graph.from_wiring(
        wiring=(
            child_step.wire(),
            package.wire(after=(child_step,)),
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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
        wiring=(Leaf(lambda: None, label=("run")).wire(),),
        observation=GraphObservationMessages(
            before="before child",
            after_success="after child",
        ),
    )
    graph = Graph.from_wiring(
        wiring=(Graph(structure=child.structure, label="child").wire(),),
        observation=GraphObservationMessages(
            before="before root",
            after_success="after root",
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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
            Graph(
                structure=Graph.from_wiring(
                    wiring=(Leaf(lambda: calls.append("run"), label="run").wire(),)
                ).structure,
                label="stage",
            ).wire(),
        ),
    )
    state = SerialGraphExecutor().execute(
        Execution(root=Graph(structure=graph.structure, label="root")).compile()
    )

    assert calls == ["run"]
    assert state.executed_steps == [NodePath((0, 0, 0))]
    assert state.completed_composites == {NodePath((0,))} | {
        NodePath(
            (
                0,
                0,
            )
        )
    }


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_action_returned_graph_preserves_parent_scope(mode: GraphMode) -> None:
    prepare = Leaf(lambda: "prepared", label=("prepare"))

    def _make_child(_ctx: NodeRunContext, result: Option[object]) -> GraphStructure:
        return Graph.from_wiring(
            wiring=(Leaf(lambda: result.unwrap(), label=("consume")).wire(),),
        ).structure

    stage = Leaf(_make_child, label=("stage"))
    graph = Graph.from_wiring(
        wiring=(
            prepare.wire(),
            stage.wire(after=(Results(prepare),)),
        ),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert state.results is not None
    assert state.results.require(NodePath((0, 1, 0))).value == "prepared"


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_live_render_uses_runtime_path_for_action_returned_graph(
    mode: GraphMode,
) -> None:
    stage = Leaf(
        lambda: Graph.from_wiring(
            wiring=(Leaf(lambda: None, label=("consume")).wire(),),
        ).structure,
        label=("stage"),
    )
    graph = Graph.from_wiring(wiring=(stage.wire(),))
    state = ExecutionState(results=ResultStore())

    Execution(
        root=Graph(structure=graph.structure, label="root"), execution_state=state
    ).run(
        options=ThreadRunOptions(workers=1),
        mode=mode,
    )

    assert render_live_execution(
        Graph(structure=graph.structure, label="root"),
        state,
    ) == ("graph=root mode=dynamic running=idle last=root/stage/consume remaining=0")


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_async_runtime_condition_false_does_not_wait_for_source(
    mode: GraphMode,
) -> None:
    build_started = asyncio.Event()

    async def _configure() -> None:
        await asyncio.wait_for(build_started.wait(), timeout=1)

    async def _build(_ctx: NodeRunContext, _result: Option[object]) -> None:
        build_started.set()

    probe = Leaf(lambda: "hit", label=("probe"))
    configure = Leaf(_configure, label=("configure"))
    build = Leaf(_build, label=("build"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            configure.wire(),
            build.wire(
                after=(
                    ConditionalPrerequisite(
                        node=configure,
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
        root=Graph(structure=graph.structure, label="root"),
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
            Leaf(_wait_for_peer, label=("wait")).wire(),
            Leaf(_release_peer, label=("release")).wire(),
        ),
    )

    Execution(root=Graph(structure=graph.structure, label="root")).run(
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

    def _make_child() -> GraphStructure:
        return Graph.from_wiring(
            wiring=(
                Leaf(
                    lambda: observed_paths.append(action_log.active_path),
                    label=("write"),
                ).wire(),
            ),
        ).structure

    component = Leaf(_make_child, label=("component"))
    plan = Execution(
        root=Graph(
            structure=Graph.from_wiring(wiring=(component.wire(),)).structure,
            label="root",
        ),
        action_log=action_log,
        node_log_paths={component: step_log_path},
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
    component = Graph(
        structure=Graph.from_wiring(
            wiring=(
                Leaf(
                    lambda: observed_paths.append(action_log.active_path), label="build"
                ).wire(),
            )
        ).structure,
        label="component",
    )
    plan = Execution(
        root=Graph(
            structure=Graph.from_wiring(wiring=(component.wire(),)).structure,
            label="root",
        ),
        action_log=action_log,
        node_log_paths={component: step_log_path},
    )

    plan.run(mode=mode)

    assert observed_paths == [step_log_path]


@pytest.mark.parametrize("mode", ["dynamic", "compiled"])
def test_run_freezes_static_conditions_before_execution(mode: GraphMode) -> None:
    prepare = Leaf(lambda _ctx: "prepare", label=("prepare"))
    android_shim = Leaf(lambda _ctx: "shim", label=("android_shim"))
    archive = Leaf(
        lambda _ctx, prepared, shim: (
            prepared.unwrap(),
            shim.unwrap(),
        ),
        label=("archive"),
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.results is not None
    assert state.results.require(
        NodePath(
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
    probe = Leaf(lambda _ctx: False, label=("probe"))
    optional = Leaf(
        lambda _ctx, *_inputs: "optional",
        label=("optional"),
        condition=(
            _optional_condition := (lambda result: bool(result.unwrap()), probe)
        ),
    )
    package = Leaf(lambda _ctx: "packaged", label=("package"))
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            optional.wire(after=(Results(probe),)),
            package.wire(after=(optional,)),
        ),
    )

    state = Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode=mode)

    assert state.executed_steps == ([NodePath((0,))] if mode == "dynamic" else []) + [
        NodePath(
            (
                0,
                0,
            )
        ),
        NodePath(
            (
                0,
                2,
            )
        ),
    ]
    assert state.results is not None
    assert not state.results.has(
        NodePath(
            (
                0,
                1,
            )
        )
    )
    assert (
        state.results.require(
            NodePath(
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
    probe = Leaf(lambda _ctx: False, label=("probe"))
    configure = Leaf(lambda _ctx: nested_calls.append("configure"), label=("configure"))
    stage = Graph(
        structure=Graph.from_wiring(wiring=(configure.wire(),)).structure,
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
    )
    package = Leaf(lambda _ctx: "packaged", label=("package"))
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert nested_calls == []
    assert state.executed_steps == [
        NodePath((0,)),
        NodePath(
            (
                0,
                0,
            )
        ),
        NodePath(
            (
                0,
                2,
            )
        ),
    ]
    assert state.results is not None
    assert not state.results.has(NodePath((0, 1, 0)))
    assert (
        state.results.require(
            NodePath(
                (
                    0,
                    2,
                )
            )
        ).value
        == "packaged"
    )


def test_compiled_runtime_when_false_matches_dynamic_for_graph_step() -> None:
    probe = Leaf(lambda _ctx: False, label=("probe"))
    configure = Leaf(lambda _ctx: "cfg", label=("configure"))
    stage = Graph(
        structure=Graph.from_wiring(wiring=(configure.wire(),)).structure,
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
    )
    package = Leaf(lambda _ctx: "packaged", label=("package"))
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode="compiled")

    assert state.executed_steps == [
        NodePath(
            (
                0,
                0,
            )
        ),
        NodePath(
            (
                0,
                2,
            )
        ),
    ]
    assert state.results is not None
    assert not state.results.has(NodePath((0, 1, 0)))
    assert (
        state.results.require(
            NodePath(
                (
                    0,
                    2,
                )
            )
        ).value
        == "packaged"
    )


def test_compiled_runtime_when_true_activates_composite() -> None:
    probe = Leaf(lambda _ctx: "prepared", label=("probe"))
    configure = Leaf(lambda: "configured", label=("configure"))
    stage = Graph(
        structure=Graph.from_wiring(wiring=(configure.wire(),)).structure,
        label="stage",
        condition=(_stage_condition := (lambda result: bool(result.unwrap()), probe)),
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
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1), mode="compiled")

    assert state.executed_steps == [
        NodePath(
            (
                0,
                0,
            )
        ),
        NodePath((0, 1, 0)),
    ]
    assert state.entered_composites == {NodePath((0,))} | {
        NodePath(
            (
                0,
                1,
            )
        )
    }
    assert state.completed_composites == {NodePath((0,))} | {
        NodePath(
            (
                0,
                1,
            )
        )
    }
    assert state.results is not None
    assert state.results.require(NodePath((0, 1, 0))).value == "configured"


def test_execution_run_logs_step_skip_by_condition(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="rivlet.runtime.compiled")
    probe = Leaf(lambda _ctx: True, label=("probe"))
    build = Leaf(
        lambda _ctx, *_inputs: "built",
        label=("build"),
        condition=(
            _build_condition := (lambda result: not bool(result.unwrap()), probe)
        ),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            build.wire(after=(Results(probe),)),
        ),
    )

    Execution(
        root=Graph(structure=graph.structure, label="root"),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(options=ThreadRunOptions(workers=1))

    assert "Condition: skip root.build (inputs=(0, 0); value=false)" in caplog.text


def test_execution_run_writes_execution_render_to_run_log_dir(
    tmp_path: Path,
) -> None:
    probe = Leaf(lambda _ctx: True, label=("probe"))
    build = Leaf(
        lambda _ctx, *_inputs: None,
        label=("build"),
        condition=(
            _build_condition := (lambda result: not bool(result.unwrap()), probe)
        ),
    )
    graph = Graph.from_wiring(
        wiring=(
            probe.wire(),
            build.wire(after=(Results(probe),)),
        ),
    )

    Execution(
        root=Graph(structure=graph.structure, label="root"),
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
