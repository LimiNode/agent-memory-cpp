#!/usr/bin/env python3
"""Audit the native full-corpus production-kernel control from raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def percentile(values: list[float], fraction: float) -> float:
    require(bool(values), "cannot summarize an empty timing series")
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(per_query: list[list[float]]) -> dict[str, float]:
    flat = [sample for samples in per_query for sample in samples]
    query_medians = [median(samples) for samples in per_query]
    return {
        "mean_ms": sum(flat) / len(flat),
        "p50_ms": percentile(flat, 0.50),
        "p95_ms": percentile(flat, 0.95),
        "p99_ms": percentile(flat, 0.99),
        "query_median_p50_ms": percentile(query_medians, 0.50),
        "query_median_p95_ms": percentile(query_medians, 0.95),
        "query_median_p99_ms": percentile(query_medians, 0.99),
    }


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(1e-4, abs(expected) * 2e-5)


def parse_inputs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        require(bool(separator) and bool(name) and bool(raw_path),
                f"invalid --input value: {value}")
        path = Path(raw_path)
        require(path.is_file(), f"input does not exist: {path}")
        require(name not in result, f"duplicate input name: {name}")
        result[name] = sha256(path)
    return result


def self_test() -> None:
    values = [[1.0, 3.0, 2.0], [10.0, 12.0, 11.0]]
    result = summarize(values)
    require(result["mean_ms"] == 6.5, "flat mean self-test differs")
    require(result["p50_ms"] == 6.5, "flat p50 self-test differs")
    require(result["query_median_p50_ms"] == 6.5,
            "query-median p50 self-test differs")
    require(close_enough(100.0, 100.001), "relative tolerance self-test differs")
    require(not close_enough(100.0, 100.1),
            "relative rejection self-test differs")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        print("audit-native-production-kernel-control self-test PASS")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--query-fixture", type=Path, required=True)
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--expected-queries", type=int, default=152)
    parser.add_argument("--expected-warmups", type=int, default=2)
    parser.add_argument("--expected-repeats", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.raw, args.summary, args.runner, args.binary,
                 args.query_fixture):
        require(path.is_file(), f"required artifact does not exist: {path}")

    rows = [json.loads(line) for line in args.raw.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    require(len(rows) == args.expected_queries, "raw query row count differs")
    require([row.get("query") for row in rows] == list(range(args.expected_queries)),
            "query IDs are not strict zero-based order")
    require(summary.get("queries") == args.expected_queries,
            "summary query count differs")
    require(summary.get("warmups") == args.expected_warmups,
            "summary warmup count differs")
    require(summary.get("repeats") == args.expected_repeats,
            "summary repeat count differs")

    stage_names = {
        "direct": ("prepare", "score", "topk", "total"),
        "cascade": ("prepare", "score", "topk", "rerank", "total"),
    }
    collected: dict[str, dict[str, list[list[float]]]] = {
        arm: {stage: [] for stage in stages}
        for arm, stages in stage_names.items()
    }
    max_absolute_error = 0.0
    max_relative_error = 0.0
    page_values = {"thq_scan": [], "rerank_codes": [],
                   "rerank_scales": [], "rerank_payload": []}

    for row in rows:
        direct = row.get("direct_top10")
        cascade = row.get("cascade_top10")
        coarse = row.get("cascade_thq_top128_ids")
        require(isinstance(direct, list) and len(direct) == 10,
                "direct top-10 shape differs")
        require(isinstance(cascade, list) and len(cascade) == 10,
                "cascade top-10 shape differs")
        require(isinstance(coarse, list) and len(coarse) == 128,
                "THQ top-128 shape differs")
        for name, ids in (("direct", direct), ("cascade", cascade),
                          ("coarse", coarse)):
            require(len(set(ids)) == len(ids), f"{name} IDs contain duplicates")
            require(all(isinstance(item, int) and 0 <= item < 1_000_000
                        for item in ids), f"{name} ID is outside the corpus")

        parity = row.get("parity", {})
        require(parity.get("thq_block32_vs_unrolled") is True,
                "THQ block32/unrolled parity differs")
        require(parity.get("int8_avx2_vs_scalar_top10") is True,
                "INT8 AVX2/scalar ordered top-10 parity differs")
        require(parity.get("direct_vs_cascade_top10") is True,
                "direct/cascade ordered top-10 parity differs")
        require(direct == cascade, "direct/cascade IDs differ despite parity flag")

        errors = row.get("int8_score_error", {})
        absolute = float(errors.get("max_absolute", math.nan))
        relative = float(errors.get("max_relative", math.nan))
        require(math.isfinite(absolute) and absolute >= 0.0,
                "INT8 absolute score error is invalid")
        require(math.isfinite(relative) and relative >= 0.0,
                "INT8 relative score error is invalid")
        max_absolute_error = max(max_absolute_error, absolute)
        max_relative_error = max(max_relative_error, relative)

        pages = row.get("page_proxy", {})
        for name in page_values:
            value = pages.get(name)
            require(isinstance(value, int) and value > 0,
                    f"page proxy {name} is invalid")
            page_values[name].append(value)
        require(pages["rerank_payload"] ==
                pages["rerank_codes"] + pages["rerank_scales"],
                "rerank page proxy omits a payload namespace")

        timings = row.get("timing_ms", {})
        for arm, stages in stage_names.items():
            arm_timings = timings.get(arm, {})
            for stage in stages:
                samples = arm_timings.get(stage)
                require(isinstance(samples, list) and
                        len(samples) == args.expected_repeats,
                        f"{arm}.{stage} timing shape differs")
                converted = [float(value) for value in samples]
                require(all(math.isfinite(value) and value >= 0.0
                            for value in converted),
                        f"{arm}.{stage} contains invalid timing")
                collected[arm][stage].append(converted)
            component_names = [stage for stage in stages if stage != "total"]
            for index in range(args.expected_repeats):
                component_sum = sum(float(arm_timings[stage][index])
                                    for stage in component_names)
                total = float(arm_timings["total"][index])
                require(abs(component_sum - total) <= 0.05,
                        f"{arm} stage timings do not close to total")

    recomputed = {
        arm: {stage: summarize(values) for stage, values in stages.items()}
        for arm, stages in collected.items()
    }
    reported_arms = summary.get("arms", {})
    for arm, stages in recomputed.items():
        for stage, metrics in stages.items():
            reported = reported_arms.get(arm, {}).get(stage, {})
            for metric, expected in metrics.items():
                require(metric in reported and
                        close_enough(float(reported[metric]), expected),
                        f"summary mismatch for {arm}.{stage}.{metric}")

    page_summary = {
        name: {
            "p50": percentile([float(value) for value in values], 0.50),
            "p95": percentile([float(value) for value in values], 0.95),
            "p99": percentile([float(value) for value in values], 0.99),
        }
        for name, values in page_values.items()
    }
    receipt: dict[str, Any] = {
        "schema_version": 2,
        "family": "native_full_corpus_production_kernel_control_audit_v2",
        "status": "PASS",
        "rows": len(rows),
        "warmups": args.expected_warmups,
        "repeats": args.expected_repeats,
        "parity": {
            "thq_block32_vs_unrolled": f"{len(rows)}/{len(rows)} ordered top-128 rows",
            "int8_avx2_vs_scalar": f"{len(rows)}/{len(rows)} ordered top-10 rows",
            "direct_vs_cascade": f"{len(rows)}/{len(rows)} ordered top-10 rows",
        },
        "int8_score_error": {
            "max_absolute": max_absolute_error,
            "max_relative": max_relative_error,
        },
        "latency_ms": recomputed,
        "page_proxy": page_summary,
        "artifact_sha256": {
            "raw_jsonl": sha256(args.raw),
            "summary_json": sha256(args.summary),
            "runner_source": sha256(args.runner),
            "audit_source": sha256(Path(__file__)),
            "runner_binary": sha256(args.binary),
            "query_fixture": sha256(args.query_fixture),
            **parse_inputs(args.input),
        },
        "interpretation": (
            "normalized in-memory kernel evidence only; no codec winner or "
            "MDBX serving claim"
        ),
        "production_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"audit-native-production-kernel-control: PASS ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-production-kernel-control: {error}")
