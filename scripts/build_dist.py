"""Build release distributions through explicit native, container and Zig entries."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from check_release import check_release_identity
from release_artifacts import digest, project_version, record_verification

ROOT = Path(__file__).resolve().parents[1]
RUST_VERSION = "1.95.0"
MATURIN_VERSION = "1.15.0"
PYTHON_VERSIONS = ("3.12", "3.13", "3.14", "3.15")
MANYLINUX = "2_28"
MANYLINUX_IMAGE = f"quay.io/pypa/manylinux_{MANYLINUX}_x86_64"


def require_maturin() -> None:
    """Require the release tool rather than installing or selecting one implicitly."""
    if importlib.metadata.version("maturin") != MATURIN_VERSION:
        raise ValueError(f"release builds require maturin=={MATURIN_VERSION}")


def empty_output(path: Path) -> Path:
    """Never overwrite an earlier build."""
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    return path


def unpack_sdist(archive: Path, destination: Path) -> Path:
    """Extract one portable source tree without links or escaping members."""
    name = f"rivlet-{project_version(ROOT)}"
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if (
                not path.parts
                or path.parts[0] != name
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"invalid sdist member: {member.name}")
        source.extractall(destination, filter="data")
    root = destination / name
    check_release_identity(root)
    if project_version(root) != project_version(ROOT):
        raise ValueError("sdist and checkout versions must agree")
    return root


def build_wheels(
    archive: Path, out: Path, interpreters: list[str], flags: list[str]
) -> None:
    """Build only from the supplied sdist using an explicitly prepared toolchain."""
    out = empty_output(out)
    require_maturin()
    interpreters = [
        str(Path(value).resolve())
        if os.sep in value or (os.altsep is not None and os.altsep in value)
        else value
        for value in interpreters
    ]
    env = dict(os.environ, RUSTUP_TOOLCHAIN=RUST_VERSION)
    if sys.platform == "darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = "11.0"
    with tempfile.TemporaryDirectory(prefix="rivlet-wheel-") as temporary:
        root = unpack_sdist(archive, Path(temporary))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "maturin",
                "build",
                "--release",
                "--locked",
                *flags,
                "--out",
                str(out),
                "--interpreter",
                *interpreters,
            ],
            cwd=root,
            env=env,
            check=True,
        )


def verify_source(archive: Path, receipts: Path) -> None:
    """Verify an sdist by building, installing and testing its native wheel."""
    expected = digest(archive)
    with tempfile.TemporaryDirectory(prefix="rivlet-source-check-") as temporary:
        directory = Path(temporary)
        root = unpack_sdist(archive, directory / "source")
        wheels = directory / "wheels"
        build_wheels(archive, wheels, [sys.executable], [])
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "verify_wheel.py"),
                "--dist",
                str(wheels),
                "--source-root",
                str(root),
            ],
            check=True,
        )
    record_verification(
        archive, receipts, version=project_version(ROOT), expected_digest=expected
    )


def build_container(archive: Path, out: Path, python: str) -> None:
    """Run the same manylinux entry inside the explicit official container."""
    if sys.platform != "linux":
        raise ValueError("the manylinux container entry requires a Linux Docker host")
    out = empty_output(out)
    abi = f"cp{python.replace('.', '')}"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={ROOT},dst=/workflow,readonly",
            "--mount",
            f"type=bind,src={archive.parent},dst=/input,readonly",
            "--mount",
            f"type=bind,src={out},dst=/output",
            MANYLINUX_IMAGE,
            "bash",
            "/workflow/scripts/build_manylinux.sh",
            abi,
            RUST_VERSION,
            MATURIN_VERSION,
            archive.name,
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("settings")
    source = commands.add_parser("sdist")
    source.add_argument("out", type=Path)
    verify = commands.add_parser("verify-source")
    verify.add_argument("sdist", type=Path)
    verify.add_argument("receipts", type=Path)
    for name in ("native", "manylinux", "linux-zig", "container"):
        wheel = commands.add_parser(name)
        wheel.add_argument("sdist", type=Path)
        wheel.add_argument("out", type=Path)
        if name == "container":
            wheel.add_argument(
                "--python-version", choices=PYTHON_VERSIONS, required=True
            )
        else:
            wheel.add_argument("--interpreter", nargs="+", required=True)
            if name == "native":
                wheel.add_argument(
                    "--target",
                    choices=("aarch64-apple-darwin", "x86_64-pc-windows-msvc"),
                    required=True,
                )
    args = parser.parse_args()
    check_release_identity(ROOT)
    match args.command:
        case "settings":
            settings = {
                "rust": RUST_VERSION,
                "maturin": MATURIN_VERSION,
                "python": json.dumps(PYTHON_VERSIONS),
                "sdist": f"rivlet-{project_version(ROOT)}.tar.gz",
            }
            for key, value in settings.items():
                print(f"{key}={value}")
        case "sdist":
            require_maturin()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "maturin",
                    "sdist",
                    "--out",
                    str(empty_output(args.out)),
                ],
                cwd=ROOT,
                env=dict(os.environ, RUSTUP_TOOLCHAIN=RUST_VERSION),
                check=True,
            )
        case "verify-source":
            verify_source(args.sdist.resolve(), args.receipts.resolve())
        case "container":
            build_container(args.sdist.resolve(), args.out, args.python_version)
        case "native":
            if sys.platform == "linux":
                raise ValueError(
                    "choose manylinux or linux-zig explicitly for Linux wheels"
                )
            build_wheels(
                args.sdist.resolve(),
                args.out,
                args.interpreter,
                ["--target", args.target],
            )
        case "manylinux" | "linux-zig":
            if sys.platform != "linux":
                raise ValueError("Linux wheel entries require a Linux build host")
            flags = ["--target", "x86_64-unknown-linux-gnu", "--manylinux", MANYLINUX]
            if args.command == "linux-zig":
                flags.append("--zig")
            build_wheels(args.sdist.resolve(), args.out, args.interpreter, flags)


if __name__ == "__main__":
    main()
