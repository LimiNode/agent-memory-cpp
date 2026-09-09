#!/usr/bin/env python3
"""Build a deterministic compact Evidence archive for NeuRoute PRs #280-#292.

The archive deliberately contains compact reports and provenance receipts only.
Large vector stores, model checkpoints, and generated databases stay outside Git
and the release asset.  Members whose original PR did not ship an independent
writer are marked ``source-bound`` or ``protocol-only`` rather than being
presented as a replay that never happened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


NOTE_FILES = {
    280: "2026-09-03-neuroute-bitwise-address-selector.md",
    281: "2026-09-03-neuroute-address-capacity-frontier.md",
    282: "2026-09-03-neuroute-prototype-binary-metric.md",
    283: "2026-09-03-neuroute-prototype-binary-neural.md",
    284: "2026-09-03-neuroute-prototype-teacher-hard-negatives.md",
    285: "2026-09-04-neuroute-asymmetric-prototype-map.md",
    286: "2026-09-04-neuroute-listwise-utility-objective.md",
    287: "2026-09-04-neuroute-training-coverage-audit.md",
    288: "2026-09-04-binary-code-family-matrix.md",
    289: "2026-09-04-binary-code-family-matrix.md",
    290: "2026-09-04-binary-code-family-matrix.md",
    291: "2026-09-04-neuroute-final-rerank-code-family.md",
    292: "2026-09-04-neuroute-document-cascade-code-family.md",
}

# Paths are relative to --artifact-root.  The first two entries are existing
# validated receipts; the remaining entries are compact raw reports retained
# by the local research workspace.
ARTIFACTS = {
    280: [
        "batch-neuroute-replication-topology/tmp/neuroute-bitwise-address-selector/evidence.json",
        "batch-neuroute-replication-topology/tmp/neuroute-bitwise-address-selector/result.json",
    ],
    281: [
        "batch-neuroute-replication-topology/tmp/neuroute-address-capacity-frontier/evidence.json",
        "batch-neuroute-replication-topology/tmp/neuroute-address-capacity-frontier/result.json",
    ],
    282: [
        "neuroute-prototype-binary-metric-seed1.json",
        "neuroute-prototype-binary-metric-seed2.json",
        "neuroute-prototype-binary-metric-seed3.json",
    ],
    283: [],
    284: [
        "work-neuroute-prototype-binary-neural-hardneg/tmp/real-neural-frontier-8141.json",
        "work-neuroute-prototype-binary-neural-hardneg/tmp/real-prototype-source-8141.json",
        "work-neuroute-prototype-binary-neural-hardneg/tmp/real-teacher-8141-deterministic-rebuild.json",
    ],
    285: [
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-best-128.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-cascade-1024.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-cascade-2048.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-cascade-4096.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-cascade-8192.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-results/seed-285.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-results/seed-286.json",
        "work-neuroute-asymmetric-prototype-map/tmp/asymmetric-results/seed-287.json",
    ],
    286: [
        "work-neuroute-address-utility-objective/tmp/listwise-frontier.json",
        "work-neuroute-address-utility-objective/tmp/listwise-128-rerun.json",
        "work-neuroute-address-utility-objective/tmp/listwise-cascade-1024.json",
        "work-neuroute-address-utility-objective/tmp/listwise-cascade-2048.json",
        "work-neuroute-address-utility-objective/tmp/listwise-cascade-4096.json",
        "work-neuroute-address-utility-objective/tmp/listwise-cascade-8192.json",
    ],
    287: [
        "work-neuroute-training-audit-fix/tmp/coverage-audit.json",
        "work-neuroute-training-audit-fix/tmp/listwise-coverage-audit.json",
        "work-neuroute-training-audit-fix/tmp/projection-frontier.json",
        "work-neuroute-training-audit-fix/tmp/projection-cascade-1024.json",
        "work-neuroute-training-audit-fix/tmp/projection-cascade-2048.json",
        "work-neuroute-training-audit-fix/tmp/projection-cascade-4096.json",
        "work-neuroute-training-audit-fix/tmp/projection-cascade-8192.json",
    ],
    288: [],
    289: [
        "binary-reference-full-ru-corrected-v2.json",
        "binary-reference-k8-cascade-corrected-v2.json",
        "binary-reference-k8-prototypes-wide.json",
    ],
    290: [
        "residual-ivf-full-cascade-pareto-v4.json",
        "residual-ivf-itq-nlist4096-v3.json",
        "residual-ivf-rabitq-bbq-208-nlist4096-v4.json",
        "residual-ivf-scalar-nlist4096-v2.json",
        "residual-ivf-pq-opq-nlist4096-v2.json",
    ],
    291: ["final-rerank-code-family-v2.json"],
    292: ["document-cascade-code-family-v6.json"],
}

STATUS = {
    280: "validated-existing",
    281: "validated-existing",
    282: "source-bound",
    283: "protocol-only",
    284: "source-bound",
    285: "source-bound-historical",
    286: "source-bound-superseded",
    287: "source-bound-corrective",
    288: "derived-note",
    289: "source-bound-corrective",
    290: "source-bound-corrective",
    291: "source-bound",
    292: "source-bound",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def collect(artifact_root: Path, notes_root: Path) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    files: dict[str, bytes] = {}
    members: list[dict[str, object]] = []
    for pr in range(280, 293):
        note_path = notes_root / NOTE_FILES[pr]
        if not note_path.is_file():
            raise ValueError(f"PR #{pr}: missing note {note_path}")
        source_paths = [artifact_root / relative for relative in ARTIFACTS[pr]]
        missing = [str(path) for path in source_paths if not path.is_file()]
        if missing:
            raise ValueError(f"PR #{pr}: missing compact source artifacts: {missing}")
        if STATUS[pr] == "validated-existing":
            evidence_path = next(path for path in source_paths if path.name == "evidence.json")
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            result_path = next(path for path in source_paths if path.name == "result.json")
            if evidence.get("result_sha256") != sha256(result_path):
                raise ValueError(f"PR #{pr}: existing evidence/result SHA mismatch")

        prefix = f"bundle/pr-{pr:03d}"
        source_entries = []
        for index, path in enumerate(source_paths):
            source_name = f"source-{index:02d}-{path.name}"
            files[f"{prefix}/{source_name}"] = path.read_bytes()
            source_entries.append({
                "path": f"{prefix}/{source_name}",
                "source": ARTIFACTS[pr][index],
                "sha256": sha256(path),
                "size": path.stat().st_size,
            })
        result = {
            "schema_version": 1,
            "pr": pr,
            "status": STATUS[pr],
            "source_artifacts": source_entries,
            "note_sha256": sha256(note_path),
        }
        result_bytes = canonical(result)
        result_path = f"{prefix}/result.json"
        files[result_path] = result_bytes
        evidence = {
            "schema_version": 1,
            "pr": pr,
            "status": STATUS[pr],
            "authoritative_replay": STATUS[pr] == "validated-existing",
            "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            "note_sha256": sha256(note_path),
            "source_count": len(source_entries),
        }
        evidence_path = f"{prefix}/evidence.json"
        files[evidence_path] = canonical(evidence)
        note_member = f"{prefix}/{note_path.name}"
        files[note_member] = note_path.read_bytes()
        members.append({
            "pr": pr,
            "status": STATUS[pr],
            "result": result_path,
            "evidence": evidence_path,
            "note": note_member,
            "result_sha256": evidence["result_sha256"],
            "evidence_sha256": hashlib.sha256(files[evidence_path]).hexdigest(),
            "source_count": len(source_entries),
        })
    return files, members


def write_archive(output: Path, files: dict[str, bytes], manifest: dict[str, object]) -> None:
    all_files = {**files, "bundle/evidence-manifest.json": canonical(manifest)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(all_files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, all_files[name])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measured-head", required=True)
    args = parser.parse_args()
    files, members = collect(args.artifact_root, args.notes_root)
    entries = [{"path": name, "sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
               for name, value in sorted(files.items())]
    root = hashlib.sha256(canonical(entries)).hexdigest()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_binary_reference_wave_280_292_evidence",
        "measured_head": args.measured_head,
        "scope": "PR #280--#292",
        "bundle_root_sha256": root,
        "members": members,
        "files": entries,
        "raw_large_artifacts_excluded": True,
    }
    write_archive(args.output, files, manifest)
    print(json.dumps({"archive_sha256": sha256(args.output),
                      "bundle_root_sha256": root, "members": len(members)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
