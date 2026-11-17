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


def validate(receipt: dict[str, Any]) -> None:
    if receipt.get("schema_version") != 1:
        fail("unsupported schema_version")
    if receipt.get("family") != "exact_vector_numerical_optimization_frontier_v1":
        fail("wrong family")
    if receipt.get("status") != "EXECUTED":
        fail("receipt is not EXECUTED")
    if receipt.get("correctness_status") != "PASS":
        fail("correctness gate did not pass")
    source = receipt.get("source_commit")
    if source != "unknown" and not isinstance(source, str):
        fail("source_commit is malformed")
    if isinstance(source, str) and source != "unknown" and not re.fullmatch(r"[0-9a-f]{40}", source):
        fail("source_commit is not a lowercase Git SHA")
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
    if not isinstance(receipt.get("guarded_fallback_semantics"), str):
        fail("guarded fallback semantics are missing")
    for name, method in receipt["methods"].items():
        if not isinstance(method, dict):
            fail(f"{name} is not an object")
        if not isinstance(method.get("samples_ms"), list) or not method["samples_ms"]:
            fail(f"{name}.samples_ms is empty")
        if method.get("sample_count") != len(method["samples_ms"]):
            fail(f"{name}.sample_count does not match samples_ms")
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
        if not kernel.get("samples_ms"):
            fail(f"{name}.kernel_timing.samples_ms is empty")
        for index, value in enumerate(kernel["samples_ms"]):
            finite(value, f"{name}.kernel_timing.samples_ms[{index}]")
            if value < 0:
                fail(f"{name}.kernel_timing.samples_ms[{index}] is negative")
        for key in ("mean_ms", "max_abs_dot_error_vs_long_double_oracle", "max_relative_dot_error_vs_long_double_oracle"):
            finite(kernel.get(key), f"{name}.kernel_timing.{key}")
            if kernel[key] < 0:
                fail(f"{name}.kernel_timing.{key} is negative")
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
            "max_abs_dot_error_vs_long_double_oracle": 0.0,
            "max_relative_dot_error_vs_long_double_oracle": 0.0,
            "guarded_fallback_observed": False,
            "guarded_fallback_events": 0,
            "guarded_fallback_frequency": 0.0,
        },
    }
    return {
        "schema_version": 1,
        "family": "exact_vector_numerical_optimization_frontier_v1",
        "status": "EXECUTED",
        "correctness_status": "PASS",
        "source_commit": "0" * 40,
        "production_api_changed": False,
        "documents": 1,
        "queries": 1,
        "dimension": 2,
        "corpus_seed": "0x5eed1234",
        "corpus_digest_fnv1a64": "0" * 16,
        "warmups": 1,
        "measured_repeats": 1,
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
    print("exact vector numerical frontier receipt validator self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
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
        validate(receipt)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"validation failed: {error}")
        return 1
    print("exact vector numerical frontier receipt: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

