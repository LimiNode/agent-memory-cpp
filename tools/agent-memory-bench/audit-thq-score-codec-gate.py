#!/usr/bin/env python3
"""Fail-closed audit for the THQ score-only codec gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


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
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_score_only_codec_gate_v1", "score gate family differs")
    require(result.get("status") == "EXECUTED", "score gate status differs")
    require(result.get("runner_sha256") == sha(args.runner), "score runner binding differs")
    require(receipt.get("family") == "semantic_r4_fused_candidate_materialization_v1",
            "candidate receipt family differs")
    require(result.get("candidate_receipt_sha256") == sha(args.candidate_receipt),
            "score candidate receipt binding differs")
    require(result.get("candidate_provenance", {}).get("receipt_sha256") == sha(args.candidate_receipt),
            "score candidate provenance differs")
    rows = result["rows"]
    query_count = int(result["query_count"])
    arms = set(result["summaries"])
    expected = {
        "candidate-fp32", "direct-int8", "thq4-centroid-direct", "rslm3-direct-score",
        "thq-sdc-1bit-direct-score", "thq-sdc-2bit-direct-score", "thq-sdc-3bit-direct-score",
        "score-basis-8x8", "score-basis-16x8", "score-basis-32x8", "score-basis-64x8"}
    require(arms == expected, "score gate arm set differs")
    require(len(rows) == query_count * len(expected), "score gate row cardinality differs")
    by_query = {(int(row["query"]), row["arm"]): row for row in rows}
    require(len(by_query) == len(rows), "duplicate score gate row")
    for arm in expected:
        selected = [by_query[(query, arm)] for query in range(query_count)]
        for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap"):
            values = np.asarray([row[metric] for row in selected], dtype=np.float64)
            expected_stats = {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
                              "min": float(np.min(values))}
            for key, value in expected_stats.items():
                require(abs(float(result["summaries"][arm][metric][key]) - value) < 1e-12,
                        f"summary differs: {arm}/{metric}/{key}")
        for baseline in ("candidate-fp32", "direct-int8"):
            deltas = np.asarray([by_query[(query, arm)]["qrels_ndcg10"] -
                                 by_query[(query, baseline)]["qrels_ndcg10"]
                                 for query in range(query_count)], dtype=np.float64)
            observed = result["summaries"][arm]["qrels_ndcg10_paired"][baseline]
            rng = np.random.default_rng(20260918)
            bootstrap = deltas[rng.integers(0, len(deltas), size=(2000, len(deltas)))].mean(axis=1)
            expected_pair = {
                "mean_delta": float(np.mean(deltas)), "p05_delta": float(np.quantile(deltas, 0.05)),
                "min_delta": float(np.min(deltas)), "worst_query_loss": float(np.min(deltas)),
                "bootstrap_ci95": [float(np.quantile(bootstrap, 0.025)),
                                    float(np.quantile(bootstrap, 0.975))]}
            for key, value in expected_pair.items():
                if isinstance(value, list):
                    require(np.allclose(observed[key], value, atol=1e-12), f"paired summary differs: {arm}/{baseline}")
                else:
                    require(abs(float(observed[key]) - value) < 1e-12, f"paired summary differs: {arm}/{baseline}")
    parity = result["rslm3_direct_reconstruction_parity"]
    require(parity["query_count"] == query_count and parity["top10_equal_queries"] == query_count,
            "RSLM direct/reconstructive parity differs")
    require(float(parity["max_abs_score_error"]) <= 1e-5, "RSLM direct score parity error differs")
    output = {"schema_version": 1, "family": "thq_score_only_codec_gate_audit_v1", "status": "PASS",
              "result_sha256": sha(args.result), "runner_sha256": sha(args.runner),
              "candidate_receipt_sha256": sha(args.candidate_receipt),
              "checks": ["input provenance", "arm/query cardinality", "independent aggregates",
                          "paired qrels bootstrap", "RSLM direct-score parity"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ score-only codec evidence audit PASS")


if __name__ == "__main__":
    main()
