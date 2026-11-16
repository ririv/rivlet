"""Hash-bound local verification records, not publisher attestations."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import tomllib
from email.parser import BytesParser
from typing import TYPE_CHECKING
from zipfile import ZipFile

if TYPE_CHECKING:
    from pathlib import Path


def project_version(root: Path) -> str:
    """Read the release identity without importing the library."""
    version: object = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))[
        "project"
    ]["version"]
    if not isinstance(version, str):
        raise ValueError("project version must be a string")
    return version


def digest(path: Path) -> str:
    """Hash an artifact without loading its full contents into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_wheel_identity(wheel: Path, *, version: str) -> None:
    """Reject unrelated distributions before installing or recording them."""
    with ZipFile(wheel) as archive:
        metadata = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata) != 1:
            raise ValueError("expected exactly one wheel metadata entry")
        package = BytesParser().parsebytes(archive.read(metadata[0]))
    if package["Name"] != "rivlet" or package["Version"] != version:
        raise ValueError("wheel identity must match the Rivlet release")


def record_verification(
    artifact: Path, receipts: Path, *, version: str, expected_digest: str
) -> None:
    """Record success only if the tested artifact has not changed."""
    if digest(artifact) != expected_digest:
        raise ValueError(f"artifact changed during verification: {artifact}")
    receipt = {
        "schema_version": 1,
        "filename": artifact.name,
        "version": version,
        "sha256": expected_digest,
        "python": platform.python_version(),
        "platform": sys.platform,
    }
    receipts.mkdir(parents=True, exist_ok=True)
    # 已有记录不覆盖；重验使用新的记录目录。
    with (receipts / f"{artifact.name}.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")


def verified_artifacts(dist: Path, receipts: Path, *, version: str) -> list[Path]:
    """Require a matching successful verification record for every artifact."""
    artifacts = sorted((*dist.glob("*.whl"), *dist.glob("*.tar.gz")))
    if not artifacts:
        raise ValueError(f"no distributions in {dist}")
    for artifact in artifacts:
        value: object = json.loads(
            (receipts / f"{artifact.name}.json").read_text("utf-8")
        )
        match value:
            case {
                "schema_version": 1,
                "filename": str(filename),
                "version": str(recorded_version),
                "sha256": str(expected),
                "python": str(),
                "platform": str(),
            } if (
                filename == artifact.name
                and recorded_version == version
                and digest(artifact) == expected
            ):
                pass
            case _:
                raise ValueError(f"invalid or stale verification record: {artifact}")
    return artifacts
