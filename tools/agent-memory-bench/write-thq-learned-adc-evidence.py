#!/usr/bin/env python3
"""Write compact SHA-bound evidence for the learned ADC gate."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

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
        raise RuntimeError("cannot write learned ADC evidence for a failed audit")
    if result.get("runner_sha256") != sha(args.runner):
        raise RuntimeError("learned ADC runner binding differs")
    compact = {key: result[key] for key in (
        "schema_version", "family", "status", "evidence_status", "runner_sha256",
        "candidate_receipt_sha256", "documents", "training_count", "query_count",
        "train_query_count", "model_hashes", "summaries", "limitations")}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    compact_path = args.output_dir / "2026-09-18-thq-learned-adc-gate.compact.json"
    receipt_path = args.output_dir / "2026-09-18-thq-learned-adc-gate.receipt.json"
    compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.write_text(json.dumps({
        "schema_version": 1,
        "family": "thq_learned_adc_gate_evidence_receipt_v1",
        "status": "PASS",
        "raw_sha256": sha(args.result),
        "compact_sha256": sha(compact_path),
        "audit_sha256": sha(args.audit),
        "runner_sha256": sha(args.runner),
        "candidate_receipt_sha256": sha(args.candidate_receipt)},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ learned ADC compact evidence written")

if __name__ == "__main__":
    main()
