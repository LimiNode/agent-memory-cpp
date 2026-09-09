#!/usr/bin/env python3
"""Build and validate a compact Evidence archive for the landed R4 wave.

The raw DE-1M stores are intentionally not copied.  The archive binds the
landed experiment notes to the compact result/evidence receipts that already
contain their materialization and source hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


PR_DIRS = {
    244: "neuroute-r4-representative-codec",
    245: "neuroute-r4-layout-benchmark",
    246: "neuroute-r4-int8-kernel",
    247: "neuroute-r4-batched-scorer",
    248: "neuroute-r4-mapped-access",
    249: "neuroute-r4-int8-compression",
    250: "neuroute-r4-native-end-to-end",
    251: "neuroute-r4-int8-lossless-block-codec",
    252: "neuroute-r4-nonlinear-quantization",
    253: "neuroute-r4-int5-physical-integration",
    254: "neuroute-r4-int5-layout-stress",
    255: "neuroute-r4-int5-quantization-anatomy",
    256: "neuroute-final-nonlinear-int5",
    257: "neuroute-final-full-corpus-codec",
}

NOTE_FILES = {
    244: "2026-08-31-neuroute-r4-representative-codec.md",
    245: "2026-08-31-neuroute-r4-physical-layout.md",
    246: "2026-08-31-neuroute-r4-fused-int8-kernel.md",
    247: "2026-08-31-neuroute-r4-batched-scorer.md",
    248: "2026-08-31-neuroute-r4-mapped-address-access.md",
    249: "2026-08-31-neuroute-r4-lossless-int8-compression.md",
    250: "2026-08-31-neuroute-r4-native-end-to-end.md",
    251: "2026-08-31-neuroute-r4-int8-lossless-block-codec.md",
    252: "2026-08-31-neuroute-r4-nonlinear-representative-quantization.md",
    253: "2026-08-31-neuroute-r4-int5-physical-integration.md",
    254: "2026-08-31-neuroute-r4-int5-layout-stress.md",
    255: "2026-08-31-neuroute-r4-int5-quantization-anatomy.md",
    256: "2026-08-31-neuroute-final-nonlinear-int5.md",
    257: "2026-08-31-neuroute-final-full-corpus-codec.md",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def collect(source_root: Path, notes_root: Path) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    files: dict[str, bytes] = {}
    members: list[dict[str, object]] = []
    for pr, directory in PR_DIRS.items():
        root = source_root / directory
        evidence_path = root / "evidence.json"
        if not evidence_path.is_file():
            raise ValueError(f"PR #{pr}: missing evidence.json in {root}")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        expected = evidence.get("result_sha256")
        result_candidates = sorted(root.glob("*.json"))
        result_path = next((path for path in result_candidates
                            if expected and sha256(path) == expected), None)
        if result_path is None:
            result_path = next((path for path in (root / "result.json",
                                                   root / "native-sensitivity-result.json")
                                if path.is_file()), None)
        if result_path is None:
            raise ValueError(f"PR #{pr}: missing compact result JSON in {root}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if expected and expected != sha256(result_path):
            raise ValueError(f"PR #{pr}: evidence/result SHA mismatch")
        if evidence.get("passed") is False:
            raise ValueError(f"PR #{pr}: evidence receipt explicitly failed")
        prefix = f"bundle/pr-{pr:03d}"
        for path in (evidence_path, result_path):
            relative = f"{prefix}/{path.name}"
            files[relative] = path.read_bytes()
        note_path = notes_root / NOTE_FILES[pr]
        if not note_path.is_file():
            raise ValueError(f"PR #{pr}: missing experiment note {note_path}")
        files[f"bundle/pr-{pr:03d}/{note_path.name}"] = note_path.read_bytes()
        members.append({
            "pr": pr,
            "directory": directory,
            "evidence": f"{prefix}/evidence.json",
            "result": f"{prefix}/{result_path.name}",
            "note": f"bundle/pr-{pr:03d}/{note_path.name}",
            "result_sha256": sha256(result_path),
            "evidence_sha256": sha256(evidence_path),
        })
    return files, members


def write_archive(output: Path, files: dict[str, bytes], manifest: dict[str, object]) -> None:
    files = {**files, "bundle/evidence-manifest.json": canonical(manifest)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, default=Path(__file__).parents[2] / "guides" / "experiments")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measured-head", required=True)
    args = parser.parse_args()
    files, members = collect(args.source_root, args.notes_root)
    file_entries = [{"path": name, "sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
                    for name, value in sorted(files.items())]
    root = hashlib.sha256(canonical(file_entries)).hexdigest()
    manifest = {"schema_version": 1, "family": "neuroute_r4_wave_244_257_evidence",
                "measured_head": args.measured_head, "scope": "PR #244--#257",
                "bundle_root_sha256": root, "members": members, "files": file_entries}
    write_archive(args.output, files, manifest)
    print(json.dumps({"archive_sha256": sha256(args.output),
                      "bundle_root_sha256": root, "members": len(members)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
