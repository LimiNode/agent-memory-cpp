#!/usr/bin/env python3
"""Audit the native full-corpus production-kernel control from raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shlex
import subprocess
import tempfile
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


def expected_manifest_hashes(path: Path) -> dict[str, str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    freeze = manifest.get("freeze", {})
    thq = freeze.get("thq", {})
    historical = freeze.get("historical_control", {})
    for name, value in {
        "thq": thq.get("codes_sha256"),
        "thq_thresholds": thq.get("thresholds_sha256"),
        "query_fixture": historical.get("queries_sha256"),
    }.items():
        if isinstance(value, str):
            result[name] = value
    return result


def validate_expected_manifest(path: Path, inputs: dict[str, str]) -> None:
    expected = expected_manifest_hashes(path)
    require(expected, "expected manifest has no frozen input hashes")
    aliases = {
        "thq": ("thq", "thq4_codes", "thq_codes"),
        "thq_thresholds": ("thq_thresholds", "thresholds"),
        "query_fixture": ("query_fixture", "queries", "query"),
    }
    for name, digest in expected.items():
        candidates = aliases.get(name, (name,))
        matched = next((candidate for candidate in candidates if candidate in inputs), None)
        require(matched is not None, f"expected manifest input is missing: {name}")
        require(inputs[matched] == digest,
                f"{matched} does not match the frozen artifact manifest")


def load_environment(path: Path | None) -> dict[str, Any]:
    if path is not None:
        require(path.is_file(), f"environment manifest does not exist: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(value, dict), "environment manifest must be an object")
        return value
    return {
        "cpu": platform.processor() or "unknown",
        "physical_cores": None,
        "logical_processors": os.cpu_count(),
        "compiler": "unknown",
        "compiler_version": "unknown",
        "language": "C++17",
        "optimization": "unknown",
        "simd_flags": [],
        "avx2": None,
        "threads": 1,
        "affinity": "unknown",
        "numa": "unknown",
        "power_policy": "uncontrolled",
    }


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
    source = Path(__file__).resolve()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw = root / "raw.jsonl"
        summary = root / "summary.json"
        runner = root / "runner.py"
        binary = root / "runner.exe"
        query = root / "queries.f32"
        output = root / "receipt.json"
        row = {
            "query": 0,
            "direct_top10": list(range(10)),
            "cascade_top10": list(range(10)),
            "cascade_thq_top128_ids": list(range(128)),
            "parity": {"thq_block32_vs_unrolled": True,
                        "int8_avx2_vs_scalar_top10": True,
                        "direct_vs_cascade_top10": True},
            "int8_score_error": {"max_absolute": 0.001,
                                  "max_relative": 0.000001},
            "page_proxy": {"thq_scan": 1, "rerank_codes": 2,
                           "rerank_scales": 1, "rerank_payload": 3},
            "timing_ms": {"direct": {"prepare": [1.0], "score": [2.0],
                                       "topk": [3.0], "total": [6.0]},
                          "cascade": {"prepare": [1.0], "score": [2.0],
                                        "topk": [1.0], "rerank": [2.0],
                                        "total": [6.0]}},
        }
        raw.write_text(json.dumps(row) + "\n", encoding="utf-8")
        summary.write_text(json.dumps({
            "queries": 1, "warmups": 0, "repeats": 1,
            "arms": {
                "direct": {stage: summarize([[float(value)]])[0] if False else {}
                            for stage in ("prepare", "score", "topk", "total")},
                "cascade": {stage: {} for stage in ("prepare", "score", "topk", "rerank", "total")},
            }}), encoding="utf-8")
        # Reuse the command-line auditor for fail-closed mutation coverage. The
        # valid summary is produced by the same recomputation contract.
        valid = json.loads(summary.read_text(encoding="utf-8"))
        for arm, stages in (("direct", ("prepare", "score", "topk", "total")),
                            ("cascade", ("prepare", "score", "topk", "rerank", "total"))):
            for stage in stages:
                valid["arms"][arm][stage] = summarize([[float(row["timing_ms"][arm][stage][0])]])
        summary.write_text(json.dumps(valid), encoding="utf-8")
        for path in (runner, binary, query):
            path.write_bytes(b"fixture")
        base = [sys.executable, str(source), "--raw", str(raw), "--summary",
                str(summary), "--runner", str(runner), "--binary", str(binary),
                "--query-fixture", str(query), "--expected-queries", "1",
                "--expected-warmups", "0", "--expected-repeats", "1",
                "--output", str(output)]
        subprocess.run(base, check=True, capture_output=True, text=True)
        mutations = []
        mutated = json.loads(json.dumps(row))
        mutated["query"] = 1
        mutations.append(("wrong query order", mutated))
        mutated = json.loads(json.dumps(row))
        mutated["direct_top10"][1] = mutated["direct_top10"][0]
        mutations.append(("duplicate ID", mutated))
        mutated = json.loads(json.dumps(row))
        mutated["cascade_thq_top128_ids"] = mutated["cascade_thq_top128_ids"][:-1]
        mutations.append(("wrong top128 width", mutated))
        mutated = json.loads(json.dumps(row))
        mutated["parity"]["direct_vs_cascade_top10"] = False
        mutations.append(("false parity flag", mutated))
        mutated = json.loads(json.dumps(row))
        mutated["timing_ms"]["direct"]["total"][0] = 5.0
        mutations.append(("broken stage total", mutated))
        for label, candidate in mutations:
            raw.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            completed = subprocess.run(base, capture_output=True, text=True)
            require(completed.returncode != 0, f"mutation accepted: {label}")
        raw.write_text("{malformed json}\n", encoding="utf-8")
        completed = subprocess.run(base, capture_output=True, text=True)
        require(completed.returncode != 0, "mutation accepted: malformed JSON row")
        raw.write_text(json.dumps(row) + "\n", encoding="utf-8")
        mutated = json.loads(json.dumps(row))
        mutated["page_proxy"].pop("rerank_scales")
        raw.write_text(json.dumps(mutated) + "\n", encoding="utf-8")
        completed = subprocess.run(base, capture_output=True, text=True)
        require(completed.returncode != 0, "mutation accepted: missing scale pages")
        raw.write_text(json.dumps(row) + "\n", encoding="utf-8")
        summary_value = json.loads(summary.read_text(encoding="utf-8"))
        summary_value["repeats"] = 2
        summary.write_text(json.dumps(summary_value), encoding="utf-8")
        completed = subprocess.run(base, capture_output=True, text=True)
        require(completed.returncode != 0, "mutation accepted: wrong repeats")
        summary_value["repeats"] = 1
        summary_value["arms"]["direct"]["total"]["mean_ms"] += 1.0
        summary.write_text(json.dumps(summary_value), encoding="utf-8")
        completed = subprocess.run(base, capture_output=True, text=True)
        require(completed.returncode != 0, "mutation accepted: wrong summary value")
        raw.write_text(json.dumps(row) + "\n", encoding="utf-8")


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
    parser.add_argument("--expected-manifest", type=Path)
    parser.add_argument("--max-abs-score-error", type=float, default=0.02)
    parser.add_argument("--max-relative-score-error", type=float, default=0.00002)
    parser.add_argument("--environment-json", type=Path)
    parser.add_argument("--argv", default=shlex.join(sys.argv))
    args = parser.parse_args()

    for path in (args.raw, args.summary, args.runner, args.binary,
                 args.query_fixture):
        require(path.is_file(), f"required artifact does not exist: {path}")

    require(args.max_abs_score_error >= 0.0 and args.max_relative_score_error >= 0.0,
            "score error tolerances must be non-negative")
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
    input_hashes = parse_inputs(args.input)
    if args.expected_manifest:
        require(args.expected_manifest.is_file(),
                f"expected manifest does not exist: {args.expected_manifest}")
        validate_expected_manifest(args.expected_manifest, input_hashes)

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
    require(max_absolute_error <= args.max_abs_score_error,
            "INT8 absolute score error exceeds declared tolerance")
    require(max_relative_error <= args.max_relative_score_error,
            "INT8 relative score error exceeds declared tolerance")
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
        "tolerance_contract": {
            "max_abs_score_error": args.max_abs_score_error,
            "max_relative_score_error": args.max_relative_score_error,
        },
        "environment": load_environment(args.environment_json),
        "run_config": {
            "argv": args.argv,
            "query_count": args.expected_queries,
            "warmups": args.expected_warmups,
            "repeats": args.expected_repeats,
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
            **input_hashes,
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
