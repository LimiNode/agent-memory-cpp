#!/usr/bin/env python3
"""Fail-closed source-binding audit for the THQ FastScan v3 bakeoff."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


ARMS = (
    "byte_lut_scalar",
    "byte_lut_unrolled4",
    "pair_fp32_scalar",
    "pair_fp32_avx2_block32",
    "pair_u8_scalar",
    "pair_u8_prepacked_plane",
    "pair_u8_global_u16_plane",
    "pair_u8_global_u16_block32",
)
EXACT = (
    "byte_lut_scalar",
    "byte_lut_unrolled4",
    "pair_fp32_scalar",
    "pair_fp32_avx2_block32",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_timing(row: dict, arm: str) -> None:
    require(int(row.get("samples", 0)) > 0, f"missing timing samples: {arm}")
    values = [float(row.get(name, math.nan)) for name in (
        "workload_p50_ms", "workload_p95_ms", "workload_p99_ms",
        "total_p50_ms", "total_p95_ms", "total_p99_ms")]
    require(all(math.isfinite(value) and value > 0.0 for value in values),
            f"invalid timing value: {arm}")
    require(values[0] <= values[1] <= values[2], f"workload percentile order differs: {arm}")
    require(values[3] <= values[4] <= values[5], f"total percentile order differs: {arm}")
    require(values[3] >= values[0], f"query setup accounting differs: {arm}")


def self_test() -> None:
    row = {"samples": 3, "workload_p50_ms": 1.0, "workload_p95_ms": 2.0,
           "workload_p99_ms": 2.1, "total_p50_ms": 1.2,
           "total_p95_ms": 2.2, "total_p99_ms": 2.3}
    check_timing(row, "synthetic")
    print("THQ FastScan v3 audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--codes", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--candidate-raw", type=Path)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--qrel-ids", type=Path)
    parser.add_argument("--qrel-scores", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    base_inputs = {
        "result": args.result,
        "runner": args.runner,
        "thq4_codes": args.codes,
        "thq4_thresholds": args.thresholds,
        "queries": args.queries,
    }
    production_inputs = {
        "candidate_flat": args.candidate_flat,
        "candidate_raw": args.candidate_raw,
        "documents": args.documents,
        "qrel_ids": args.qrel_ids,
        "qrel_scores": args.qrel_scores,
    }
    require(args.output is not None, "--output is required")
    require(all(path is not None and path.is_file() for path in base_inputs.values()),
            "result, runner, codes, thresholds and queries are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_fastscan_v3_benchmark", "wrong result family")
    require(result.get("status") == "EXECUTED", "result was not executed")
    mode = result.get("mode")
    require(mode in ("dense", "production"), "audit requires a single-workload result")
    inputs = dict(base_inputs)
    if mode == "production":
        require(all(path is not None and path.is_file() for path in production_inputs.values()),
                "production audit requires candidate, document and qrel sources")
        inputs.update(production_inputs)
    require(int(result.get("documents", 0)) == 1_000_000, "document cardinality differs")
    expected_queries = 152 if mode == "production" else 8
    expected_repeats = 20 if mode == "production" else 5
    require(int(result.get("queries", 0)) == expected_queries,
            f"{mode} replay query cardinality differs")
    require(int(result.get("repeats", 0)) >= expected_repeats and
            int(result.get("warmups", 0)) >= 1,
            "timing protocol is not hardened")
    require(result.get("avx2_compiled") is True, "AVX2 arms were not compiled")
    require(result.get("arm_order_randomized") is True, "arm order was not randomized")
    require(math.isfinite(float(result.get("checksum", math.nan))) and
            float(result["checksum"]) != 0.0, "invalid observable checksum")
    rows = result.get(mode, {})
    require(set(rows) == set(ARMS), "arm manifest differs")
    for arm in ARMS:
        row = rows[arm]
        check_timing(row, arm)
        overlap = float(row.get("mean_top128_overlap", math.nan))
        require(math.isfinite(overlap) and 0.0 <= overlap <= 1.0,
                f"invalid top128 overlap: {arm}")
        if mode == "production":
            final_overlap = float(row.get("mean_final_fp32_top10_overlap", math.nan))
            ndcg = float(row.get("mean_qrels_ndcg10", math.nan))
            require(math.isfinite(final_overlap) and 0.0 <= final_overlap <= 1.0,
                    f"invalid final top10 overlap: {arm}")
            require(math.isfinite(ndcg) and 0.0 <= ndcg <= 1.0, f"invalid nDCG: {arm}")
    for arm in EXACT:
        row = rows[arm]
        require(float(row["mean_top128_overlap"]) == 1.0, f"exact top128 parity differs: {arm}")
        error = float(row.get("max_abs_exact_score_error", math.inf))
        require(math.isfinite(error) and error <= 1.0e-5, f"exact score tolerance differs: {arm}")
    require(rows["pair_u8_scalar"]["mean_top128_overlap"] ==
            rows["pair_u8_prepacked_plane"]["mean_top128_overlap"],
            "local-u8 implementation quality differs")
    require(rows["pair_u8_global_u16_plane"]["mean_top128_overlap"] ==
            rows["pair_u8_global_u16_block32"]["mean_top128_overlap"],
            "global-u8 layout quality differs")
    audit = {
        "schema_version": 1,
        "family": "thq_fastscan_v3_audit",
        "status": "PASS",
        "source_binding": True,
        "mode": mode,
        "result_sha256": sha256(args.result),
        "runner_sha256": sha256(args.runner),
        "input_hashes": {name: sha256(path) for name, path in inputs.items()
                         if name not in ("result", "runner")},
        "query_count": expected_queries,
        "checks": [
            "source and runner SHA binding",
            "canonical cardinality and timing protocol",
            "complete arm manifest",
            "exact score/top128 tolerances",
            "local-u8 implementation quality parity",
            "global-u8 plane/block quality parity",
            "final-rerank metric ranges",
            "observable checksum",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ FastScan v3 source-binding audit: PASS")


if __name__ == "__main__":
    main()
