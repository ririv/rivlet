"""Exercise native process boundaries without relying on a command shell."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from rivlet import (
    AsyncRunOptions,
    Execution,
    Graph,
    GraphStructure,
    ThreadRunOptions,
)
from rivlet._native import _native_process_graph


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_process_preserves_paths_arguments_and_explicit_environment(
    tmp_path: Path, monkeypatch, asynchronous: bool
) -> None:
    directory = tmp_path / "native 空间"
    directory.mkdir()
    log_path = directory / "进程 output.log"
    arguments = ["", "with spaces", 'a"quoted"value', "trailing\\", "中文"]
    monkeypatch.setenv("RIVLET_NOT_FOR_CHILD", "must not leak")
    environment = dict(os.environ)
    del environment["RIVLET_NOT_FOR_CHILD"]
    environment["RIVLET_PROCESS_VALUE"] = "显式 environment"
    script = """
import json, os, pathlib, sys
pathlib.Path('result.json').write_text(json.dumps({
    'cwd': str(pathlib.Path.cwd()),
    'arguments': sys.argv[1:],
    'value': os.environ['RIVLET_PROCESS_VALUE'],
    'inherited': 'RIVLET_NOT_FOR_CHILD' in os.environ,
}), encoding='utf-8')
print('stdout marker', flush=True)
print('stderr marker', file=sys.stderr, flush=True)
"""
    native = Graph(
        structure=_native_process_graph(
            [
                (
                    "process",
                    sys.executable,
                    ["-c", script, *arguments],
                    str(directory),
                    environment,
                )
            ],
            [],
        )
    )
    Execution(
        Graph(structure=GraphStructure(nodes=(native,))),
        node_log_paths={native: log_path},
    ).run(
        AsyncRunOptions(concurrency=2) if asynchronous else ThreadRunOptions(workers=2)
    )
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert Path(result["cwd"]) == directory.resolve()
    assert result["arguments"] == arguments
    assert result["value"] == environment["RIVLET_PROCESS_VALUE"]
    assert not result["inherited"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "stdout marker",
        "stderr marker",
    ]
