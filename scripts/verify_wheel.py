"""Install and test exactly one wheel using the current Python interpreter."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from release_artifacts import (
    check_wheel_identity,
    digest,
    project_version,
    record_verification,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--dist", type=Path)
    selection.add_argument("--wheel", type=Path)
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    wheels = (
        [args.wheel.resolve()]
        if args.wheel is not None
        else sorted((args.dist or root / "dist").resolve().glob("*.whl"))
    )
    if len(wheels) != 1:
        parser.error(f"expected exactly one wheel in {args.dist}, found {len(wheels)}")
    wheel = wheels[0]
    version = project_version(root)
    expected_digest = digest(wheel)
    check_wheel_identity(wheel, version=version)
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
    if args.receipts is not None:
        record_verification(
            wheel,
            args.receipts.resolve(),
            version=version,
            expected_digest=expected_digest,
        )


if __name__ == "__main__":
    main()
