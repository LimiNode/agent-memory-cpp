#!/usr/bin/env python3
"""Write compact committed evidence for the score-only codec gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    if audit.get("status") != "PASS":
        raise RuntimeError("cannot write score evidence for a failed audit")
    if result.get("runner_sha256") != sha(args.runner):
        raise RuntimeError("score runner binding differs")
    compact = {
        "schema_version": 1,
        "family": result["family"],
        "status": result["status"],
        "evidence_status": result["evidence_status"],
        "raw_sha256": sha(args.result),
        "runner_sha256": result["runner_sha256"],
        "candidate_receipt_sha256": result["candidate_receipt_sha256"],
        "candidate_provenance": result["candidate_provenance"],
        "query_count": result["query_count"],
        "documents": result["documents"],
        "training_count": result["training_count"],
        "model_hashes": result["model_hashes"],
        "score_basis_explained_fraction": result["score_basis_explained_fraction"],
        "rslm3_direct_reconstruction_parity": result["rslm3_direct_reconstruction_parity"],
        "summaries": result["summaries"],
        "limitations": result["limitations"],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    compact_path = args.output_dir / "2026-09-18-thq-score-codec-gate.compact.json"
    receipt_path = args.output_dir / "2026-09-18-thq-score-codec-gate.receipt.json"
    write(compact_path, compact)
    write(receipt_path, {
        "schema_version": 1,
        "family": "thq_score_only_codec_gate_evidence_receipt_v1",
        "status": "PASS",
        "raw_sha256": sha(args.result),
        "compact_sha256": sha(compact_path),
        "runner_sha256": sha(args.runner),
        "candidate_receipt_sha256": sha(args.candidate_receipt),
        "audit_sha256": sha(args.audit),
    })
    audit_receipt_path = args.output_dir / "2026-09-18-thq-score-codec-gate.audit.receipt.json"
    write(audit_receipt_path, {
        "schema_version": 1,
        "family": "thq_score_only_codec_gate_audit_receipt_v1",
        "status": "PASS",
        "audit_sha256": sha(args.audit),
        "result_sha256": sha(args.result),
        "runner_sha256": sha(args.runner),
        "candidate_receipt_sha256": sha(args.candidate_receipt),
        "compact_sha256": sha(compact_path),
    })


if __name__ == "__main__":
    main()
