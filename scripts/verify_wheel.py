"""Install and test exactly one wheel using the current Python interpreter."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--dist", type=Path, default=root / "dist")
    parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args()
    wheels = sorted(args.dist.resolve().glob("*.whl"))
    if len(wheels) != 1:
        parser.error(f"expected exactly one wheel in {args.dist}, found {len(wheels)}")
    wheel = wheels[0]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    subprocess.run(
        [sys.executable, str(root / "scripts" / "check_release.py")], check=True
    )
    subprocess.run(
        [sys.executable, "-I", "-m", "twine", "check", "--strict", str(wheel)],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            str(wheel),
        ],
        check=True,
    )
    # 隔离导入，并核对扩展确实位于当前解释器的安装目录，而不是 checkout 或 editable。
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import importlib.metadata, importlib.machinery, pathlib, sys, sysconfig; "
            "import rivlet, rivlet._native; "
            "assert importlib.metadata.version('rivlet') == sys.argv[1]; "
            "roots = [pathlib.Path(sysconfig.get_path(k)).resolve() "
            "for k in ('purelib', 'platlib')]; "
            "paths = [pathlib.Path(m.__file__).resolve() for m in (rivlet, rivlet._native)]; "
            "assert all(any(p.is_relative_to(r) for r in roots) for p in paths), paths; "
            "assert any(str(paths[1]).endswith(s) "
            "for s in importlib.machinery.EXTENSION_SUFFIXES), paths[1]; "
            "print(*paths, sep='\\n')",
            version,
        ],
        check=True,
    )
    command = [sys.executable, "-I", "-m", "pytest", str(root / "tests")]
    if args.junitxml is not None:
        command.append(f"--junitxml={args.junitxml.resolve()}")
    subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
