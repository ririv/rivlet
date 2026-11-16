"""Keep installed-wheel validation independent of shell wildcard expansion."""

from __future__ import annotations

import runpy
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_wheel.py"


@pytest.fixture(autouse=True)
def release_script_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))


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
    version = tomllib.loads((SCRIPT.parents[1] / "pyproject.toml").read_text())[
        "project"
    ]["version"]
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "rivlet.dist-info/METADATA", f"Name: rivlet\nVersion: {version}\n"
        )
    report = tmp_path / "results.xml"
    calls = []

    def run(command, *, check, **kwargs):
        assert check
        calls.append(command)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--dist",
            str(tmp_path),
            "--junitxml",
            str(report),
            "--receipts",
            str(tmp_path / "receipts"),
        ],
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
    assert (tmp_path / "receipts" / f"{wheel.name}.json").is_file()


def test_validation_failure_does_not_write_verification_record(tmp_path, monkeypatch):
    wheel = tmp_path / "candidate.whl"
    version = tomllib.loads((SCRIPT.parents[1] / "pyproject.toml").read_text())[
        "project"
    ]["version"]
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "rivlet.dist-info/METADATA", f"Name: rivlet\nVersion: {version}\n"
        )

    def run(command, **_):
        if "pytest" in command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", run)
    receipts = tmp_path / "receipts"
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--wheel", str(wheel), "--receipts", str(receipts)]
    )
    with pytest.raises(subprocess.CalledProcessError):
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert not receipts.exists()


def test_unrelated_wheel_is_rejected_before_installing(tmp_path, monkeypatch):
    wheel = tmp_path / "unrelated.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("other.dist-info/METADATA", "Name: other\nVersion: 1.0\n")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--wheel", str(wheel)])
    with pytest.raises(ValueError, match="wheel identity"):
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert calls == []
