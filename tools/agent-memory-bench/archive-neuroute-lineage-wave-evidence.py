#!/usr/bin/env python3
"""Build a deterministic compact Evidence archive for NeuRoute PRs #267--#275.

The clean-main continuations #317--#319 are recorded as lineage-only members:
their original compact receipts were not retained in the available archive
source, so the manifest deliberately does not claim them as measured bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


PR_ITEMS: dict[int, dict[str, Any]] = {
    267: {"status": "CONFIRMED HISTORICAL RECONSTRUCTION", "note": "2026-09-02-neuroute-local-k8-historical-replay.md", "family": "neuroute-local-k8-historical-replay"},
    268: {"status": "CONFIRMED NEGATIVE", "note": "2026-09-02-neuroute-fixed-top-m-router.md", "family": "neuroute-fixed-top-m-router"},
    269: {"status": "CONFIRMED QUALITY CONTROL / NOT PRODUCTION LICENSED", "note": "2026-09-02-neuroute-shortlist-generator-bakeoff.md", "family": "neuroute-shortlist-generator-bakeoff-confirmed"},
    270: {"status": "CONFIRMED NEGATIVE", "note": "2026-09-02-neuroute-training-sufficient-router.md", "family": "neuroute-training-sufficient-router-confirmed-v2"},
    271: {"status": "CONFIRMED NEGATIVE", "note": "2026-09-02-neuroute-12-to-16-hierarchy-replay.md", "family": "neuroute-width-hierarchy-replay-confirmed-v4"},
    272: {"status": "CONFIRMED POLICY BAKE-OFF", "note": "2026-09-02-neuroute-generator-policy-bakeoff.md", "family": "neuroute-generator-policy-bakeoff"},
    273: {"status": "CONFIRMED NEGATIVE", "note": "2026-09-02-neuroute-prefix-aware-router.md", "family": "neuroute-prefix-aware-router"},
    274: {"status": "CONFIRMED NEGATIVE REPRESENTATION CEILING", "note": "2026-09-02-neuroute-binary-k8-representation-ceiling.md", "family": "neuroute-binary-k8-ceiling"},
    275: {"status": "CONFIRMED NEGATIVE FEASIBILITY AUDIT", "note": "2026-09-02-neuroute-binary-k8-mih-feasibility.md", "family": "neuroute-binary-k8-mih"},
}

LINEAGE_ONLY = {
    317: {"status": "CANONICAL CLEAN CONTINUATION OF SUPERSEDED #277", "note": "2026-09-02-neuroute-semantic-anchor-replay.md"},
    318: {"status": "CANONICAL CLEAN CONTINUATION OF SUPERSEDED #278", "note": "2026-09-02-neuroute-semantic-anchor-replay.md"},
    319: {"status": "CANONICAL CLEAN CONTINUATION OF SUPERSEDED #279", "note": "2026-09-02-neuroute-semantic-anchor-replay.md"},
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(source_root: Path, notes_root: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    files: dict[str, bytes] = {}
    members: list[dict[str, Any]] = []
    for pr, spec in PR_ITEMS.items():
        prefix = f"bundle/pr-{pr:03d}"
        family = source_root / spec["family"]
        evidence_path = family / "evidence.json"
        result_path = family / "result.json"
        note_path = notes_root / spec["note"]
        for path in (evidence_path, result_path, note_path):
            if not path.is_file():
                raise ValueError(f"PR #{pr}: missing {path}")
        evidence_bytes = evidence_path.read_bytes()
        result_bytes = result_path.read_bytes()
        evidence = json.loads(evidence_bytes.decode("utf-8"))
        result_hash = sha256_bytes(result_bytes)
        expected = evidence.get("result_sha256")
        if expected is not None and expected != result_hash:
            raise ValueError(f"PR #{pr}: result SHA mismatch")
        evidence_name = f"{prefix}/evidence.json"
        result_name = f"{prefix}/result.json"
        note_name = f"{prefix}/{note_path.name}"
        files[evidence_name] = evidence_bytes
        files[result_name] = result_bytes
        files[note_name] = note_path.read_bytes()
        members.append({
            "pr": pr,
            "interpretation_status": spec["status"],
            "note": note_name,
            "evidence": {"path": evidence_name, "sha256": sha256_bytes(evidence_bytes)},
            "result": {"path": result_name, "sha256": result_hash},
        })
    for pr, spec in LINEAGE_ONLY.items():
        note_path = notes_root / spec["note"]
        if not note_path.is_file():
            raise ValueError(f"lineage PR #{pr}: missing {note_path}")
        note_name = f"bundle/lineage/pr-{pr:03d}/{note_path.name}"
        files[note_name] = note_path.read_bytes()
        members.append({"pr": pr, "interpretation_status": spec["status"], "note": note_name, "receipts": "not retained; lineage-only"})
    return files, members


def write_archive(output: Path, files: dict[str, bytes], manifest: dict[str, Any]) -> None:
    contents = {**files, "bundle/evidence-manifest.json": canonical(manifest)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, contents[name])


def validate_archive(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("archive member ordering differs")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        for entry in manifest["files"]:
            value = archive.read(entry["path"])
            if len(value) != entry["size"] or sha256_bytes(value) != entry["sha256"]:
                raise ValueError(f"archive member differs: {entry['path']}")
        if sha256_bytes(canonical(manifest["files"])) != manifest["bundle_root_sha256"]:
            raise ValueError("archive bundle root differs")
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--notes-root", type=Path, default=Path(__file__).parents[2] / "guides" / "experiments")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--measured-head")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate is not None:
        manifest = validate_archive(args.validate)
        print(json.dumps({"archive_sha256": sha256(args.validate), "bundle_root_sha256": manifest["bundle_root_sha256"], "members": len(manifest["members"])}, sort_keys=True))
        return 0
    if args.source_root is None or args.output is None or args.measured_head is None:
        parser.error("--source-root, --output, and --measured-head are required")
    files, members = collect(args.source_root, args.notes_root)
    entries = [{"path": name, "sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())]
    manifest = {
        "schema_version": 1,
        "family": "neuroute_lineage_wave_267_275",
        "measured_head": args.measured_head,
        "scope": "Measured receipts PR #267--#275; lineage-only notes for canonical clean continuations #317--#319",
        "excluded": "Original #277--#279 receipts were not retained; #276 was gated off.",
        "bundle_root_sha256": sha256_bytes(canonical(entries)),
        "members": members,
        "files": entries,
    }
    write_archive(args.output, files, manifest)
    validate_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"], "members": len(members)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
