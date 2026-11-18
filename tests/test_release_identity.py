from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release.py"


def check_identity(
    root: Path,
    version: str,
    cargo: str,
    tag: str | None,
    *,
    node: str | None = None,
    crate_pin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "rivlet"\nversion = "{version}"\n'
    )
    cargo_manifest = f'[workspace.package]\nversion = "{cargo}"\n'
    if node is not None:
        cargo_manifest += (
            "\n[workspace.dependencies]\n"
            f'rivlet-core = {{ path = "crates/core", version = "={crate_pin or cargo}" }}\n'
        )
        binding = root / "bindings/node"
        binding.mkdir(parents=True)
        (binding / "package.json").write_text(
            json.dumps({"name": "rivlet", "version": node})
        )
        (binding / "package-lock.json").write_text(
            json.dumps(
                {"name": "rivlet", "version": node, "packages": {"": {"version": node}}}
            )
        )
        addon = root / "crates/node"
        addon.mkdir(parents=True)
        (addon / "Cargo.toml").write_text(f'[package]\nversion = "{node}"\n')
    (root / "Cargo.toml").write_text(cargo_manifest)
    env = dict(os.environ)
    env.pop("GITHUB_REF_TYPE", None)
    env.pop("GITHUB_REF_NAME", None)
    args = [sys.executable, str(SCRIPT), "--root", str(root)]
    if tag is not None:
        args.extend(("--tag", tag))
    if node is not None:
        args.append("--all-channels")
    return subprocess.run(args, env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("0.2.0", None),
        ("0.2.0", "v0.2.0"),
        ("0.2.0.post0", "v0.2.0.post0"),
        ("0.2.0.post1", None),
        ("0.2.0.post1", "v0.2.0.post1"),
        ("0.2.0.post12", "v0.2.0.post12"),
    ],
)
def test_release_identity_accepts_final_and_post_versions(
    tmp_path: Path, version: str, tag: str | None
) -> None:
    result = check_identity(tmp_path, version, "0.2.0", tag)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("version", "cargo", "tag", "message"),
    [
        ("0.2.0.post1", "0.1.0", None, "Cargo version must agree"),
        ("0.2.0", "0.2.1", None, "Cargo version must agree"),
        ("0.2.0.post1", "0.2.0", "v0.2.0", "release tag must be"),
        ("0.2.0.post1", "0.2.0", "v0.2.0.post2", "release tag must be"),
        ("0.2.0.post", "0.2.0", None, "unsupported Rivlet release version"),
        ("0.2.0.post01", "0.2.0", None, "unsupported Rivlet release version"),
        ("0.2.0+docs", "0.2.0", None, "unsupported Rivlet release version"),
    ],
)
def test_release_identity_rejects_invalid_publication_metadata(
    tmp_path: Path, version: str, cargo: str, tag: str | None, message: str
) -> None:
    result = check_identity(tmp_path, version, cargo, tag)
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("node", "crate_pin", "error"),
    [
        ("0.5.0", "0.5.0", None),
        ("0.4.0", "0.5.0", "Node addon and Cargo versions must agree"),
        ("0.5.0", "0.4.0", "Cargo dependency rivlet-core must require exactly 0.5.0"),
    ],
)
def test_check_all_channels_with_python_post_release(
    tmp_path: Path, node: str, crate_pin: str, error: str | None
) -> None:
    result = check_identity(
        tmp_path, "0.5.0.post1", "0.5.0", "v0.5.0.post1", node=node, crate_pin=crate_pin
    )
    if error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert error in result.stderr
