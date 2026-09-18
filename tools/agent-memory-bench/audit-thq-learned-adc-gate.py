#!/usr/bin/env python3
"""Fail-closed audit for the score-aware learned ADC gate."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_learned_adc_gate_v1", "learned ADC family differs")
    require(result.get("status") == "EXECUTED", "learned ADC status differs")
    require(result.get("runner_sha256") == sha(args.runner), "learned ADC runner binding differs")
    require(result.get("candidate_receipt_sha256") == sha(args.candidate_receipt), "candidate receipt binding differs")
    require(candidate.get("family") == "semantic_r4_fused_candidate_materialization_v1",
            "candidate receipt family differs")
    require(candidate.get("execution_status") == "EXECUTED", "candidate receipt is not executed")
    require(result.get("query_count") == 152, "canonical query count differs")
    require(result.get("train_query_count") == 120, "held-out split differs")
    arms = set(result.get("summaries", {}))
    require(arms == {"learned-adc-8B", "learned-adc-16B", "learned-adc-32B",
                     "learned-adc-8B+norm2", "learned-adc-16B+norm2", "learned-adc-32B+norm2"},
            "learned ADC arm set differs")
    rows = result.get("rows", [])
    require(len(rows) == 152 * 6, "learned ADC row cardinality differs")
    require(all(row.get("timing_scope") == "numpy_reference_direct_adc_quality_only" for row in rows),
            "learned ADC timing scope differs")
    require(all(len(row.get("top10_ids", [])) == 10 for row in rows), "learned ADC top10 cardinality differs")
    output = {"schema_version": 1, "family": "thq_learned_adc_gate_audit_v1", "status": "PASS",
              "result_sha256": sha(args.result), "runner_sha256": sha(args.runner),
              "candidate_receipt_sha256": sha(args.candidate_receipt),
              "checks": ["runner/result binding", "candidate provenance binding", "held-out split",
                         "three byte budgets", "row cardinality", "direct ADC quality scope"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ learned ADC evidence audit PASS")

if __name__ == "__main__":
    main()
