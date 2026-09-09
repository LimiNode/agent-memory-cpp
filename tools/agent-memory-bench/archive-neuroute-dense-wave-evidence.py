#!/usr/bin/env python3
"""Build and validate the compact Evidence archive for PRs #258--#266."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


PR_ITEMS: dict[int, dict[str, Any]] = {
    258: {
        "status": "CONFIRMED/NEGATIVE",
        "notes": [
            "2026-08-31-neuroute-final-nonlinear-int5.md",
            "2026-08-31-neuroute-r4-int5-kernel-frontier.md",
        ],
        "receipts": [
            ("neuroute-final-nonlinear-int5/evidence.json",
             ["neuroute-final-nonlinear-int5/quality.json"]),
            ("neuroute-r4-int5-kernel-frontier/evidence.json",
             ["neuroute-r4-int5-kernel-frontier/result.json"]),
        ],
    },
    259: {
        "status": "CONFIRMED",
        "notes": ["2026-08-31-neuroute-dense-performance-audit.md"],
        "receipts": [
            ("neuroute-dense-performance-audit/matched-evidence.json",
             ["neuroute-dense-performance-audit/matched-result.json"]),
        ],
    },
    260: {
        "status": "CONFIRMED",
        "notes": ["2026-08-31-neuroute-final-rerank-ceiling.md"],
        "receipts": [
            ("neuroute-final-rerank-ceiling/evidence.json",
             ["neuroute-final-rerank-ceiling/result.json"]),
        ],
    },
    261: {
        "status": "CONFIRMED",
        "notes": ["2026-08-31-neuroute-storage-execution-separation.md"],
        "receipts": [
            ("neuroute-storage-execution-separation/evidence.json",
             ["neuroute-storage-execution-separation/result.json"]),
        ],
    },
    262: {
        "status": "CORRECTED/CONFIRMED",
        "notes": ["2026-08-31-neuroute-external-ann-comparison.md"],
        "receipts": [
            ("neuroute-external-ann-comparison/evidence.json",
             ["neuroute-external-ann-comparison/result.json"]),
        ],
    },
    263: {
        "status": "CONFIRMED CONDITIONAL CLOSURE",
        "notes": ["2026-08-31-neuroute-external-ann-comparison.md"],
        "receipts": [
            ("neuroute-dense-policy-closure/evidence.json",
             ["neuroute-dense-policy-closure/result.json"]),
        ],
    },
    264: {
        "status": "DIAGNOSTIC/NOT PRODUCTION LICENSED",
        "notes": ["2026-09-01-neuroute-actual-r4-codec-frontier.md"],
        "receipts": [
            ("neuroute-actual-r4-codec-frontier/final-evidence.json",
             ["neuroute-actual-r4-codec-frontier/final-result.json"]),
        ],
    },
    265: {
        "status": "ACTIVE PHYSICAL FOLLOW-UP/NOT PRODUCTION LICENSED",
        "notes": ["2026-09-01-neuroute-actual-r4-codec-frontier.md"],
        "receipts": [
            ("neuroute-actual-r4-representative-codec-frontier/evidence.json",
             ["neuroute-actual-r4-representative-codec-frontier/result.json"]),
        ],
    },
    266: {
        "status": "CONFIRMED TESTED IMPLEMENTATION CEILING",
        "notes": [
            "2026-08-31-neuroute-external-ann-comparison.md",
            "2026-09-01-neuroute-actual-r4-codec-frontier.md",
        ],
        "receipts": [
            ("neuroute-k8-codec-closure-evidence.json", [
                "neuroute-approximate-k8-frontier-v1/result.json",
                "neuroute-exact-k8-codec-frontier-v3/result.json",
                "neuroute-k32-physical-codec-v1/result.json",
            ]),
        ],
    },
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validate_receipt(evidence: dict[str, Any], result_hashes: list[str]) -> None:
    if evidence.get("passed") is False:
        raise ValueError("evidence receipt explicitly failed")
    expected = evidence.get("result_sha256")
    if expected is not None and expected not in result_hashes:
        raise ValueError("evidence/result SHA mismatch")
    expected_many = evidence.get("results_sha256")
    if expected_many is not None and set(expected_many.values()) != set(result_hashes):
        raise ValueError("multi-result evidence SHA mismatch")


def collect(source_root: Path, notes_root: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    files: dict[str, bytes] = {}
    members: list[dict[str, Any]] = []
    for pr, spec in PR_ITEMS.items():
        prefix = f"bundle/pr-{pr:03d}"
        receipts = []
        for receipt_index, (evidence_name, result_names) in enumerate(spec["receipts"]):
            evidence_path = source_root / evidence_name
            if not evidence_path.is_file():
                raise ValueError(f"PR #{pr}: missing evidence {evidence_path}")
            evidence_bytes = evidence_path.read_bytes()
            evidence = json.loads(evidence_bytes.decode("utf-8"))
            result_hashes = []
            archived_results = []
            for result_index, result_name in enumerate(result_names):
                result_path = source_root / result_name
                if not result_path.is_file():
                    raise ValueError(f"PR #{pr}: missing result {result_path}")
                result_bytes = result_path.read_bytes()
                result_hashes.append(sha256_bytes(result_bytes))
                archived_name = f"{prefix}/receipt-{receipt_index}/result-{result_index}-{result_path.name}"
                files[archived_name] = result_bytes
                archived_results.append({"path": archived_name,
                                         "sha256": result_hashes[-1]})
            validate_receipt(evidence, result_hashes)
            archived_evidence = f"{prefix}/receipt-{receipt_index}/{evidence_path.name}"
            files[archived_evidence] = evidence_bytes
            receipts.append({"evidence": archived_evidence,
                             "evidence_sha256": sha256_bytes(evidence_bytes),
                             "results": archived_results})
        archived_notes = []
        for note_name in spec["notes"]:
            note_path = notes_root / note_name
            if not note_path.is_file():
                raise ValueError(f"PR #{pr}: missing note {note_path}")
            archived_name = f"{prefix}/{note_name}"
            files[archived_name] = note_path.read_bytes()
            archived_notes.append(archived_name)
        members.append({"pr": pr, "interpretation_status": spec["status"],
                        "notes": archived_notes, "receipts": receipts})
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
        root = sha256_bytes(canonical(manifest["files"]))
        if root != manifest["bundle_root_sha256"]:
            raise ValueError("archive bundle root differs")
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--notes-root", type=Path,
                        default=Path(__file__).parents[2] / "guides" / "experiments")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--measured-head")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate is not None:
        manifest = validate_archive(args.validate)
        print(json.dumps({"archive_sha256": sha256(args.validate),
                          "bundle_root_sha256": manifest["bundle_root_sha256"],
                          "members": len(manifest["members"])}, sort_keys=True))
        return 0
    if args.source_root is None or args.output is None or args.measured_head is None:
        parser.error("--source-root, --output, and --measured-head are required")
    files, members = collect(args.source_root, args.notes_root)
    entries = [{"path": name, "sha256": sha256_bytes(value), "size": len(value)}
               for name, value in sorted(files.items())]
    root = sha256_bytes(canonical(entries))
    manifest = {
        "schema_version": 1,
        "family": "neuroute_dense_wave_258_266_evidence",
        "measured_head": args.measured_head,
        "scope": "PR #258--#266",
        "bundle_root_sha256": root,
        "members": members,
        "files": entries,
    }
    write_archive(args.output, files, manifest)
    validate_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output),
                      "bundle_root_sha256": root,
                      "members": len(members)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
