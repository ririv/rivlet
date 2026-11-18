"""Validate Python, Rust, and tag identities before publishing Rivlet."""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from pathlib import Path

RELEASE_VERSION = re.compile(
    r"(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"(?:\.post(?:0|[1-9]\d*))?"
)


def check_release_identity(
    root: Path, *, tag: str | None = None, all_channels: bool = False
) -> None:
    """Require an exact release identity, allowing metadata-only post-releases."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))[
        "workspace"
    ]
    if project["name"] != "rivlet":
        raise ValueError("project name must be rivlet")
    version = project["version"]
    if not isinstance(version, str):
        raise ValueError("Python version must be a string")
    match = RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"unsupported Rivlet release version: {version}")
    # .postN 修订 Python 发行元数据，不改变对应的 Rust crate 版本。
    base = match["base"]
    if workspace["package"]["version"] != base:
        raise ValueError("Python release base and Cargo version must agree")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag must be v{version}")
    if all_channels:
        _check_other_channels(root, workspace, base)


def _check_other_channels(root: Path, workspace: dict[str, object], base: str) -> None:
    dependencies = workspace.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError(
            "Cargo workspace dependencies do not declare the Rivlet crates"
        )
    for name, spec in dependencies.items():
        if name.startswith("rivlet-") and (
            not isinstance(spec, dict) or spec.get("version") != f"={base}"
        ):
            raise ValueError(f"Cargo dependency {name} must require exactly {base}")
    manifest = json.loads((root / "bindings/node/package.json").read_text("utf-8"))
    lock = json.loads((root / "bindings/node/package-lock.json").read_text("utf-8"))
    native = tomllib.loads((root / "crates/node/Cargo.toml").read_text("utf-8"))
    if manifest["name"] != "rivlet" or lock["name"] != "rivlet":
        raise ValueError("Node project and lockfile must be named rivlet")
    if (
        manifest["version"] != base
        or lock["version"] != base
        or lock["packages"][""]["version"] != base
        or native["package"]["version"] != base
    ):
        raise ValueError(
            "npm package, lockfile, Node addon and Cargo versions must agree"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-channels",
        action="store_true",
        help="also check the npm identities and exact workspace dependency versions",
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--tag",
        default=(
            os.environ["GITHUB_REF_NAME"]
            if os.environ.get("GITHUB_REF_TYPE") == "tag"
            else None
        ),
    )
    args = parser.parse_args()
    try:
        check_release_identity(args.root, tag=args.tag, all_channels=args.all_channels)
    except (KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    print("Release identity is valid")


if __name__ == "__main__":
    main()
