"""Protect source-only builds and hash-bound publication gates."""

from __future__ import annotations

import importlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("release_artifacts")


def test_artifact_change_during_verification_has_no_success_record(tmp_path, release):
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"original")
    expected = release.digest(wheel)
    wheel.write_bytes(b"changed")
    receipts = tmp_path / "receipts"
    with pytest.raises(ValueError, match="changed during verification"):
        release.record_verification(
            wheel, receipts, version="1.0.0", expected_digest=expected
        )
    assert not receipts.exists()


def test_changed_artifact_is_rejected_before_publication(tmp_path, release):
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"verified")
    receipts = tmp_path / "receipts"
    release.record_verification(
        wheel, receipts, version="1.0.0", expected_digest=release.digest(wheel)
    )
    assert release.verified_artifacts(tmp_path, receipts, version="1.0.0") == [wheel]
    wheel.write_bytes(b"not the verified bytes")
    with pytest.raises(ValueError, match="stale verification"):
        release.verified_artifacts(tmp_path, receipts, version="1.0.0")


def test_missing_verification_record_does_not_allow_publication(tmp_path, release):
    (tmp_path / "candidate.whl").write_bytes(b"candidate")
    with pytest.raises(FileNotFoundError):
        release.verified_artifacts(tmp_path, tmp_path / "receipts", version="1.0.0")


@pytest.fixture
def builder(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("build_dist")
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="rivlet"\nversion="1.0.0"\n')
    (root / "Cargo.toml").write_text('[workspace.package]\nversion="1.0.0"\n')
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "require_maturin", lambda: None)
    return module


def source_archive(path, root):
    with tarfile.open(path, "w:gz") as archive:
        for name in ("pyproject.toml", "Cargo.toml"):
            archive.add(root / name, arcname=f"rivlet-1.0.0/{name}")


@pytest.mark.parametrize("member_name", ["../escape", "rivlet-1.0.0/../../escape"])
def test_sdist_rejects_escaping_members(tmp_path, builder, member_name):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo(member_name)
        info.size = 1
        stream.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="invalid sdist member"):
        builder.unpack_sdist(archive, tmp_path / "unpacked")
    assert not (tmp_path / "escape").exists()


def test_wheels_are_built_from_sdist_not_checkout(tmp_path, builder, monkeypatch):
    archive = tmp_path / "source with spaces.tar.gz"
    source_archive(archive, builder.ROOT)
    calls = []

    def run(command, *, cwd, env, check):
        assert check
        assert cwd != builder.ROOT
        assert (cwd / "pyproject.toml").is_file()
        assert env["RUSTUP_TOOLCHAIN"] == builder.RUST_VERSION
        calls.append(command)

    monkeypatch.setattr(subprocess, "run", run)
    flags = ["--zig", "--manylinux", "2_28"]
    builder.build_wheels(archive, tmp_path / "out", ["python with spaces"], flags)
    command = calls[0]
    assert command[:6] == [
        sys.executable,
        "-m",
        "maturin",
        "build",
        "--release",
        "--locked",
    ]
    assert command[6:9] == flags
    assert command[-2:] == ["--interpreter", "python with spaces"]


def test_publication_check_does_not_upload(tmp_path, monkeypatch, release):
    publisher = importlib.import_module("publish_dist")
    version = release.project_version(SCRIPTS.parent)
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"candidate")
    receipts = tmp_path / "receipts"
    release.record_verification(
        wheel, receipts, version=version, expected_digest=release.digest(wheel)
    )
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **_: calls.append(command))
    monkeypatch.setattr(
        sys, "argv", ["publish_dist.py", "check", str(tmp_path), str(receipts)]
    )
    publisher.main()
    assert len(calls) == 1
    assert calls[0][2:5] == ["-m", "twine", "check"]
    assert "upload" not in calls[0]


def test_container_uses_shared_entry_without_zig(tmp_path, builder, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda command, **_: calls.append(command))
    archive = tmp_path / "source.tar.gz"
    builder.build_container(archive, tmp_path / "out", "3.13")
    command = calls[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "/workflow/scripts/build_manylinux.sh" in command
    assert command[-4:] == [
        "cp313",
        builder.RUST_VERSION,
        builder.MATURIN_VERSION,
        archive.name,
    ]
    assert "--zig" not in command


def test_linux_zig_entry_selects_explicit_baseline(tmp_path, builder, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(builder, "build_wheels", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_dist.py",
            "linux-zig",
            str(tmp_path / "source.tar.gz"),
            str(tmp_path / "out"),
            "--interpreter",
            "python3.13",
        ],
    )
    builder.main()
    assert calls[0][2] == ["python3.13"]
    assert calls[0][3] == [
        "--target",
        "x86_64-unknown-linux-gnu",
        "--manylinux",
        "2_28",
        "--zig",
    ]
