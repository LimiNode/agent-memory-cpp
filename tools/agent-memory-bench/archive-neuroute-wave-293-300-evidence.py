#!/usr/bin/env python3
"""Build the deterministic compact Evidence archive for NeuRoute #293--#300.

Only compact JSON receipts and the experiment notes are archived.  The source
artifacts were retained locally but were not all produced by an independent
evidence writer, so the manifest labels each member's evidentiary status rather
than upgrading it to a replay claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


MEMBERS: dict[int, dict[str, Any]] = {
    293: {"status": "source-bound", "notes": ["2026-09-04-neuroute-document-cascade-code-family.md"],
          "sources": ["document-cascade-code-family-v6.json", "document-cascade-tail-v1.json"]},
    294: {"status": "source-bound", "notes": ["2026-09-04-neuroute-document-cascade-code-family.md"],
          "sources": ["document-cascade-code-family-v6.json", "final-rerank-code-family-v2.json"]},
    295: {"status": "superseded", "notes": ["2026-09-04-neuroute-document-cascade-code-family.md"], "sources": []},
    296: {"status": "source-bound", "notes": ["2026-09-04-neuroute-document-cascade-code-family.md"],
          "sources": ["document-codec-native-benchmark-final64-v1.json", "document-codec-native-optimized-5000-v1.json"]},
    297: {"status": "protocol-only", "notes": ["2026-09-05-neuroute-lthq-retrieval-supervised.md"], "sources": []},
    298: {"status": "corrected-protocol", "notes": ["2026-09-05-neuroute-routing-architecture-bakeoff.md"], "sources": []},
    299: {"status": "source-bound-corrected", "notes": ["2026-09-05-neuroute-learned-ordinal-lattice-router.md"],
          "sources": ["ordinal-lattice-de1m/manifest.json", "ordinal-lattice-de1m/result.json",
                      "ordinal-lattice-de1m/historical-learned-result.json",
                      "ordinal-lattice-de1m/historical-learned-result-k256.json"]},
    300: {"status": "source-bound", "notes": ["2026-09-08-cosine-lsh-locality.md",
                                                   "2026-09-08-locality-bakeoff-synthesis.md",
                                                   "2026-09-08-native-thq-ivf-bakeoff.md",
                                                   "2026-09-08-thq-aware-ivf.md",
                                                   "2026-09-09-rp-thq-validation.md"],
          "sources": ["cosine-lsh-locality-20260908/result.json", "thq-full-scan-v2/full-result.json",
                      "thq-full-scan-v2/native-bakeoff-result.json", "native-thq-ivf-bakeoff-v1/full-result.json",
                      "native-document-routing-bakeoff-v2/corrective-result.json"]},
}


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path: Path) -> bytes:
    value = path.read_bytes()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"compact source is not a JSON object: {path}")
    return value


def collect(artifact_root: Path, notes_root: Path) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    files: dict[str, bytes] = {}
    members: list[dict[str, object]] = []
    for pr, spec in MEMBERS.items():
        prefix = f"bundle/pr-{pr:03d}"
        notes: list[dict[str, str]] = []
        for index, name in enumerate(spec["notes"]):
            path = notes_root / name
            if not path.is_file():
                raise ValueError(f"PR #{pr}: missing note: {path}")
            payload = path.read_bytes()
            archived = f"{prefix}/note-{index:02d}-{path.name}"
            files[archived] = payload
            notes.append({"path": archived, "sha256": sha256(payload)})
        sources: list[dict[str, object]] = []
        for index, name in enumerate(spec["sources"]):
            path = artifact_root / name
            if not path.is_file():
                raise ValueError(f"PR #{pr}: missing compact source: {path}")
            payload = read_json(path)
            archived = f"{prefix}/source-{index:02d}-{path.name}"
            files[archived] = payload
            sources.append({"path": archived, "source": name, "sha256": sha256(payload), "size": len(payload)})
        result = {"schema_version": 1, "pr": pr, "status": spec["status"], "notes": notes,
                  "source_artifacts": sources}
        result_path = f"{prefix}/result.json"
        result_bytes = canonical(result)
        files[result_path] = result_bytes
        evidence = {"schema_version": 1, "pr": pr, "status": spec["status"],
                    "authoritative_replay": False, "result_sha256": sha256(result_bytes),
                    "source_count": len(sources), "note_count": len(notes)}
        evidence_path = f"{prefix}/evidence.json"
        evidence_bytes = canonical(evidence)
        files[evidence_path] = evidence_bytes
        members.append({"pr": pr, "status": spec["status"], "result": result_path,
                        "evidence": evidence_path, "result_sha256": sha256(result_bytes),
                        "evidence_sha256": sha256(evidence_bytes)})
    return files, members


def write_zip(output: Path, files: dict[str, bytes], manifest: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    contents = {**files, "bundle/evidence-manifest.json": canonical(manifest)}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, contents[name])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measured-head", required=True)
    args = parser.parse_args()
    files, members = collect(args.artifact_root, args.notes_root)
    entries = [{"path": name, "sha256": sha256(value), "size": len(value)} for name, value in sorted(files.items())]
    root = sha256(canonical(entries))
    manifest = {"schema_version": 1, "family": "neuroute_wave_293_300_evidence",
                "measured_head": args.measured_head, "scope": "PR #293--#300", "bundle_root_sha256": root,
                "members": members, "files": entries, "raw_large_artifacts_excluded": True}
    write_zip(args.output, files, manifest)
    print(json.dumps({"archive_sha256": sha256(args.output.read_bytes()), "bundle_root_sha256": root,
                      "members": len(members)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
