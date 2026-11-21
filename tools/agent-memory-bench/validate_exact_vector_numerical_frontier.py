#!/usr/bin/env python3
"""Validate the compact receipt emitted by the numerical frontier harness."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from pathlib import Path
from typing import Any


METHODS = {
    "float_lanes",
    "fp64_sequential",
    "fp64_neumaier",
    "fp64_streaming_sequential",
    "fp64_streaming_neumaier",
    "fp64_pairwise",
    "fp64_blockwise",
    "fp64_overflow_guarded_blockwise",
}


def fail(message: str) -> None:
    raise ValueError(message)


def finite(value: Any, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        fail(f"{name} is not numeric")
    if not math.isfinite(float(value)):
        fail(f"{name} is not finite")


def sha(value: Any, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        fail(f"{name} is not a lowercase Git SHA")


def close(actual: float, expected: float, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12):
        fail(f"{name} is inconsistent: {actual!r} != {expected!r}")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def validate(
    receipt: dict[str, Any],
    *,
    expected_source_commit: str | None = None,
    expected_checkout_sha: str | None = None,
    expected_pr_head_sha: str | None = None,
) -> None:
    if receipt.get("schema_version") != 2:
        fail("unsupported schema_version")
    if receipt.get("family") != "exact_vector_numerical_optimization_frontier_v1":
        fail("wrong family")
    if receipt.get("status") != "EXECUTED":
        fail("receipt is not EXECUTED")
    if receipt.get("correctness_status") != "PASS":
        fail("correctness gate did not pass")
    sha(receipt.get("source_commit"), "source_commit")
    sha(receipt.get("checkout_sha"), "checkout_sha")
    sha(receipt.get("expected_pr_head_sha"), "expected_pr_head_sha")
    if receipt["source_commit"] != receipt["checkout_sha"]:
        fail("source_commit and checkout_sha disagree")
    if expected_source_commit is not None:
        sha(expected_source_commit, "expected source commit")
        if receipt["source_commit"] != expected_source_commit:
            fail("receipt source_commit does not match expected source commit")
    if expected_checkout_sha is not None:
        sha(expected_checkout_sha, "expected checkout SHA")
        if receipt["checkout_sha"] != expected_checkout_sha:
            fail("receipt checkout_sha does not match actual checkout")
    if expected_pr_head_sha is not None:
        sha(expected_pr_head_sha, "expected PR head SHA")
        if receipt["expected_pr_head_sha"] != expected_pr_head_sha:
            fail("receipt expected_pr_head_sha does not match CI event")
    for key in ("documents", "queries", "dimension", "warmups", "measured_repeats"):
        value = receipt.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            fail(f"{key} must be a positive integer")
    if not isinstance(receipt.get("corpus_seed"), str):
        fail("corpus_seed is missing")
    if not isinstance(receipt.get("corpus_digest_fnv1a64"), str) or not re.fullmatch(
        r"[0-9a-f]{16}", receipt["corpus_digest_fnv1a64"]
    ):
        fail("corpus digest is malformed")
    if not isinstance(receipt.get("methods"), dict):
        fail("methods is missing")
    if set(receipt["methods"]) != METHODS:
        fail("method set is incomplete or unexpected")
    if not isinstance(receipt.get("allocation_accounting"), dict):
        fail("allocation_accounting is missing")
    if not isinstance(receipt.get("build_provenance"), dict):
        fail("build_provenance is missing")
    for key in ("compiler_id", "compiler_version", "configuration", "system_processor", "simd_backend"):
        if not isinstance(receipt["build_provenance"].get(key), str):
            fail(f"build_provenance.{key} is missing")
    if receipt["build_provenance"].get("cxx_standard") != 17:
        fail("build_provenance.cxx_standard must be 17")
    expected_pairs = receipt["documents"] * receipt["queries"] * receipt["measured_repeats"]
    for key in ("measured_scan_scored_pairs", "measured_kernel_scored_pairs"):
        if receipt.get(key) != expected_pairs:
            fail(f"{key} does not match documents * queries * measured_repeats")
    if not isinstance(receipt.get("guarded_fallback_semantics"), str):
        fail("guarded fallback semantics are missing")
    for name, method in receipt["methods"].items():
        if not isinstance(method, dict):
            fail(f"{name} is not an object")
        if not isinstance(method.get("samples_ms"), list) or not method["samples_ms"]:
            fail(f"{name}.samples_ms is empty")
        if method.get("sample_count") != len(method["samples_ms"]):
            fail(f"{name}.sample_count does not match samples_ms")
        if method["sample_count"] != receipt["measured_repeats"]:
            fail(f"{name}.sample_count does not match measured_repeats")
        samples = [float(value) for value in method["samples_ms"]]
        close(float(method["mean_ms"]), sum(samples) / len(samples), f"{name}.mean_ms")
        close(float(method["min_ms"]), min(samples), f"{name}.min_ms")
        close(float(method["p50_ms"]), percentile(samples, 0.50), f"{name}.p50_ms")
        close(float(method["p95_ms"]), percentile(samples, 0.95), f"{name}.p95_ms")
        close(float(method["max_ms"]), max(samples), f"{name}.max_ms")
        for index, value in enumerate(method["samples_ms"]):
            finite(value, f"{name}.samples_ms[{index}]")
            if value < 0:
                fail(f"{name}.samples_ms[{index}] is negative")
        for key in (
            "mean_ms",
            "min_ms",
            "p50_ms",
            "p95_ms",
            "max_ms",
            "max_abs_error_vs_long_double_oracle",
            "max_relative_error_vs_long_double_oracle",
            "max_abs_error_after_fp32_rounding",
            "max_relative_error_after_fp32_rounding",
            "guarded_fallback_frequency",
        ):
            finite(method.get(key), f"{name}.{key}")
            if method[key] < 0:
                fail(f"{name}.{key} is negative")
        if not isinstance(method.get("kernel_timing"), dict):
            fail(f"{name}.kernel_timing is missing")
        kernel = method["kernel_timing"]
        if kernel.get("sample_count") != len(kernel.get("samples_ms", [])):
            fail(f"{name}.kernel_timing.sample_count does not match samples_ms")
        if kernel["sample_count"] != receipt["measured_repeats"]:
            fail(f"{name}.kernel_timing.sample_count does not match measured_repeats")
        if not kernel.get("samples_ms"):
            fail(f"{name}.kernel_timing.samples_ms is empty")
        kernel_samples = [float(value) for value in kernel["samples_ms"]]
        for index, value in enumerate(kernel["samples_ms"]):
            finite(value, f"{name}.kernel_timing.samples_ms[{index}]")
            if value < 0:
                fail(f"{name}.kernel_timing.samples_ms[{index}] is negative")
        for key in (
            "mean_ms", "min_ms", "p50_ms", "p95_ms", "max_ms",
            "max_abs_dot_error_vs_long_double_oracle",
            "max_relative_dot_error_vs_long_double_oracle",
        ):
            finite(kernel.get(key), f"{name}.kernel_timing.{key}")
            if kernel[key] < 0:
                fail(f"{name}.kernel_timing.{key} is negative")
        close(float(kernel["mean_ms"]), sum(kernel_samples) / len(kernel_samples), f"{name}.kernel_timing.mean_ms")
        close(float(kernel["min_ms"]), min(kernel_samples), f"{name}.kernel_timing.min_ms")
        close(float(kernel["p50_ms"]), percentile(kernel_samples, 0.50), f"{name}.kernel_timing.p50_ms")
        close(float(kernel["p95_ms"]), percentile(kernel_samples, 0.95), f"{name}.kernel_timing.p95_ms")
        close(float(kernel["max_ms"]), max(kernel_samples), f"{name}.kernel_timing.max_ms")
        finite(kernel.get("guarded_fallback_frequency"), f"{name}.kernel_timing.guarded_fallback_frequency")
        if kernel["guarded_fallback_frequency"] < 0:
            fail(f"{name}.kernel_timing.guarded_fallback_frequency is negative")
        for key in ("nonfinite_scores", "top1_exact_queries", "topk_exact_queries"):
            value = method.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                fail(f"{name}.{key} is not a non-negative integer")
            if key != "nonfinite_scores" and value > receipt["queries"]:
                fail(f"{name}.{key} exceeds query count")
        for key in ("guarded_fallback_events",):
            value = method.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                fail(f"{name}.{key} is not a non-negative integer")
        if method["guarded_fallback_events"] > expected_pairs:
            fail(f"{name}.guarded_fallback_events exceeds measured pairs")
        close(
            float(method["guarded_fallback_frequency"]),
            method["guarded_fallback_events"] / expected_pairs,
            f"{name}.guarded_fallback_frequency",
        )
        kernel_events = kernel.get("guarded_fallback_events")
        if not isinstance(kernel_events, int) or isinstance(kernel_events, bool) or kernel_events < 0:
            fail(f"{name}.kernel_timing.guarded_fallback_events is invalid")
        if kernel_events > expected_pairs:
            fail(f"{name}.kernel_timing.guarded_fallback_events exceeds measured pairs")
        close(
            float(kernel["guarded_fallback_frequency"]),
            kernel_events / expected_pairs,
            f"{name}.kernel_timing.guarded_fallback_frequency",
        )
    if receipt.get("production_api_changed") is not False:
        fail("production_api_changed must remain false")


def fixture() -> dict[str, Any]:
    method: dict[str, Any] = {
        "samples_ms": [1.0],
        "sample_count": 1,
        "mean_ms": 1.0,
        "min_ms": 1.0,
        "p50_ms": 1.0,
        "p95_ms": 1.0,
        "max_ms": 1.0,
        "nonfinite_scores": 0,
        "max_abs_error_vs_long_double_oracle": 0.0,
        "max_relative_error_vs_long_double_oracle": 0.0,
        "max_abs_error_after_fp32_rounding": 0.0,
        "max_relative_error_after_fp32_rounding": 0.0,
        "top1_exact_queries": 1,
        "topk_exact_queries": 1,
        "guarded_fallback_observed": False,
        "guarded_fallback_events": 0,
        "guarded_fallback_frequency": 0.0,
        "kernel_timing": {
            "samples_ms": [1.0],
            "sample_count": 1,
            "mean_ms": 1.0,
            "min_ms": 1.0,
            "p50_ms": 1.0,
            "p95_ms": 1.0,
            "max_ms": 1.0,
            "max_abs_dot_error_vs_long_double_oracle": 0.0,
            "max_relative_dot_error_vs_long_double_oracle": 0.0,
            "guarded_fallback_observed": False,
            "guarded_fallback_events": 0,
            "guarded_fallback_frequency": 0.0,
        },
    }
    return {
        "schema_version": 2,
        "family": "exact_vector_numerical_optimization_frontier_v1",
        "status": "EXECUTED",
        "correctness_status": "PASS",
        "source_commit": "0" * 40,
        "checkout_sha": "0" * 40,
        "expected_pr_head_sha": "1" * 40,
        "production_api_changed": False,
        "documents": 1,
        "queries": 1,
        "dimension": 2,
        "corpus_seed": "0x5eed1234",
        "corpus_digest_fnv1a64": "0" * 16,
        "warmups": 1,
        "measured_repeats": 1,
        "measured_scan_scored_pairs": 1,
        "measured_kernel_scored_pairs": 1,
        "guarded_fallback_semantics": "finite-overflow diagnostic only",
        "allocation_accounting": {},
        "build_provenance": {
            "compiler_id": "test",
            "compiler_version": "test",
            "configuration": "Release",
            "cxx_standard": 17,
            "system_processor": "test",
            "simd_backend": "none",
        },
        "methods": {name: copy.deepcopy(method) for name in METHODS},
    }


def self_test() -> None:
    valid = fixture()
    validate(valid)
    invalid_nan = copy.deepcopy(valid)
    invalid_nan["methods"]["float_lanes"]["samples_ms"][0] = float("nan")
    try:
        validate(invalid_nan)
    except ValueError:
        pass
    else:
        fail("NaN mutation was accepted")
    invalid_count = copy.deepcopy(valid)
    invalid_count["methods"]["fp64_pairwise"]["sample_count"] = 2
    try:
        validate(invalid_count)
    except ValueError:
        pass
    else:
        fail("sample-count mutation was accepted")
    invalid_source = copy.deepcopy(valid)
    invalid_source["source_commit"] = "unknown"
    try:
        validate(invalid_source)
    except ValueError:
        pass
    else:
        fail("unknown source mutation was accepted")
    invalid_frequency = copy.deepcopy(valid)
    invalid_frequency["methods"]["fp64_pairwise"]["guarded_fallback_frequency"] = 1.0
    try:
        validate(invalid_frequency)
    except ValueError:
        pass
    else:
        fail("fallback-frequency mutation was accepted")
    invalid_mean = copy.deepcopy(valid)
    invalid_mean["methods"]["float_lanes"]["mean_ms"] = 2.0
    try:
        validate(invalid_mean)
    except ValueError:
        pass
    else:
        fail("summary-statistics mutation was accepted")
    print("exact vector numerical frontier receipt validator self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-checkout-sha")
    parser.add_argument("--expected-pr-head-sha")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.receipt is None:
        parser.error("receipt is required unless --self-test is used")
    try:
        with args.receipt.open("r", encoding="utf-8") as stream:
            receipt = json.load(stream, parse_constant=lambda value: fail(f"non-finite JSON constant: {value}"))
        if not isinstance(receipt, dict):
            fail("receipt root is not an object")
        validate(
            receipt,
            expected_source_commit=args.expected_source_commit,
            expected_checkout_sha=args.expected_checkout_sha,
            expected_pr_head_sha=args.expected_pr_head_sha,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"validation failed: {error}")
        return 1
    print("exact vector numerical frontier receipt: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
