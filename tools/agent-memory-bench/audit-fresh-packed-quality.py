#!/usr/bin/env python3
"""Fail-closed aggregate audit for a fresh packed-quality receipt."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

Q = 305
CODECS = ("int8", "lsq32", "lsq48", "tq1", "tq1-pq8", "plsq8x6x8", "rslm1")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def check_stage(stage: dict, expected: float) -> dict:
    rows = stage.get("per_query")
    if not isinstance(rows, list) or len(rows) != Q:
        raise ValueError("per-query coverage differs")
    nd = np.asarray([float(r["ndcg_at_10"]) for r in rows])
    mr = np.asarray([float(r["mrr"]) for r in rows])
    mr10 = np.asarray([float(r["mrr_at_10"]) for r in rows])
    if not np.all(np.isfinite(nd)) or not np.all(np.isfinite(mr)) or not np.all(np.isfinite(mr10)):
        raise ValueError("non-finite aggregate input")
    for row in rows:
        rank = row.get("first_relevant_rank")
        expected_mrr = 0.0 if rank is None else 1.0 / int(rank)
        if rank is not None and abs(float(row["mrr"]) - expected_mrr) > 1e-12:
            raise ValueError("MRR rank contract differs")
    if abs(float(stage["mean_ndcg_at_10"]) - float(nd.mean())) > 1e-12:
        raise ValueError("nDCG aggregate differs")
    if abs(float(stage["mean_mrr"]) - float(mr.mean())) > 1e-12:
        raise ValueError("full-rank MRR aggregate differs")
    if abs(float(stage["mean_mrr_at_10"]) - float(mr10.mean())) > 1e-12:
        raise ValueError("MRR@10 aggregate differs")
    return {"mean_ndcg_at_10": float(nd.mean()), "mean_mrr": float(mr.mean()), "mean_mrr_at_10": float(mr10.mean()), "p05_ndcg_at_10": float(np.sort(nd)[14]), "worst_ndcg_at_10": float(nd.min())}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--target-commit", required=True)
    ap.add_argument("--bundle-sha256", required=True)
    args = ap.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("query_count") != Q or result.get("candidate_count") != 5000:
        raise ValueError("fresh matrix shape differs")
    if set(result.get("stages", {})) != {"prototype_ivf", "modern_r4"}:
        raise ValueError("serving-mode coverage differs")
    compact = {"schema_version": 1, "family": "fresh_packed_quality_decomposition_receipt_v1", "status": "PASS", "target_commit": args.target_commit, "result_sha256": sha256(args.result), "bundle_sha256": args.bundle_sha256, "query_count": Q, "codec_count": len(CODECS), "metric_contract": {"ndcg": "nDCG@10", "mrr": "full ranked-stage MRR", "mrr_at_10": "diagnostic only", "tie_policy": "score-desc-or-THQ-distance-asc then numeric-id-asc"}, "source": result.get("source"), "payloads": result.get("payloads"), "stages": {}}
    check_stage(result["exact_oracle"], 0.0)
    compact["exact_oracle"] = {k: result["exact_oracle"][k] for k in ("mean_ndcg_at_10", "mean_mrr", "mean_mrr_at_10", "p05_ndcg_at_10", "worst_ndcg_at_10")}
    for mode, value in result["stages"].items():
        compact["stages"][mode] = {"route_exact_fp32": check_stage(value["route_exact_fp32"], 0.0), "thq_top128_exact_fp32": check_stage(value["thq_top128_exact_fp32"], 0.0), "packed": {codec: check_stage(value["packed"][codec], 0.0) for codec in CODECS}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "result_sha256": compact["result_sha256"], "output": str(args.output)}, sort_keys=True))

if __name__ == "__main__":
    main()
