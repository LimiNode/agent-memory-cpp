#!/usr/bin/env python3
"""Build and validate the compact MDBX finalist evidence archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def collect(args: argparse.Namespace) -> tuple[dict[str, bytes], dict]:
    paths = {
        "bundle/mdbx-finalist-storage.corrected.result.json": args.storage_result,
        "bundle/mdbx-z0-compression.result.json": args.z0_result,
        "bundle/fixture.manifest.json": args.fixture_manifest,
        "bundle/mdbx-finalist-storage.summary.json": args.storage_summary,
        "bundle/mdbx-z0-compression.summary.json": args.z0_summary,
        "bundle/mdbx-finalist-storage.note.md": args.storage_note,
        "bundle/mdbx-z0-compression.note.md": args.z0_note,
    }
    files: dict[str, bytes] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"missing archive input: {path}")
        files[name] = path.read_bytes()
    entries = [{"path": name, "sha256": sha256_bytes(payload), "size": len(payload)}
               for name, payload in sorted(files.items())]
    manifest = {
        "schema_version": 1,
        "family": "mdbx_finalist_evidence_v1",
        "measured_head": args.measured_head,
        "scope": "MDBX finalist storage bakeoff plus Z0 offline compression screen",
        "artifact_scope": ["corrected MDBX receipt", "Z0 compression receipt", "fixture manifest", "compact summaries", "experiment notes"],
        "files": entries,
        "bundle_root_sha256": sha256_bytes(canonical(entries)),
    }
    return files, manifest


def write_archive(output: Path, files: dict[str, bytes], manifest: dict) -> None:
    all_files = {**files, "bundle/evidence-manifest.json": canonical(manifest)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(all_files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, all_files[name])


def validate_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != sorted(names):
            raise ValueError("archive member ordering differs")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        for entry in manifest["files"]:
            payload = archive.read(entry["path"])
            if len(payload) != entry["size"] or sha256_bytes(payload) != entry["sha256"]:
                raise ValueError(f"archive member differs: {entry['path']}")
        if manifest["bundle_root_sha256"] != sha256_bytes(canonical(manifest["files"])):
            raise ValueError("bundle root differs")
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--measured-head")
    parser.add_argument("--storage-result", type=Path)
    parser.add_argument("--z0-result", type=Path)
    parser.add_argument("--fixture-manifest", type=Path)
    parser.add_argument("--storage-summary", type=Path)
    parser.add_argument("--z0-summary", type=Path)
    parser.add_argument("--storage-note", type=Path)
    parser.add_argument("--z0-note", type=Path)
    args = parser.parse_args()
    if args.validate:
        manifest = validate_archive(args.validate)
        print(json.dumps({"archive_sha256": sha256(args.validate), "bundle_root_sha256": manifest["bundle_root_sha256"], "members": len(manifest["files"])}, sort_keys=True))
        return
    required = (args.output, args.measured_head, args.storage_result, args.z0_result, args.fixture_manifest,
                args.storage_summary, args.z0_summary, args.storage_note, args.z0_note)
    if any(value is None for value in required):
        parser.error("archive inputs and --measured-head are required")
    files, manifest = collect(args)
    write_archive(args.output, files, manifest)
    validate_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"], "members": len(manifest["files"])}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise SystemExit(f"archive-mdbx-finalist-evidence: {error}")
