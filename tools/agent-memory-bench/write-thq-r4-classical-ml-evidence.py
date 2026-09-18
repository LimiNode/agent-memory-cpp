#!/usr/bin/env python3
"""Write compact, committed evidence and receipts from external raw reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


INPUT_HASH_FIELDS = (
    "documents_sha256",
    "training_sha256",
    "thq4_codes_sha256",
    "thq4_thresholds_sha256",
    "thresholds_sha256",
    "int8_codes_sha256",
    "int8_scales_sha256",
    "queries_sha256",
    "qrel_ids_sha256",
    "qrel_scores_sha256",
    "teacher_ids_sha256",
    "candidate_flat_sha256",
    "candidate_raw_sha256",
    "candidate_receipt_sha256",
)


def compact_result(raw_path: Path, selected: tuple[str, ...]) -> tuple[dict, Path]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    compact = {
        "schema_version": 1,
        "family": raw["family"],
        "status": raw["status"],
        "evidence_status": raw.get("evidence_status"),
        "raw_sha256": sha(raw_path),
        "runner_sha256": raw.get("runner_sha256"),
        "query_count": raw.get("query_count"),
        "train_query_count": raw.get("train_query_count"),
        "heldout_query_count": raw.get("heldout_query_count"),
        "heldout_count": raw.get("heldout_count"),
        "documents": raw.get("documents"),
        "training_count": raw.get("training_count"),
        "summaries": raw.get("summaries"),
        "model_hashes": raw.get("model_hashes"),
        "results": raw.get("results"),
        "candidate_provenance": raw.get("candidate_provenance"),
        "candidate_receipt_sha256": raw.get("candidate_receipt_sha256"),
        "limitations": raw.get("limitations"),
    }
    compact["input_hashes"] = {
        field: raw[field] for field in INPUT_HASH_FIELDS if field in raw
    }
    # Keep the selected arm list explicit in the compact evidence contract.
    if "summaries" in compact and selected:
        compact["selected_arms"] = [name for name in selected if name in compact["summaries"]]
    return compact, raw_path


def validate_audit_bindings(audit_value: dict, classical: Path, ml_sanity: Path,
                            teacher: Path, candidate_receipt: Path,
                            audit_runner: Path) -> dict[str, str]:
    expected = {
        "classical_sha256": sha(classical),
        "ml_sanity_sha256": sha(ml_sanity),
        "teacher_sha256": sha(teacher),
        "candidate_receipt_sha256": sha(candidate_receipt),
        "runner_sha256": sha(audit_runner),
    }
    for field, value in expected.items():
        if audit_value.get(field) != value:
            raise RuntimeError(f"audit binding differs: {field}")
    return expected


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        paths = [root / name for name in ("classical", "ml", "teacher", "candidate", "audit-runner")]
        for index, path in enumerate(paths):
            path.write_bytes(f"fixture-{index}".encode("ascii"))
        audit = {field: sha(path) for field, path in zip(
            ("classical_sha256", "ml_sanity_sha256", "teacher_sha256",
             "candidate_receipt_sha256", "runner_sha256"), paths)}
        validate_audit_bindings(audit, *paths)
        paths[0].write_bytes(b"changed")
        try:
            validate_audit_bindings(audit, *paths)
        except RuntimeError as error:
            if str(error) != "audit binding differs: classical_sha256":
                raise
        else:
            raise RuntimeError("stale audit self-test did not fail closed")
    print("write-thq-r4-classical-ml-evidence self-test PASS")


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--classical", type=Path, required=True)
    parser.add_argument("--ml-sanity", type=Path, required=True)
    parser.add_argument("--teacher", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--audit-runner", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    if candidate_receipt.get("family") != "semantic_r4_fused_candidate_materialization_v1":
        raise RuntimeError("candidate receipt family differs")
    if candidate_receipt.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not executed")

    specs = [
        (args.classical, "2026-09-18-thq-r4-classical-gate",
         ("candidate-fp32", "thq4-fp32", "direct-int8", "rslm2", "rslm3", "rslm4",
          "hierarchical-3bit", "pq32x8")),
        (args.ml_sanity, "2026-09-18-thq-ml-sanity-gate", ()),
        (args.teacher, "2026-09-18-thq-r4-teacher-diagnostics", ()),
    ]
    receipts = []
    compact_values = {}
    for raw_path, stem, selected in specs:
        compact, _ = compact_result(raw_path, selected)
        compact_values[stem] = compact
        compact_path = args.output_dir / f"{stem}.compact.json"
        receipt_path = args.output_dir / f"{stem}.receipt.json"
        write_json(compact_path, compact)
        receipt = {
            "schema_version": 1,
            "family": f"{compact['family']}_evidence_receipt_v1",
            "status": "PASS",
            "raw_sha256": compact["raw_sha256"],
            "compact_sha256": sha(compact_path),
            "runner_sha256": compact.get("runner_sha256"),
            "candidate_receipt_sha256": compact.get("candidate_receipt_sha256"),
            "input_hashes": compact["input_hashes"],
        }
        write_json(receipt_path, receipt)
        receipts.append(receipt_path)

    audit_value = json.loads(args.audit.read_text(encoding="utf-8"))
    if audit_value.get("status") != "PASS":
        raise RuntimeError("cannot write evidence for a failed audit")
    expected_audit_bindings = validate_audit_bindings(
        audit_value, args.classical, args.ml_sanity, args.teacher,
        args.candidate_receipt, args.audit_runner)
    audit_receipt = {
        "schema_version": 1,
        "family": "thq_r4_classical_ml_gate_audit_receipt_v1",
        "status": audit_value.get("status"),
        "audit_raw_sha256": sha(args.audit),
        "audit_runner_sha256": expected_audit_bindings["runner_sha256"],
        "candidate_receipt_sha256": sha(args.candidate_receipt),
        "classical_raw_sha256": sha(args.classical),
        "ml_sanity_raw_sha256": sha(args.ml_sanity),
        "teacher_raw_sha256": sha(args.teacher),
        "input_hashes": {
            stem: compact["input_hashes"]
            for stem, compact in compact_values.items()
        },
        "compact_receipts": {
            path.name: sha(path) for path in receipts
        },
    }
    write_json(args.output_dir / "2026-09-18-thq-r4-classical-ml-gates.audit.receipt.json",
               audit_receipt)


if __name__ == "__main__":
    main()
