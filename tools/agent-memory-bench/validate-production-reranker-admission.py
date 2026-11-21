#!/usr/bin/env python3
"""Validate the production-shaped reranker admission receipt and sidecar."""

from __future__ import annotations

import hashlib
import json
import math
import copy
import itertools
import re
import runpy
from pathlib import Path
from typing import Any


EXPECTED_VARIANTS = {
    "linear_scan",
    "sorted_identity_index",
    "hash_identity_index",
}
EXPECTED_COUNTS = [10, 50, 100, 500, 1000, 2000]
EXPECTED_CONTROLS = {
    "foreign_chunk",
    "foreign_resource",
    "mismatched_section",
    "protected_metadata_mutation",
    "duplicate_id",
    "all_repeated_ids",
    "late_duplicate_id",
    "non_finite_score",
    "positive_infinity",
    "negative_infinity",
    "untrusted_tier",
    "untrusted_enrichment",
    "oversized_output",
}
SOURCE_PATHS = [
    "tools/agent-memory-bench/production_reranker_admission.cpp",
    "tools/agent-memory-bench/CMakeLists.txt",
    "tools/agent-memory-bench/RerankerResearchProfile.hpp",
    "tools/agent-memory-bench/generate-production-reranker-variants.py",
    "tools/agent-memory-bench/validate-production-reranker-admission.py",
    "src/agent_memory/retrieval/HybridRetrievalEngine.cpp",
    "src/agent_memory/retrieval/HybridRetrievalEngine.hpp",
    "src/agent_memory/lexical/IReranker.hpp",
    "src/agent_memory/lexical/ILexicalIndex.hpp",
]
FIXTURE_DESCRIPTION = (
    "candidate_counts=10,50,100,500,1000,2000;metadata_widths=2,8,32;"
    "warmups=2;repeats=12;orders=all-six-permutations;"
    "variants=linear_scan,sorted_identity_index,hash_identity_index;"
    "profile_warmups=1;profile_repeats=3;sorted_input_width=8;"
    "negative_counts=10,100,2000;negative_widths=2,32;"
    "negative_mutations=all_repeated_ids,late_duplicate_id"
)
PROFILE_STAGES = (
    "index_search", "trusted_pool_copy", "reranker", "admission_total",
    "lookup_construction", "validation_and_lookup", "trusted_result_copy",
    "response_materialization",
)
GENERATED_NAMES = {
    f"{label}ResearchHybridRetrievalEngine.cpp"
    for label in ("Linear", "Sorted", "Hash", "ProfiledLinear", "ProfiledSorted", "ProfiledHash")
}


