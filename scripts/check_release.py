"""Validate Python, Rust, and tag identities before publishing Rivlet."""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path

RELEASE_VERSION = re.compile(
    r"(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"(?:\.post(?:0|[1-9]\d*))?"
)


def check_release_identity(root: Path, *, tag: str | None = None) -> None:
    """Require an exact release identity, allowing metadata-only post-releases."""
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    cargo = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]["package"]
    if project["name"] != "rivlet":
        raise ValueError("project name must be rivlet")
    version = project["version"]
    if not isinstance(version, str):
        raise ValueError("Python version must be a string")
    match = RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"unsupported Rivlet release version: {version}")
    # .postN 修订 Python 发行元数据，不改变对应的 Rust crate 版本。
    if cargo["version"] != match["base"]:
        raise ValueError("Python release base and Cargo version must agree")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag must be v{version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
        check_release_identity(args.root, tag=args.tag)
    except ValueError as error:
        parser.error(str(error))
    print("Release identity is valid")


if __name__ == "__main__":
    main()
