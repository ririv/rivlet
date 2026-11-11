"""Keep installed-wheel validation independent of shell wildcard expansion."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_wheel.py"


@pytest.mark.parametrize("count", [0, 2])
def test_validation_rejects_ambiguous_wheel_directory(tmp_path, monkeypatch, count):
    for index in range(count):
        (tmp_path / f"candidate-{index}.whl").touch()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--dist", str(tmp_path)])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert caught.value.code == 2


def test_validation_uses_exact_wheel_and_isolated_installed_package(
    tmp_path, monkeypatch
):
    wheel = tmp_path / "candidate with spaces.whl"
    wheel.touch()
    report = tmp_path / "results.xml"
    calls = []

    def run(command, *, check, **kwargs):
        assert check
        calls.append(command)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--dist", str(tmp_path), "--junitxml", str(report)]
    )
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert len(calls) == 5
    assert calls[1][-1] == str(wheel)
    assert calls[2][-1] == str(wheel)
    assert "--no-deps" in calls[2]
    assert calls[3][1:3] == ["-I", "-c"]
    assert "EXTENSION_SUFFIXES" in calls[3][3]
    assert calls[4][1:4] == ["-I", "-m", "pytest"]
    assert calls[4][-1] == f"--junitxml={report}"
