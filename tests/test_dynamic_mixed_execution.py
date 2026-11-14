from __future__ import annotations

import os
import socketserver
import subprocess
import sys
import threading
from functools import partial

import pytest
from execution_support import run_async_execution

from rivlet import (
    ExactProviderRequirement,
    Execution,
    ExecutionState,
    Graph,
    GraphInterface,
    GraphStructure,
    NodeExecutionEvent,
    NodeExecutionState,
    NodeRunContext,
    Option,
    Results,
    ResultStore,
    Step,
    resolve_port_requirements,
)
from rivlet import run as run_execution
from rivlet._native import _rust_process_graph


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_and_python_actions_use_independent_execution_resources(
    tmp_path, asynchronous
) -> None:
    # 计数由单线程 TCP server 串行提交，跨进程、跨平台，不依赖文件锁或执行事件。
    state = {
        kind: {"active": 0, "peak": 0, "completed": 0} for kind in ("python", "rust")
    }

    class ActivityHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            kind, raw_delta = self.rfile.readline().decode("ascii").split()
            activity = state[kind]
            delta = int(raw_delta)
            activity["active"] += delta
            activity["peak"] = max(activity["peak"], activity["active"])
            if delta < 0:
                activity["completed"] += 1
            self.wfile.write(b"ok\n")

    script = """
import socket, sys, time
def change(delta):
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=10) as conn:
        conn.sendall(f'{sys.argv[2]} {delta}\\n'.encode('ascii'))
        with conn.makefile('rb') as reply:
            assert reply.readline() == b'ok\\n'
change(1)
time.sleep(0.02)
change(-1)
"""
    server = socketserver.TCPServer(("127.0.0.1", 0), ActivityHandler)
    command = [sys.executable, "-c", script, str(server.server_address[1])]

    def python_action() -> None:
        subprocess.run([*command, "python"], check=True, timeout=30)

    def branch() -> GraphStructure:
        native = _rust_process_graph(
            [
                (
                    "process",
                    command[0],
                    [*command[1:], "rust"],
                    str(tmp_path),
                    dict(os.environ),
                )
            ],
            [],
        )
        return Graph(
            structure=GraphStructure(
                nodes=(
                    Graph(structure=native),
                    Step(python_action),
                    Step(python_action),
                )
            )
        ).structure

    graph = Graph(structure=GraphStructure(nodes=tuple(Step(branch) for _ in range(4))))
    events: list[NodeExecutionEvent] = []

    class Sink:
        def emit(self, event: NodeExecutionEvent) -> None:
            events.append(event)

    with server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        thread.start()
        try:
            (
                partial(run_async_execution, max_workers=2)
                if asynchronous
                else partial(run_execution, max_workers=2)
            )(Execution(Graph(structure=graph.structure), events=Sink()))
        finally:
            server.shutdown()
            thread.join()
    assert state["python"]["completed"] == 8
    assert state["rust"]["completed"] == 4
    assert state["python"]["active"] == state["rust"]["active"] == 0
    assert 1 <= state["python"]["peak"] <= 2
    assert 1 <= state["rust"]["peak"] <= 4
    rust_events = [event for event in events if event.node_path.labels[-1] == "process"]
    assert sum(event.state is NodeExecutionState.STARTED for event in rust_events) == 4
    assert (
        sum(event.state is NodeExecutionState.COMPLETED for event in rust_events) == 4
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_runtime_child_preserves_interface_ports_and_ancestor_results(
    asynchronous,
) -> None:
    calls = []
    prepared = Step(lambda: "prepared", label=("prepared"))
    port = "ready"

    def expand(_ctx: NodeRunContext, result: Option[object]) -> GraphStructure:
        source = result.unwrap()
        ready = Step(lambda: calls.append(source), label=("ready"))
        provider = Graph(
            structure=GraphStructure(
                nodes=(ready,), interface=GraphInterface(outputs={port: ready})
            )
        )

        def consume() -> None:
            assert calls == ["prepared"]
            calls.append("consumed")

        use = Step(consume, label=("consume"))
        consumer = Graph(
            structure=GraphStructure(
                nodes=(use,), interface=GraphInterface(inputs={port: use})
            )
        )
        return Graph(
            structure=GraphStructure(
                nodes=(consumer, provider),
                edges=resolve_port_requirements(
                    (provider, consumer),
                    (ExactProviderRequirement(consumer, provider, port),),
                ),
            )
        ).structure

    graph = Graph.from_wiring(
        (
            prepared.wire(),
            Step(expand, label=("expand")).wire(after=(Results(prepared),)),
        )
    )
    (
        partial(run_async_execution, max_workers=1)
        if asynchronous
        else partial(run_execution, max_workers=1)
    )(
        Execution(
            Graph(structure=graph.structure),
            execution_state=ExecutionState(results=ResultStore()),
        )
    )
    assert calls == ["prepared", "consumed"]
