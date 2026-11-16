"""Build and test an independent Rust extension without installing it into the host environment."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "python-native"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maturin", default="maturin", help="Explicit maturin executable"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rivlet-native-bridge-") as temporary:
        directory = Path(temporary)
        dist = directory / "dist"
        subprocess.run(
            [
                args.maturin,
                "build",
                "--locked",
                "--release",
                "--manifest-path",
                str(EXAMPLE / "Cargo.toml"),
                "--interpreter",
                sys.executable,
                "--out",
                str(dist),
            ],
            cwd=ROOT,
            check=True,
        )
        wheels = tuple(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError(
                "native bridge validation requires exactly one example wheel"
            )
        installed = directory / "installed"
        with zipfile.ZipFile(wheels[0]) as wheel:
            # 只解包本次构建产物，仍拒绝归档越界，不覆盖任何已安装环境。
            for member in wheel.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError(f"invalid wheel member: {member.filename}")
            wheel.extractall(installed)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(installed)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "-q",
                str(EXAMPLE / "test_integration.py"),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, str(EXAMPLE / "demo.py")],
            cwd=ROOT,
            env=environment,
            check=True,
        )


if __name__ == "__main__":
    main()
