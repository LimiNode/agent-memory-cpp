#!/usr/bin/env python3
"""Fail-closed provenance audit for the THQ FastScan hardening result."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def self_test() -> None:
    value = {"family": "thq_fastscan_kernel_benchmark_v1", "repeats": 3,
             "warmups": 1, "pair_score_mismatches_gt_1e5": 0}
    require(value["repeats"] >= 3 and value["warmups"] >= 1, "timing contract")
    require(value["pair_score_mismatches_gt_1e5"] == 0, "parity contract")
    print("THQ FastScan audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--codes", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--queries", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.result, args.runner, args.codes, args.thresholds, args.queries)
    if any(value is None for value in required):
        parser.error("--result, --runner, --codes, --thresholds and --queries are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_fastscan_kernel_benchmark_v1", "wrong result family")
    require(result.get("status") == "EXECUTED_BOUNDED", "unexpected result status")
    require(result.get("runner_sha256") == sha256(args.runner), "runner SHA mismatch")
    require(result.get("source_hashes", {}).get("thq4_ordinal") == sha256(args.codes), "code SHA mismatch")
    require(result.get("source_hashes", {}).get("thq4_thresholds") == sha256(args.thresholds), "threshold SHA mismatch")
    require(result.get("source_hashes", {}).get("queries") == sha256(args.queries), "query SHA mismatch")
    require(int(result.get("documents", 0)) == 1_000_000, "document count mismatch")
    require(int(result.get("repeats", 0)) >= 3 and int(result.get("warmups", 0)) >= 1,
            "timing repetitions are not hardened")
    require(int(result.get("pair_score_mismatches_gt_1e5", -1)) == 0, "pair parity mismatch")
    require(int(result.get("byte_lut_mismatches_gt_1e5", -1)) == 0, "byte LUT parity mismatch")
    require(float(result.get("avx2_packed96_max_abs_error", math.inf)) == 0.0,
            "packed AVX2 parity mismatch")
    for name in ("coordinate_fp32", "byte_lut_fp32", "pair_lut_u8_avx2_packed96"):
        row = result.get("kernels", {}).get(name, {})
        require(float(row.get("p50_ms", math.inf)) > 0.0 and float(row.get("p95_ms", math.inf)) >= float(row["p50_ms"]),
                f"invalid timing row: {name}")
    print(json.dumps({"status": "PASS", "source_replay": True,
                      "checks": ["source hashes", "runner hash", "timing protocol",
                                 "pair/byte parity", "packed AVX2 parity"]}, indent=2))


if __name__ == "__main__":
    main()