def source_manifest_digest(root: Path) -> str:
    manifest = "".join(f"{path}:{_digest(root / path)}\n" for path in SOURCE_PATHS)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _is_sha(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        character in "0123456789abcdef" for character in value
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_samples(timing: Any, count: int = 12, allow_zero: bool = False) -> bool:
    if not isinstance(timing, dict):
        return False
    samples = timing.get("samples_ms")
    if not isinstance(samples, list) or not samples:
        return False
    if timing.get("sample_count") != len(samples) or len(samples) != count:
        return False
    if not all(type(value) in (int, float) and math.isfinite(value)
               and (value >= 0 if allow_zero else value > 0) for value in samples):
        return False
    ordered = sorted(samples)
    summaries = {
        "mean_ms": sum(samples) / len(samples),
        "p50_ms": ordered[int(0.5 * (len(samples) - 1))],
        "p95_ms": ordered[int(0.95 * (len(samples) - 1))],
        "min_ms": min(samples),
        "max_ms": max(samples),
    }
    return all(
        type(timing.get(key)) in (int, float)
        and math.isclose(timing[key], value, rel_tol=1e-9, abs_tol=1e-12)
        for key, value in summaries.items()
    )


def validate_profile(
    profile: Any, candidate_count: int, variant: str, *, validated: int | None = None,
    copied: int | None = None,
) -> list[str]:
    if not isinstance(profile, dict):
        return ["stage_profile must be a separate instrumented observation"]
    errors = []
    if (profile.get("execution_status") != "EXECUTED" or profile.get("warmups") != 1
            or profile.get("repeats") != 3
            or profile.get("instrumentation_overhead_included") is not True
            or profile.get("primary_performance_evidence") is not False
            or "separate instrumented retrieve" not in str(profile.get("timing_scope", ""))):
        errors.append("stage_profile must separate direct instrumented timing from primary evidence")
    if not _finite_samples(profile.get("full_retrieve_timing"), 3):
        errors.append("stage_profile full retrieve samples are invalid")
    for key in ("allocation_counts", "allocated_bytes"):
        values = profile.get(key)
        if not isinstance(values, list) or len(values) != 3 or not all(type(v) is int and v >= 0 for v in values):
            errors.append(f"stage_profile {key} is incomplete")
    stages = profile.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(PROFILE_STAGES):
        return errors + ["stage_profile stages are incomplete"]
    expected_calls = {name: 1 for name in PROFILE_STAGES}
    expected_calls["lookup_construction"] = int(variant != "linear_scan")
    expected_calls["validation_and_lookup"] = candidate_count if validated is None else validated
    expected_calls["trusted_result_copy"] = candidate_count if copied is None else copied
    for name in PROFILE_STAGES:
        stage = stages[name]
        if not isinstance(stage, dict) or not _finite_samples(stage.get("timing"), 3, allow_zero=True):
            errors.append(f"stage_profile {name} has invalid direct samples")
            continue
        if stage.get("calls") != [expected_calls[name]] * 3:
            errors.append(f"stage_profile {name} call count contradicts exercised path")
        if expected_calls[name] == 0 and any(stage["timing"]["samples_ms"]):
            errors.append(f"stage_profile {name} reports time for an unexecuted stage")
    if errors:
        return errors
    for sample in range(3):
        measured = {name: stages[name]["timing"]["samples_ms"][sample] for name in PROFILE_STAGES}
        nested = sum(measured[name] for name in (
            "lookup_construction", "validation_and_lookup", "trusted_result_copy"
        ))
        exclusive = sum(measured[name] for name in (
            "index_search", "trusted_pool_copy", "reranker", "admission_total", "response_materialization"
        ))
        if nested > measured["admission_total"] + 1e-9:
            errors.append("stage_profile nested admission phases exceed their direct parent timer")
        if exclusive > profile["full_retrieve_timing"]["samples_ms"][sample] + 1e-9:
            errors.append("stage_profile direct phases exceed full instrumented retrieve")
    return errors


def validate_receipt(
    receipt: Any, *, expected_head: str, actual_checkout_sha: str,
    expected_compiler: str | None = None, expected_system: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt must be a JSON object"]
    if receipt.get("schema_version") != 2:
        errors.append("schema_version must be 2; historical v1 receipts are not rewritten")
    if receipt.get("benchmark_name") != "production_reranker_admission":
        errors.append("benchmark_name is not production_reranker_admission")
    if receipt.get("source_worktree_clean") is not True:
        errors.append("dirty source worktree cannot be presented as exact-source execution evidence")
    if not _is_sha(receipt.get("source_commit"), 40):
        errors.append("source_commit must be a lowercase 40-character SHA")
    elif receipt["source_commit"] != actual_checkout_sha:
        errors.append("source_commit does not match actual compiled checkout SHA")
    if not _is_sha(receipt.get("expected_pr_head_sha"), 40):
        errors.append("expected_pr_head_sha must be a lowercase 40-character SHA")
    elif receipt["expected_pr_head_sha"] != expected_head:
        errors.append("expected_pr_head_sha does not match the requested PR HEAD")
    for field in ("source_manifest_sha256", "fixture_sha256"):
        if not _is_sha(receipt.get(field), 64):
            errors.append(f"{field} must be a lowercase 64-character SHA-256")
    generated = receipt.get("generated_source_sha256")
    if (not isinstance(generated, dict) or set(generated) != GENERATED_NAMES
            or not all(_is_sha(value, 64) for value in generated.values())):
        errors.append("generated_source_sha256 must bind all six instrumented/uninstrumented class copies")
    environment = receipt.get("build_environment")
    if not isinstance(environment, dict):
        errors.append("build_environment must be an object")
    else:
        if environment.get("compiler_id") not in {"GNU", "MSVC", "Clang", "AppleClang"}:
            errors.append("build_environment.compiler_id is unsupported")
        version = environment.get("compiler_version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[-a-zA-Z0-9.]*)", version):
            errors.append("build_environment.compiler_version must identify the toolchain")
        if type(environment.get("cxx_standard")) is not int or environment["cxx_standard"] != 17:
            errors.append("build_environment.cxx_standard must be 17")
        if environment.get("build_configuration") != "Release":
            errors.append("build_environment.build_configuration must be Release")
        if environment.get("system_name") not in {"Windows", "Linux", "Darwin"}:
            errors.append("build_environment.system_name is unsupported")
        if type(environment.get("pointer_bits")) is not int or environment["pointer_bits"] not in {32, 64}:
            errors.append("build_environment.pointer_bits must be 32 or 64")
        processor = environment.get("system_processor")
        if not isinstance(processor, str) or not processor.strip():
            errors.append("build_environment.system_processor is required")
        if expected_compiler is not None and environment.get("compiler_id") != expected_compiler:
            errors.append("build_environment.compiler_id does not match the required compiler")
        if expected_system is not None and environment.get("system_name") != expected_system:
            errors.append("build_environment.system_name does not match the required platform")
    if receipt.get("production_contract") != (
        "HybridRetrievalEngine::retrieve() admission implementation merged by PR #625"
    ):
        errors.append("production_contract is not the declared PR #625 contract")
    if receipt.get("candidate_counts") != EXPECTED_COUNTS:
        errors.append("candidate_counts must cover 10, 50, 100, 500, 1000, and 2000")
    if receipt.get("warmups") != 2 or receipt.get("repeats") != 12:
        errors.append("warmups/repeats do not match the frozen fixture")
    if set(receipt.get("variants", [])) != EXPECTED_VARIANTS:
        errors.append("variants must contain linear, sorted, and hash controls exactly")
    if set(receipt.get("fail_closed_controls", [])) != EXPECTED_CONTROLS:
        errors.append("fail_closed_controls do not cover the required negative matrix")
    distribution = receipt.get("candidate_count_distribution")
    if not isinstance(distribution, dict) or (
        distribution.get("execution_status") != "NOT_EXECUTED"
        or distribution.get("reason") != "REPRESENTATIVE_REQUEST_DISTRIBUTION_UNAVAILABLE"
        or not distribution.get("source")
        or distribution.get("synthetic_case_counts_are_request_frequencies") is not False
    ):
        errors.append("real request distribution is unavailable, not a synthetic frequency distribution")
    memory = receipt.get("memory_scope")
    if not isinstance(memory, dict) or (
        memory.get("peak_retained_workspace") != "NOT_MEASURED"
        or memory.get("process_peak_rss") != "NOT_MEASURED"
        or not memory.get("peak_retained_reason") or not memory.get("allocation_series")
    ):
        errors.append("allocated totals cannot be promoted to peak retained workspace or RSS")
    if receipt.get("production_default_decision") != "KEEP CURRENT":
        errors.append("research receipt must not activate production admission")
    if not str(receipt.get("conditional_dispatch_decision", "")).startswith("DEFER:"):
        errors.append("conditional dispatch remains deferred without request-frequency evidence")

    cases = receipt.get("cases")
    if not isinstance(cases, list) or len(cases) != len(EXPECTED_COUNTS) * 3:
        errors.append("cases must contain one entry for every frozen candidate count")
        return errors
    sorted_cases = receipt.get("sorted_input_cases")
    if not isinstance(sorted_cases, list) or len(sorted_cases) != len(EXPECTED_COUNTS):
        errors.append("sorted_input_cases must cover all candidate counts separately")
        sorted_cases = []
    for index, case in enumerate(cases + sorted_cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if case.get("candidate_count") != EXPECTED_COUNTS[index % len(EXPECTED_COUNTS)]:
            errors.append(f"{prefix}.candidate_count is out of order")
        expected_width = [2, 8, 32][index // len(EXPECTED_COUNTS)] if index < len(cases) else 8
        if case.get("metadata_width") != expected_width:
            errors.append(f"{prefix}.metadata_width is not frozen")
        expected_order = "fixture_numeric_order" if index < len(cases) else "identity_sorted"
        if case.get("trusted_input_order") != expected_order or case.get("reranker_output_order") != "reverse_trusted_input":
            errors.append(f"{prefix}.trusted/reranked input ordering is mislabeled")
        if "HybridRetrievalEngine::retrieve()" not in str(case.get("timing_scope", "")):
            errors.append(f"{prefix}.timing_scope is not end-to-end")
        variants = case.get("variants")
        if not isinstance(variants, list) or len(variants) != 3 or {item.get("algorithm") for item in variants if isinstance(item, dict)} != EXPECTED_VARIANTS:
            errors.append(f"{prefix}.variants are incomplete")
            continue
        orders = case.get("execution_orders")
        if not isinstance(orders, list) or sorted(tuple(order) for order in orders) != sorted(list(itertools.permutations(range(3))) * 2):
            errors.append(f"{prefix}.execution_orders must balance all six permutations")
        for variant in variants:
            name = variant.get("algorithm", "unknown")
            variant_prefix = f"{prefix}.{name}"
            errors.extend(f"{variant_prefix}.{error}" for error in validate_profile(
                variant.get("stage_profile"), case.get("candidate_count", 0), name
            ))
            if not _finite_samples(variant.get("timing")):
                errors.append(f"{variant_prefix}.timing has invalid raw samples")
            elif not math.isclose(case.get("mean_ms_by_variant", {}).get(name, -1),
                                  variant["timing"]["mean_ms"], rel_tol=1e-9):
                errors.append(f"{variant_prefix}.mean_ms_by_variant must match raw samples")
            if not _finite_samples(variant.get("setup_and_retrieve_timing")):
                errors.append(f"{variant_prefix}.setup_and_retrieve_timing has invalid samples")
            elif _finite_samples(variant.get("timing")) and any(
                total < query for total, query in zip(
                    variant["setup_and_retrieve_timing"]["samples_ms"], variant["timing"]["samples_ms"]
                )
            ):
                errors.append(f"{variant_prefix}.nested timing scopes contradict")
            for allocation_field in ("allocation_counts", "allocated_bytes"):
                values = variant.get(allocation_field)
                if not isinstance(values, list) or len(values) != 12 or not all(type(value) is int and value >= 0 for value in values):
                    errors.append(f"{variant_prefix}.{allocation_field} is not a complete raw allocation series")
            for field in (
                "full_retrieve_path",
                "index_construction_included",
                "correctness_parity",
                "materialization_checked",
                "fail_closed_controls_checked",
                "fixture_engine_setup_excluded",
            ):
                if variant.get(field) is not True:
                    errors.append(f"{variant_prefix}.{field} must be true")
    negatives = receipt.get("negative_workloads")
    expected_negative = set(itertools.product(
        (10, 100, 2000), (2, 32), ("all_repeated_ids", "late_duplicate_id"), EXPECTED_VARIANTS
    ))
    observed_negative = set()
    if not isinstance(negatives, list):
        errors.append("negative_workloads must contain measured early and late duplicate rejection")
        negatives = []
    for item in negatives:
        if not isinstance(item, dict):
            errors.append("negative_workload must be an object")
            continue
        key = (item.get("candidate_count"), item.get("metadata_width"), item.get("mutation"), item.get("algorithm"))
        if key not in expected_negative or key in observed_negative:
            errors.append("negative workload is duplicated or outside the frozen matrix")
            continue
        observed_negative.add(key)
        count, _, mutation, variant = key
        validated = 2 if mutation == "all_repeated_ids" else count
        if (item.get("rejected") is not True or item.get("validated_candidates") != validated
                or item.get("trusted_copies_before_rejection") != validated - 1):
            errors.append("negative workload rejection depth is mislabeled")
        errors.extend(validate_profile(item.get("stage_profile"), count, variant,
                                       validated=validated, copied=validated - 1))
    if observed_negative != expected_negative:
        errors.append("negative workload matrix is incomplete")
    break_even = receipt.get("break_even")
    if not isinstance(break_even, dict) or "definition" not in break_even or "scope" not in break_even:
        errors.append("break_even must state its definition and bounded scope")
    else:
        if errors:
            return errors
        expected_crossovers = []
        for width in (2, 8, 32):
            crossing = {"metadata_width": width, "sorted_first_tested_count": None, "hash_first_tested_count": None}
            for case in cases:
                if case.get("metadata_width") != width:
                    continue
                means = {arm["algorithm"]: sum(arm["timing"]["samples_ms"]) / 12 for arm in case["variants"]}
                for field, variant in (("sorted_first_tested_count", "sorted_identity_index"), ("hash_first_tested_count", "hash_identity_index")):
                    if crossing[field] is None and means[variant] <= means["linear_scan"]:
                        crossing[field] = case["candidate_count"]
            expected_crossovers.append(crossing)
        if break_even.get("by_metadata_width") != expected_crossovers:
            errors.append("break_even does not match observed mean crossovers")
    return errors


def validate_sidecar(
    sidecar: Any,
    receipt_bytes: bytes,
    *,
    expected_head: str,
    actual_checkout_sha: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(sidecar, dict):
        return ["sidecar must be a JSON object"]
    if sidecar.get("receipt_sha256") != hashlib.sha256(receipt_bytes).hexdigest():
        errors.append("sidecar receipt_sha256 does not match receipt bytes")
    if sidecar.get("expected_pr_head_sha") != expected_head:
        errors.append("sidecar expected_pr_head_sha mismatch")
    if sidecar.get("source_commit") != actual_checkout_sha:
        errors.append("sidecar source_commit mismatch")
    return errors


def self_test() -> None:
    expected = "a" * 40

    def fake_timing(value: float, repeats: int = 3) -> dict[str, Any]:
        return {"samples_ms": [value] * repeats, "sample_count": repeats,
                "mean_ms": value, "p50_ms": value, "p95_ms": value,
                "min_ms": value, "max_ms": value}

    def fake_profile(count: int, name: str, validated: int | None = None) -> dict[str, Any]:
        exercised = count if validated is None else validated
        copied = count if validated is None else validated - 1
        stages = {}
        for stage in PROFILE_STAGES:
            calls = (exercised if stage == "validation_and_lookup" else copied if stage == "trusted_result_copy"
                     else int(name != "linear_scan") if stage == "lookup_construction" else 1)
            value = 4.0 if stage == "admission_total" else 0.0 if calls == 0 else 1.0
            stages[stage] = {"timing": fake_timing(value), "calls": [calls] * 3}
        return {
            "execution_status": "EXECUTED", "warmups": 1, "repeats": 3,
            "timing_scope": "separate instrumented retrieve, direct stage observations",
            "instrumentation_overhead_included": True, "primary_performance_evidence": False,
            "full_retrieve_timing": fake_timing(10.0), "stages": stages,
            "allocation_counts": [1] * 3, "allocated_bytes": [1] * 3,
        }

    valid = {
        "schema_version": 2,
        "benchmark_name": "production_reranker_admission",
        "source_commit": expected,
        "source_worktree_clean": True,
        "expected_pr_head_sha": expected,
        "source_manifest_sha256": "b" * 64,
        "fixture_sha256": "c" * 64,
        "generated_source_sha256": {name: "d" * 64 for name in GENERATED_NAMES},
        "build_environment": {
            "compiler_id": "MSVC", "compiler_version": "19.44.35228.0",
            "cxx_standard": 17, "build_configuration": "Release",
            "system_name": "Windows", "system_processor": "AMD64", "pointer_bits": 64,
        },
        "production_contract": "HybridRetrievalEngine::retrieve() admission implementation merged by PR #625",
        "candidate_counts": EXPECTED_COUNTS,
        "warmups": 2,
        "repeats": 12,
        "variants": sorted(EXPECTED_VARIANTS),
        "fail_closed_controls": sorted(EXPECTED_CONTROLS),
        "candidate_count_distribution": {
            "execution_status": "NOT_EXECUTED", "reason": "REPRESENTATIVE_REQUEST_DISTRIBUTION_UNAVAILABLE",
            "source": "fixed synthetic fixtures, not real query frequency",
            "synthetic_case_counts_are_request_frequencies": False,
        },
        "memory_scope": {"peak_retained_workspace": "NOT_MEASURED", "process_peak_rss": "NOT_MEASURED",
                         "peak_retained_reason": "no live allocation tracking", "allocation_series": "ordinary new/new[]"},
        "production_default_decision": "KEEP CURRENT",
        "conditional_dispatch_decision": "DEFER: no frequency evidence",
        "break_even": {"definition": "bounded", "scope": "fixture", "by_metadata_width": [
            {"metadata_width": width, "sorted_first_tested_count": 10, "hash_first_tested_count": 10}
            for width in (2, 8, 32)
        ]},
        "cases": [],
    }
    for width, count in itertools.product((2, 8, 32), EXPECTED_COUNTS):
        valid["cases"].append({
            "candidate_count": count,
            "metadata_width": width,
            "trusted_input_order": "fixture_numeric_order",
            "reranker_output_order": "reverse_trusted_input",
            "timing_scope": "HybridRetrievalEngine::retrieve() end-to-end",
            "execution_orders": list(itertools.permutations(range(3))) * 2,
            "mean_ms_by_variant": {name: 1.0 for name in EXPECTED_VARIANTS},
            "variants": [
                {
                    "algorithm": name,
                    "timing": {"samples_ms": [1.0] * 12, "sample_count": 12, "mean_ms": 1.0, "p50_ms": 1.0, "p95_ms": 1.0, "min_ms": 1.0, "max_ms": 1.0},
                    "setup_and_retrieve_timing": {"samples_ms": [2.0] * 12, "sample_count": 12, "mean_ms": 2.0, "p50_ms": 2.0, "p95_ms": 2.0, "min_ms": 2.0, "max_ms": 2.0},
                    "fixture_engine_setup_excluded": True,
                    "allocation_counts": [1] * 12,
                    "allocated_bytes": [1] * 12,
                    "full_retrieve_path": True,
                    "index_construction_included": True,
                    "correctness_parity": True,
                    "materialization_checked": True,
                    "fail_closed_controls_checked": True,
                    "stage_profile": fake_profile(count, name),
                }
                for name in sorted(EXPECTED_VARIANTS)
            ],
        })
    valid["sorted_input_cases"] = copy.deepcopy(valid["cases"][6:12])
    for case in valid["sorted_input_cases"]:
        case["trusted_input_order"] = "identity_sorted"
    valid["negative_workloads"] = []
    for count, width, mutation, name in itertools.product(
        (10, 100, 2000), (2, 32), ("all_repeated_ids", "late_duplicate_id"), sorted(EXPECTED_VARIANTS)
    ):
        exercised = 2 if mutation == "all_repeated_ids" else count
        valid["negative_workloads"].append({
            "candidate_count": count, "metadata_width": width, "mutation": mutation, "algorithm": name,
            "rejected": True, "validated_candidates": exercised, "trusted_copies_before_rejection": exercised - 1,
            "stage_profile": fake_profile(count, name, exercised),
        })
    assert not validate_receipt(valid, expected_head=expected, actual_checkout_sha=expected)
    for field, value in (
        ("compiler_id", "FAKE"), ("compiler_version", "unknown"),
        ("cxx_standard", 3), ("build_configuration", "Debug"),
        ("system_name", "FAKE"), ("system_processor", ""), ("pointer_bits", 0),
    ):
        invalid = copy.deepcopy(valid)
        invalid["build_environment"][field] = value
        assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    invalid = copy.deepcopy(valid)
    del invalid["build_environment"]
    assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    assert not validate_receipt(valid, expected_head=expected, actual_checkout_sha=expected,
                                expected_compiler="MSVC", expected_system="Windows")
    assert validate_receipt(valid, expected_head=expected, actual_checkout_sha=expected,
                            expected_compiler="GNU")
    assert validate_receipt(valid, expected_head=expected, actual_checkout_sha=expected,
                            expected_system="Linux")
    invalid = json.loads(json.dumps(valid))
    invalid["cases"][0]["variants"][0]["full_retrieve_path"] = False
    assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    for field in ("correctness_parity", "index_construction_included", "materialization_checked", "fail_closed_controls_checked", "fixture_engine_setup_excluded"):
        invalid = copy.deepcopy(valid)
        invalid["cases"][0]["variants"][0][field] = False
        assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["execution_orders"] = [[0, 1, 2]] * 12
    assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    invalid = copy.deepcopy(valid)
    invalid["break_even"]["by_metadata_width"][0]["hash_first_tested_count"] = 1000
    assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["variants"][0]["setup_and_retrieve_timing"] = {
        key: ([0.5] * 12 if key == "samples_ms" else 12 if key == "sample_count" else 0.5)
        for key in valid["cases"][0]["variants"][0]["setup_and_retrieve_timing"]
    }
    assert any("nested timing scopes contradict" in error for error in validate_receipt(
        invalid, expected_head=expected, actual_checkout_sha=expected
    ))
    closure_controls = []
    invalid = copy.deepcopy(valid)
    del invalid["cases"][0]["variants"][0]["stage_profile"]
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["variants"][0]["stage_profile"]["primary_performance_evidence"] = True
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["variants"][0]["stage_profile"]["stages"]["trusted_result_copy"]["calls"] = [0] * 3
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["variants"][0]["stage_profile"]["stages"]["admission_total"]["timing"] = fake_timing(0.1)
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["cases"][0]["variants"][0]["stage_profile"]["full_retrieve_timing"] = fake_timing(0.1)
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["negative_workloads"][0]["validated_candidates"] = 10
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["negative_workloads"].pop()
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["sorted_input_cases"][0]["trusted_input_order"] = "fixture_numeric_order"
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["candidate_count_distribution"]["synthetic_case_counts_are_request_frequencies"] = True
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["memory_scope"]["peak_retained_workspace"] = "allocated_bytes"
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["production_default_decision"] = "ADOPT"
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["conditional_dispatch_decision"] = "threshold=500"
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    del invalid["generated_source_sha256"]["ProfiledHashResearchHybridRetrievalEngine.cpp"]
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["source_worktree_clean"] = False
    closure_controls.append(invalid)
    invalid = copy.deepcopy(valid)
    invalid["sorted_input_cases"][0]["mean_ms_by_variant"]["linear_scan"] = 0.01
    closure_controls.append(invalid)
    for invalid in closure_controls:
        assert validate_receipt(invalid, expected_head=expected, actual_checkout_sha=expected)
    receipt_bytes = json.dumps(valid, sort_keys=True).encode("utf-8")
    sidecar = {
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "expected_pr_head_sha": expected,
        "source_commit": expected,
    }
    assert not validate_sidecar(
        sidecar,
        receipt_bytes,
        expected_head=expected,
        actual_checkout_sha=expected,
    )
    sidecar["receipt_sha256"] = "0" * 64
    assert validate_sidecar(
        sidecar,
        receipt_bytes,
        expected_head=expected,
        actual_checkout_sha=expected,
    )
    print(f"production-shaped reranker receipt validator self-test: PASS; closure_controls={len(closure_controls)}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--expected-head")
    parser.add_argument("--actual-checkout-sha")
    parser.add_argument("--expected-compiler", choices=("GNU", "MSVC", "Clang", "AppleClang"))
    parser.add_argument("--expected-system", choices=("Windows", "Linux", "Darwin"))
    parser.add_argument("--write-sidecar", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.receipt or not args.sidecar or not args.expected_head or not args.actual_checkout_sha:
        parser.error("--receipt, --sidecar, --expected-head, and --actual-checkout-sha are required")
    receipt_bytes = args.receipt.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    if args.write_sidecar:
        args.sidecar.write_text(json.dumps({
            "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "source_commit": args.actual_checkout_sha,
            "expected_pr_head_sha": args.expected_head,
        }, indent=2) + "\n", encoding="utf-8")
    sidecar = json.loads(args.sidecar.read_text(encoding="utf-8"))
    try:
        errors = validate_receipt(
            receipt,
            expected_head=args.expected_head,
            actual_checkout_sha=args.actual_checkout_sha,
            expected_compiler=args.expected_compiler,
            expected_system=args.expected_system,
        )
    except (KeyError, TypeError, ValueError) as error:
        errors = [f"malformed receipt: {error}"]
    root = Path(__file__).resolve().parents[2]
    if receipt.get("source_manifest_sha256") != source_manifest_digest(root):
        errors.append("source_manifest_sha256 does not match checked-out harness and production source bytes")
    if receipt.get("fixture_sha256") != hashlib.sha256(FIXTURE_DESCRIPTION.encode("utf-8")).hexdigest():
        errors.append("fixture_sha256 does not match frozen experiment parameters")
    generator = runpy.run_path(str(root / "tools/agent-memory-bench/generate-production-reranker-variants.py"))
    generated = generator["generate"](
        (root / "src/agent_memory/retrieval/HybridRetrievalEngine.cpp").read_text(encoding="utf-8"),
        (root / "src/agent_memory/retrieval/HybridRetrievalEngine.hpp").read_text(encoding="utf-8"),
    )
    generated_digests = {
        name: hashlib.sha256(contents.encode("utf-8")).hexdigest()
        for name, contents in generated.items() if name.endswith(".cpp")
    }
    if receipt.get("generated_source_sha256") != generated_digests:
        errors.append("generated_source_sha256 does not match source-bound transformations")
    errors.extend(
        validate_sidecar(
            sidecar,
            receipt_bytes,
            expected_head=args.expected_head,
            actual_checkout_sha=args.actual_checkout_sha,
        )
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("production-shaped reranker receipt: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
