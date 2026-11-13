"""Run real Rust declarations through mixed execution and inspect their scopes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

import pytest

from rivlet import (
    AsyncRunOptions,
    Case,
    Edge,
    Execution,
    ExecutionState,
    Graph,
    GraphError,
    GraphInterface,
    GraphNodeFailure,
    GraphStructure,
    NodeExecutionEvent,
    NodeExecutionState,
    NodePath,
    Option,
    ResultStore,
    SerialGraphExecutor,
    Step,
    ThreadRunOptions,
    export_graph,
    render_graph,
    render_graph_mermaid,
    static,
)
from rivlet._native import (
    RustGraphStructure,
    _compose_rust_graph,
    _rust_process_graph,
)
from rivlet.compile import CompiledRustStep

type Backend = Literal["serial", "thread", "async"]


def execute(execution: Execution, backend: Backend) -> None:
    if backend == "serial":
        SerialGraphExecutor(execution.execution_state).execute(execution.compile())
    else:
        execution.run(
            ThreadRunOptions(workers=2)
            if backend == "thread"
            else AsyncRunOptions(concurrency=2)
        )


def processes(
    directory: Path,
    scripts: list[tuple[str, str]],
    *,
    arcs: list[tuple[int, int]],
    inputs: list[tuple[str, list[int]]],
    outputs: list[tuple[str, int]],
) -> RustGraphStructure:
    return _rust_process_graph(
        [
            (name, sys.executable, ["-c", script], str(directory), dict(os.environ))
            for name, script in scripts
        ],
        arcs,
        inputs,
        outputs,
    )


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_nested_shared_rust_graph_has_ports_paths_logs_and_no_python_actions(
    tmp_path: Path,
    backend: Backend,
) -> None:
    script = (
        "from pathlib import Path; assert Path('gate').exists(); "
        "p=Path('order'); p.write_text(p.read_text()+'task\\n' if p.exists() else 'task\\n'); "
        "print('native task', flush=True)"
    )
    body = processes(
        tmp_path,
        [("task", script)],
        arcs=[],
        inputs=[("start", [0])],
        outputs=[("done", 0)],
    )
    composed = _compose_rust_graph(
        [("left", body), ("right", body)],
        [(0, 1, "done", "start")],
        [("start", [(0, "start")])],
        [("done", 1, "done")],
    )
    inner = Graph(composed, label="rust")
    forwarded = Graph(
        GraphStructure(
            nodes=(inner,),
            interface=GraphInterface(inputs=inner, outputs=inner),
        ),
        label="forwarded",
    )
    prepare = Step(lambda: (tmp_path / "gate").touch(), label="prepare")
    provider = Graph(
        GraphStructure(
            nodes=(prepare,), interface=GraphInterface(outputs={"go": prepare})
        )
    )
    observed: list[str] = []
    verify = Step(
        lambda: observed.extend((tmp_path / "order").read_text().splitlines()),
        label="verify",
    )
    consumer = Graph(
        GraphStructure(
            nodes=(verify,), interface=GraphInterface(inputs={"apply": verify})
        )
    )
    root = Graph(
        GraphStructure(
            nodes=(provider, forwarded, consumer),
            edges=(
                Edge(provider, forwarded, ports=("go", "start")),
                Edge(forwarded, consumer, ports=("done", "apply")),
            ),
        ),
        label="mixed",
    )
    events: list[NodeExecutionEvent] = []

    class Events:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    log = tmp_path / "native.log"
    execution = Execution(root, node_log_paths={forwarded: log}, events=Events())
    execute(execution, backend)
    assert observed == ["task", "task"]
    assert log.read_text().splitlines() == ["native task", "native task"]
    native = [
        node for node in execution.compile().steps if isinstance(node, CompiledRustStep)
    ]
    assert len(native) == 2
    assert native[0].id == native[1].id
    assert native[0].path != native[1].path
    assert native[0].path.labels[-3:] == ("rust", "left", "task")
    assert native[1].path.labels[-3:] == ("rust", "right", "task")
    # 公共执行事件在串行外部执行器中不是必需项；混合驱动必须发布真实挂载事件。
    if backend != "serial":
        finished = [
            event
            for event in events
            if event.node_id == native[0].id
            and event.state is NodeExecutionState.COMPLETED
        ]
        assert len(finished) == 2
        assert finished[0].node_path != finished[1].node_path
    for subject in (root, execution.compile()):
        document = export_graph(subject)
        assert (
            sum(
                node["kind"] == "leaf" and node["label"] == "task"
                for node in document["nodes"]
            )
            == 2
        )
        rust_ports = [
            edge
            for edge in document["edges"]
            if edge["kind"] == "port" and edge["source_port"] == "done"
        ]
        assert len(rust_ports) == 2
        encoded = json.dumps(document)
        assert sys.executable not in encoded
        assert str(tmp_path) not in encoded
    assert "left [graph]" in render_graph(root, root_label="mixed")
    assert "task [native]" in render_graph(root, root_label="mixed")
    assert "done -> start" in render_graph_mermaid(root, root_label="mixed")


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_output_can_feed_python_result_without_inventing_native_values(
    tmp_path: Path,
    backend: Backend,
) -> None:
    body = processes(
        tmp_path, [("done", "pass")], arcs=[], inputs=[], outputs=[("done", 0)]
    )
    inner = _compose_rust_graph([("child", body)], [], [], [("result", 0, "done")])
    producer = Graph(inner)
    seen: list[object] = []

    def consume(_ctx, value: Option[object]) -> None:
        seen.append(value.unwrap())

    target = Step(consume)
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(producer, target),
                edges=Edge.results((producer.interface.outputs["result"],), target),
            )
        ),
        execution_state=ExecutionState(results=ResultStore()),
    )
    execute(execution, backend)
    assert seen == [None]
    with pytest.raises(GraphError, match="native action expects 0 result inputs"):
        receiver = Graph(
            processes(
                tmp_path,
                [("receiver", "pass")],
                arcs=[],
                inputs=[("apply", [0])],
                outputs=[],
            )
        )
        GraphStructure(
            nodes=(target, receiver),
            edges=Edge.results((target,), receiver.interface.inputs["apply"]),
        )


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_failure_is_scoped_and_blocks_only_port_dependents(
    tmp_path: Path,
    backend: Backend,
) -> None:
    failed_body = processes(
        tmp_path,
        [("fail", "raise SystemExit(7)")],
        arcs=[],
        inputs=[],
        outputs=[("ready", 0)],
    )
    target_body = processes(
        tmp_path,
        [("blocked", "raise AssertionError('must not run')")],
        arcs=[],
        inputs=[("apply", [0])],
        outputs=[],
    )
    body = _compose_rust_graph(
        [("provider", failed_body), ("consumer", target_body)],
        [(0, 1, "ready", "apply")],
        [],
        [],
    )
    native = Graph(body, label="native")
    seen: list[str] = []
    independent = Step(lambda: seen.append("independent"))
    execution = Execution(Graph(GraphStructure(nodes=(native, independent))))
    with pytest.raises(RuntimeError, match=r"exited with status .*7"):
        execute(execution, backend)
    assert seen == ["independent"]
    assert (
        execution.execution_state.node_failures[NodePath((0, 0, 1, 0))]
        is GraphNodeFailure.BLOCKED
    )


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_outer_python_condition_skips_whole_native_hierarchy(
    tmp_path: Path,
    backend: Backend,
) -> None:
    body = processes(
        tmp_path,
        [("never", "raise AssertionError('must not run')")],
        arcs=[],
        inputs=[],
        outputs=[],
    )
    nested = _compose_rust_graph([("inner", body)], [], [], [])
    root = Graph(nested, condition=lambda: False)
    execution = Execution(root)
    execute(execution, backend)
    assert not execution.execution_state.node_failures
    selected = static.select(
        cases=(Case(False, root), Case(True, Step(lambda: None)))
    ).graph()
    compilation = Execution(selected).compile()
    assert not any(isinstance(node, CompiledRustStep) for node in compilation.steps)


def test_native_ports_reject_real_expanded_cycle_and_retain_scope_local_view(
    tmp_path: Path,
) -> None:
    body = processes(
        tmp_path,
        [("task", "pass")],
        arcs=[],
        inputs=[("in", [0])],
        outputs=[("out", 0)],
    )
    cyclic = _compose_rust_graph(
        [("a", body), ("b", body)], [(0, 1, "out", "in"), (1, 0, "out", "in")], [], []
    )
    with pytest.raises(GraphError, match="cycle"):
        Execution(Graph(cyclic)).compile()
    nested = _compose_rust_graph([("inner", body)], [], [], [])
    _, _, child_view = nested.nodes()[0]
    assert child_view is not None
    assert child_view.output_path("out") == [0]
    execution = Execution(Graph(child_view))
    execution.run()
    assert len(execution.compile().steps) == 1


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_two_python_mounts_share_nested_rust_body_without_sharing_scopes(
    tmp_path: Path,
    backend: Backend,
) -> None:
    body = processes(
        tmp_path, [("task", "pass")], arcs=[], inputs=[], outputs=[("done", 0)]
    )
    shared = _compose_rust_graph([("child", body)], [], [], [("done", 0, "done")])
    left, right = Graph(shared, label="left"), Graph(shared, label="right")
    seen: list[tuple[object, object]] = []
    consume = Step(lambda ctx, a, b: seen.append((a.unwrap(), b.unwrap())))
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(left, right, consume),
                edges=Edge.results(
                    (right.interface.outputs["done"], left.interface.outputs["done"]),
                    consume,
                ),
            )
        ),
        execution_state=ExecutionState(results=ResultStore()),
    )
    execute(execution, backend)
    native = [
        node for node in execution.compile().steps if isinstance(node, CompiledRustStep)
    ]
    assert len(native) == 2
    assert native[0].id == native[1].id
    assert {node.path.labels[-3:] for node in native} == {
        ("left", "child", "task"),
        ("right", "child", "task"),
    }
    assert seen == [(None, None)]


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_native_partial_output_and_later_failure_allow_interleaved_python_work(
    tmp_path: Path,
    backend: Backend,
) -> None:
    body = processes(
        tmp_path,
        [("ready", "pass"), ("later-fail", "raise SystemExit(9)")],
        arcs=[],
        inputs=[("late", [1])],
        outputs=[("ready", 0)],
    )
    provider = Graph(body, label="provider")
    seen: list[str] = []
    work = Step(lambda: seen.append("ran-before-late-failure"))
    consumer = Graph(
        GraphStructure(
            nodes=(work,),
            interface=GraphInterface(
                inputs={"apply": work},
                outputs={"done": work},
            ),
        )
    )
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(provider, consumer),
                edges=(
                    Edge(provider, consumer, ports=("ready", "apply")),
                    Edge(consumer, provider, ports=("done", "late")),
                ),
            )
        )
    )
    with pytest.raises(RuntimeError, match=r"exited with status .*9"):
        execute(execution, backend)
    assert seen == ["ran-before-late-failure"]
    assert (
        GraphNodeFailure.BLOCKED not in execution.execution_state.node_failures.values()
    )


@pytest.mark.parametrize("backend", ("serial", "thread", "async"))
def test_python_conditional_port_can_control_native_consumer(
    tmp_path: Path,
    backend: Backend,
) -> None:
    provider = Step(lambda: None)
    source = Graph(
        GraphStructure(
            nodes=(provider,), interface=GraphInterface(outputs={"ready": provider})
        )
    )
    native = Graph(
        processes(
            tmp_path,
            [("run", "from pathlib import Path; Path('ran').touch()")],
            arcs=[],
            inputs=[("apply", [0])],
            outputs=[],
        )
    )
    execution = Execution(
        Graph(
            GraphStructure(
                nodes=(source, native),
                edges=(
                    Edge(
                        source,
                        native,
                        ports=("ready", "apply"),
                        condition=lambda: False,
                    ),
                ),
            )
        )
    )
    execute(execution, backend)
    assert (tmp_path / "ran").is_file()
