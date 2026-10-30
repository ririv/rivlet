from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release.py"


def check_identity(
    root: Path, version: str, cargo: str, tag: str | None
) -> subprocess.CompletedProcess[str]:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "rivlet"\nversion = "{version}"\n'
    )
    (root / "Cargo.toml").write_text(f'[workspace.package]\nversion = "{cargo}"\n')
    env = dict(os.environ)
    env.pop("GITHUB_REF_TYPE", None)
    env.pop("GITHUB_REF_NAME", None)
    args = [sys.executable, str(SCRIPT), "--root", str(root)]
    if tag is not None:
        args.extend(("--tag", tag))
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
