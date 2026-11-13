from __future__ import annotations

import os
import socketserver
import subprocess
import sys
import threading

import pytest

from rivlet import (
    AsyncRunOptions,
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
    ThreadRunOptions,
    resolve_port_requirements,
)
from rivlet._native import _rust_process_graph


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_and_python_actions_share_dynamic_budget(tmp_path, asynchronous) -> None:
    # 计数由单线程 TCP server 串行提交，跨进程、跨平台，不依赖文件锁或执行事件。
    state = {"active": 0, "peak": 0, "completed": 0}

    class ActivityHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            delta = int(self.rfile.readline())
            state["active"] += delta
            state["peak"] = max(state["peak"], state["active"])
            if delta < 0:
                state["completed"] += 1
            self.wfile.write(b"ok\n")

    script = """
import socket, sys, time
def change(delta):
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=10) as conn:
        conn.sendall(f'{delta}\\n'.encode('ascii'))
        with conn.makefile('rb') as reply:
            assert reply.readline() == b'ok\\n'
change(1)
time.sleep(0.02)
change(-1)
"""
    server = socketserver.TCPServer(("127.0.0.1", 0), ActivityHandler)
    command = [sys.executable, "-c", script, str(server.server_address[1])]

    def python_action() -> None:
        subprocess.run(command, check=True, timeout=30)

    def branch() -> GraphStructure:
        native = _rust_process_graph(
            [("process", command[0], command[1:], str(tmp_path), dict(os.environ))],
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
            Execution(Graph(structure=graph.structure), events=Sink()).run(
                AsyncRunOptions(concurrency=2)
                if asynchronous
                else ThreadRunOptions(workers=2)
            )
        finally:
            server.shutdown()
            thread.join()
    assert state["completed"] == 12
    assert state["active"] == 0
    assert 1 <= state["peak"] <= 2
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
    Execution(
        Graph(structure=graph.structure),
        execution_state=ExecutionState(results=ResultStore()),
    ).run(
        AsyncRunOptions(concurrency=1) if asynchronous else ThreadRunOptions(workers=1)
    )
    assert calls == ["prepared", "consumed"]
