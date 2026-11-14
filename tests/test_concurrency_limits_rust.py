from __future__ import annotations

import asyncio
import os
import socketserver
import subprocess
import sys
import threading
from collections import Counter

import pytest

from rivlet import Graph, GraphStructure, Step, arun, run
from rivlet._native import _rust_process_graph


@pytest.mark.parametrize("asynchronous", [False, True])
def test_rust_python_and_dynamic_children_share_ancestor_limits(tmp_path, asynchronous):
    active = Counter()
    peak = Counter()
    completed = Counter()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            branch, language, raw_delta = self.rfile.readline().decode("ascii").split()
            delta = int(raw_delta)
            for key in ("all", branch, language):
                active[key] += delta
                peak[key] = max(peak[key], active[key])
            if delta < 0:
                completed[language] += 1
            self.wfile.write(b"ok\n")

    script = """
import socket, sys, time
def change(delta):
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=5) as conn:
        conn.sendall(f'{sys.argv[2]} {sys.argv[3]} {delta}\\n'.encode('ascii'))
        with conn.makefile('rb') as reply:
            assert reply.readline() == b'ok\\n'
change(1)
time.sleep(0.02)
change(-1)
"""
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    base = ["-c", script, str(server.server_address[1])]

    def dynamic(branch):
        rust = _rust_process_graph(
            [
                (
                    f"{branch}-{index}",
                    sys.executable,
                    [*base, branch, "rust"],
                    str(tmp_path),
                    dict(os.environ),
                )
                for index in range(3)
            ],
            [],
        )

        def python_action():
            subprocess.run(
                [sys.executable, *base, branch, "python"], check=True, timeout=10
            )

        return GraphStructure(
            nodes=(Graph(rust), *(Step(python_action) for _ in range(3)))
        )

    # 两层动态 session 继承同一个受限祖先；父 action 不能占着名额等待。
    left = Graph(
        GraphStructure(nodes=tuple(Step(lambda: dynamic("left")) for _ in range(2)))
    )
    right = Graph(
        GraphStructure(nodes=tuple(Step(lambda: dynamic("right")) for _ in range(2)))
    )
    root = (left | right).graph()
    limits = {root: 2, left: 1, right: 1}
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}
    )
    with server:
        thread.start()
        try:
            if asynchronous:
                asyncio.run(arun(root, max_workers=8, max_concurrency=limits))
            else:
                run(root, max_workers=8, max_concurrency=limits)
        finally:
            server.shutdown()
            thread.join(5)
    assert completed == {"rust": 12, "python": 12}
    assert peak["all"] == 2
    assert peak["left"] == peak["right"] == 1
    assert not +active
