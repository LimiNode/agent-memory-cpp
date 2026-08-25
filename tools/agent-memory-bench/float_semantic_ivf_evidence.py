"""Shared fail-closed validation for float semantic-IVF evidence archives."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_archive(path: Path) -> tuple[dict[str, Any], bytes]:
    """Return a validated manifest and exact archive bytes.

    Both measurement and packaging must reject an archive whose declared member
    digests do not match its actual ZIP membership and payload bytes.
    """
    if not path.is_file():
        raise ValueError("binary centroid routing frozen float evidence ZIP missing")
    payload = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        members = manifest.get("members")
        if not (manifest.get("schema_version") == 1 and manifest.get("family") == "float_semantic_ivf_evidence_v1" and manifest.get("row_count") == 12 and isinstance(members, dict)):
            raise ValueError("binary centroid routing frozen float evidence manifest differs")
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(members) | {"bundle/evidence-manifest.json"}:
            raise ValueError("binary centroid routing frozen float evidence ZIP membership differs")
        for name, metadata in members.items():
            value = archive.read(name)
            if metadata != {"sha256": _sha256(value), "size": len(value)}:
                raise ValueError(f"binary centroid routing frozen float evidence member differs: {name}")
    return manifest, payload


def frozen_evaluation_manifest_sha256(path: Path, manifest: dict[str, Any], scale: str) -> str:
    """Return the archived evaluation-manifest identity for one frozen scale."""
    name = f"bundle/{scale}/frozen-evaluation-manifest.json"
    members = manifest.get("members")
    if not isinstance(members, dict) or name not in members:
        raise ValueError(f"float semantic IVF evidence lacks frozen evaluation manifest: {scale}")
    with zipfile.ZipFile(path) as archive:
        value = archive.read(name)
    metadata = members[name]
    if metadata != {"sha256": _sha256(value), "size": len(value)}:
        raise ValueError(f"float semantic IVF frozen evaluation manifest differs: {scale}")
    return metadata["sha256"]


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evidence.zip"
        value = b"value"
        manifest = {
            "schema_version": 1,
            "family": "float_semantic_ivf_evidence_v1",
            "row_count": 12,
            "members": {"bundle/value": {"sha256": _sha256(value), "size": len(value)}, "bundle/es-100k/frozen-evaluation-manifest.json": {"sha256": _sha256(b"evaluation"), "size": 10}},
        }
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("bundle/value", value)
            archive.writestr("bundle/es-100k/frozen-evaluation-manifest.json", b"evaluation")
            archive.writestr("bundle/evidence-manifest.json", json.dumps(manifest, sort_keys=True))
        parsed, payload = validate_archive(path)
        if parsed != manifest or payload != path.read_bytes() or frozen_evaluation_manifest_sha256(path, parsed, "es-100k") != _sha256(b"evaluation"):
            raise ValueError("binary centroid routing float evidence validator differs")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("bundle/value", b"tampered")
            archive.writestr("bundle/evidence-manifest.json", json.dumps(manifest, sort_keys=True))
        try:
            validate_archive(path)
        except ValueError:
            return
        raise ValueError("binary centroid routing tampered float evidence was accepted")


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--self-test"]:
            raise ValueError("expected --self-test")
        self_test()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"float-semantic-ivf-evidence: {error}", file=sys.stderr)
        raise SystemExit(1)
