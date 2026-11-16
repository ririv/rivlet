"""Check recorded artifact hashes before staging or explicitly uploading a release."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from build_dist import empty_output
from check_release import check_release_identity
from release_artifacts import project_version, verified_artifacts


def stage_artifacts(
    artifacts: list[Path], receipts: Path, out: Path, version: str
) -> list[Path]:
    """Copy candidates and recheck the copies against the original records."""
    out = empty_output(out)
    for artifact in artifacts:
        target = out / artifact.name
        shutil.copyfile(artifact, target)
    return verified_artifacts(out, receipts, version=version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "stage", "upload"):
        command = commands.add_parser(name)
        command.add_argument("dist", type=Path)
        command.add_argument("receipts", type=Path)
        if name == "stage":
            command.add_argument("out", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    check_release_identity(root)
    artifacts = verified_artifacts(
        args.dist.resolve(), args.receipts.resolve(), version=project_version(root)
    )
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "twine",
            "check",
            "--strict",
            *map(str, artifacts),
        ],
        check=True,
    )
    match args.command:
        case "stage":
            stage_artifacts(
                artifacts, args.receipts.resolve(), args.out, project_version(root)
            )
        case "upload":
            if "TWINE_REPOSITORY_URL" in os.environ:
                raise ValueError(
                    "unset TWINE_REPOSITORY_URL; this entry uploads to repository pypi"
                )
            with tempfile.TemporaryDirectory(prefix="rivlet-publish-") as temporary:
                candidates = stage_artifacts(
                    artifacts,
                    args.receipts.resolve(),
                    Path(temporary),
                    project_version(root),
                )
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "twine",
                        "upload",
                        "--non-interactive",
                        "--repository",
                        "pypi",
                        *map(str, candidates),
                    ],
                    check=True,
                )
        case "check":
            print(f"Verified {len(artifacts)} distribution hashes")


if __name__ == "__main__":
    main()
