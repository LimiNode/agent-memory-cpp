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
    parser.add_argument("--score-baseline", type=Path)
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
        "train_query_count", "model_hashes", "summaries", "summaries_by_scope", "limitations")}
    compact["input_hashes"] = {key: result[key] for key in (
        "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256",
        "qrel_scores_sha256", "teacher_ids_sha256", "candidate_flat_sha256",
        "candidate_raw_sha256", "candidate_receipt_sha256")}
    if args.score_baseline:
        baseline = json.loads(args.score_baseline.read_text(encoding="utf-8"))
        by_query = {(row["query"], row["arm"]): row for row in baseline["rows"]
                    if row.get("scope", "full-shell") == "full-shell"}
        paired = {}
        rng = __import__("numpy").random.default_rng(20260918)
        for arm in result["summaries"]:
            heldout = [row for row in result["rows"]
                       if row["arm"] == arm and row["scope"] == "thq4-top128"
                       and row["query"] >= result["train_query_count"]]
            paired[arm] = {}
            for control in ("candidate-fp32", "direct-int8", "rslm3-direct-score"):
                delta = __import__("numpy").asarray([
                    row["qrels_ndcg10"] - by_query[(row["query"], control)]["qrels_ndcg10"]
                    for row in heldout], dtype=float)
                bootstrap = delta[rng.integers(0, len(delta), size=(5000, len(delta)))].mean(axis=1)
                paired[arm][control] = {
                    "mean_delta": float(delta.mean()),
                    "bootstrap_ci95": [float(__import__("numpy").quantile(bootstrap, .025)),
                                        float(__import__("numpy").quantile(bootstrap, .975))],
                    "worst_query_delta": float(delta.min())}
        compact["heldout_paired_qrels_ndcg10"] = paired
        compact["paired_scope"] = "thq4-top128"
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
