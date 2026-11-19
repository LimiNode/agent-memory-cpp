#!/usr/bin/env python3
"""Fail-closed validator for the production-shaped lexical top-k receipt."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any


SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_COUNTS = {128, 1024, 8192, 65536}
VARIANTS = {"full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"}
SOURCE_PATHS = (
    "tools/agent-memory-bench/exact_lexical_topk_decision.cpp",
    "tools/agent-memory-bench/generate-exact-lexical-topk-variants.py",
    "tools/agent-memory-bench/validate-exact-lexical-topk-decision.py",
    "src/agent_memory/lexical/ExactLexicalIndex.cpp",
    "src/agent_memory/lexical/ExactLexicalIndex.hpp",
    "src/agent_memory/lexical/Lexical.cpp",
    "src/agent_memory/lexical/Lexical.hpp",
    "src/agent_memory/domain/Metadata.cpp",
    "src/agent_memory/domain/Metadata.hpp",
    "src/agent_memory/domain/MetadataFilter.cpp",
    "src/agent_memory/domain/MetadataFilter.hpp",
)


def source_manifest_sha256(root: Path) -> str:
    manifest = "".join(f"{path}:{hashlib.sha256((root / path).read_bytes()).hexdigest()}\n" for path in SOURCE_PATHS)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def validate_sidecar(receipt_path: Path, sidecar_path: Path) -> None:
    expected = f"{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}  {receipt_path.name}"
    if sidecar_path.read_text(encoding="utf-8").strip() != expected:
        raise ValueError("receipt SHA-256 sidecar does not match receipt bytes and filename")


def generated_manifest_sha256(root: Path) -> str:
    spec = importlib.util.spec_from_file_location("lexical_topk_source_generator", root / SOURCE_PATHS[1])
    if spec is None or spec.loader is None:
        raise ValueError("source generator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = module.generate(
        (root / "src/agent_memory/lexical/ExactLexicalIndex.cpp").read_text(encoding="utf-8"),
        (root / "src/agent_memory/lexical/ExactLexicalIndex.hpp").read_text(encoding="utf-8"),
    )
    return module.generated_manifest_sha256(generated)


def _sha(value: Any, label: str, pattern: re.Pattern[str] = SHA1) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase hexadecimal digest")


def _finite_samples(value: Any, label: str, expected_count: int) -> None:
    if not isinstance(value, list) or len(value) != expected_count:
        raise ValueError(f"{label} must contain {expected_count} samples")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)) or float(item) < 0.0 for item in value):
        raise ValueError(f"{label} must contain finite non-negative samples")


def _parallel_counts(payload: dict[str, Any], prefix: str, count: int) -> None:
    for field in ("allocation_counts", "allocated_bytes"):
        value = payload.get(field)
        if not isinstance(value, list) or len(value) != count:
            raise ValueError(f"{prefix}.{field} must match sample_count")
        if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value):
            raise ValueError(f"{prefix}.{field} must contain non-negative integers")


def _validate_orders(payload: dict[str, Any], expected_count: int, expected_names: set[str]) -> None:
    orders = payload.get("execution_orders")
    if not isinstance(orders, list) or len(orders) != expected_count:
        raise ValueError("kernel execution_orders must contain one order per repeat")
    for repeat, order in enumerate(orders):
        if repeat == 0:
            if not isinstance(order, list) or set(order) != expected_names or len(order) != len(expected_names):
                raise ValueError("kernel first execution order is invalid")
        else:
            expected = orders[0][repeat % len(orders[0]):] + orders[0][:repeat % len(orders[0])]
            if order != expected:
                raise ValueError("kernel execution order is not cyclically balanced")


def _validate_kernel(kernel: Any, expected_count: int, expected_names: set[str]) -> None:
    if not isinstance(kernel, dict):
        raise ValueError("kernel_only must be an object")
    if kernel.get("timing_scope") != "kernel-only selection over filtered scored LexicalSearchResult candidates; no scoring/filter/I/O":
        raise ValueError("kernel timing scope is not explicit")
    if kernel.get("warmups") != 2 or kernel.get("repeats") != expected_count:
        raise ValueError("kernel warmup/repeat contract is invalid")
    variants = kernel.get("variants")
    if not isinstance(variants, dict) or set(variants) != expected_names:
        raise ValueError("kernel variant set is incomplete")
    _validate_orders(kernel, expected_count, expected_names)
    for name, variant in variants.items():
        if not isinstance(variant, dict) or variant.get("exact_parity") is not True:
            raise ValueError(f"{name} lacks an exact parity gate")
        if variant.get("sample_count") != expected_count:
            raise ValueError(f"{name}.sample_count is invalid")
        _finite_samples(variant.get("samples_ms"), f"{name}.samples_ms", expected_count)
        _parallel_counts(variant, name, expected_count)
        for field in ("copy_counts", "move_counts"):
            values = variant.get(field)
            if not isinstance(values, list) or len(values) != expected_count:
                raise ValueError(f"{name}.{field} must match sample_count")
            if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in values):
                raise ValueError(f"{name}.{field} must contain non-negative integers")


def _validate_matched(payload: Any, result_count: int) -> None:
    if not isinstance(payload, dict) or payload.get("warmups") != 2:
        raise ValueError("matched end-to-end scope/warmups are missing")
    if payload.get("timing_scope") != "matched end_to_end ExactLexicalIndex::search; identical scoring/filter/metadata; selection block only differs; no I/O":
        raise ValueError("matched end-to-end timing scope is invalid")
    variants = payload.get("variants")
    if not isinstance(variants, dict) or set(variants) != VARIANTS:
        raise ValueError("matched end-to-end variants are incomplete")
    _validate_orders(payload, 8, VARIANTS)
    for name, production in variants.items():
        if not isinstance(production, dict) or production.get("exact_parity") is not True or production.get("warmups") != 2:
            raise ValueError(f"{name} search lacks exact parity/warmup gate")
        if production.get("result_count") != result_count or production.get("sample_count") != 8:
            raise ValueError(f"{name} search result/sample count is invalid")
        _finite_samples(production.get("samples_ms"), f"{name} search samples_ms", 8)
        _parallel_counts(production, f"{name} search", 8)
        if production.get("metadata_entries_materialized") != [2 * result_count] * 8:
            raise ValueError(f"{name} search metadata accounting is invalid")


def validate(receipt: Any, *, expected_head: str | None = None, actual_checkout: str | None = None, source_root: Path | None = None) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    if receipt.get("schema_version") != 1 or receipt.get("status") != "EXECUTED":
        raise ValueError("receipt schema/status is invalid")
    if receipt.get("benchmark_name") != "production_shaped_exact_lexical_topk_decision":
        raise ValueError("unexpected benchmark name")
    _sha(receipt.get("source_commit"), "source_commit")
    _sha(receipt.get("actual_checkout_sha"), "actual_checkout_sha")
    _sha(receipt.get("expected_pr_head_sha"), "expected_pr_head_sha")
    if receipt["source_commit"] != receipt["actual_checkout_sha"]:
        raise ValueError("source_commit and actual_checkout_sha must match")
    if expected_head is not None:
        _sha(expected_head, "expected head")
        if receipt["expected_pr_head_sha"] != expected_head:
            raise ValueError("receipt expected_pr_head_sha does not match CI")
    if actual_checkout is not None:
        _sha(actual_checkout, "actual checkout")
        if receipt["actual_checkout_sha"] != actual_checkout:
            raise ValueError("receipt actual_checkout_sha does not match checkout")
    _sha(receipt.get("source_manifest_sha256"), "source_manifest_sha256", SHA256)
    if source_root is not None and receipt["source_manifest_sha256"] != source_manifest_sha256(source_root):
        raise ValueError("receipt source manifest does not match actual source files")
    _sha(receipt.get("generated_source_manifest_sha256"), "generated_source_manifest_sha256", SHA256)
    if source_root is not None and receipt["generated_source_manifest_sha256"] != generated_manifest_sha256(source_root):
        raise ValueError("receipt generated source manifest does not match pinned transformations")
    build = receipt.get("build_identity")
    if not isinstance(build, dict) or not all(isinstance(build.get(key), str) and build[key] for key in ("compiler_id", "compiler_version", "generator", "configuration")):
        raise ValueError("build_identity is incomplete")
    for field in ("configured_environment_sha256", "base_cxx_flags_sha256", "active_configuration_flags_sha256"):
        _sha(build.get(field), field, SHA256)
    if receipt.get("oracle") != "score_desc_id_asc":
        raise ValueError("oracle must be score_desc_id_asc")
    if receipt.get("production_api_changed") is not False or receipt.get("production_defaults_changed") is not False:
        raise ValueError("production implementation/defaults must remain unchanged")
    fixture = receipt.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("io_scope") != "in_memory_no_io" or fixture.get("metadata_and_filter_included") is not True:
        raise ValueError("fixture must declare metadata/filter and no-I/O scope")
    if any(fixture.get(field) != "NOT_EXERCISED" for field in ("authorization", "tenant_isolation", "resource_revision_freshness")):
        raise ValueError("the benchmark must not claim authorization, tenant isolation, or revision freshness")
    counts = fixture.get("document_counts")
    if not isinstance(counts, list) or any(type(count) is not int for count in counts) or len(counts) != 4 or set(counts) != EXPECTED_COUNTS:
        raise ValueError("fixture document counts are incomplete")
    cases = receipt.get("cases")
    if not isinstance(cases, list) or len(cases) != len(EXPECTED_COUNTS) * 4:
        raise ValueError("receipt must contain four cases for each document count")
    seen: set[tuple[int, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        count = case.get("document_count")
        name = case.get("name")
        if type(count) is not int or count not in EXPECTED_COUNTS or not isinstance(name, str):
            raise ValueError("case identity is invalid")
        key = (count, name)
        if key in seen:
            raise ValueError("duplicate case")
        seen.add(key)
        if case.get("oracle") != "score_desc_id_asc" or case.get("io_scope") != "in_memory ExactLexicalIndex; I/O bytes are zero":
            raise ValueError("case contract scope is invalid")
        public_count = count - (count + 4) // 5
        candidates = 0 if name == "empty_result" else public_count
        limit = 0 if name == "top_k_zero" else public_count - 1 if name == "ties_k_near_n" else 16
        if case.get("candidate_count") != candidates or case.get("top_k") != limit:
            raise ValueError("case candidate count/top-k does not match the frozen fixture")
        if case.get("candidate_order") != "chunk_id_asc_pre_selection":
            raise ValueError("kernel candidates must use production pre-selection order")
        if case.get("metadata_filters") != [{"key": "scope", "value": "public"}]:
            raise ValueError("case metadata filter is invalid")
        result_count = min(candidates, limit)
        _validate_matched(case.get("matched_end_to_end"), result_count)
        _validate_kernel(case.get("kernel_only"), 8, VARIANTS)
    if seen != {(count, name) for count in EXPECTED_COUNTS for name in ("mixed_top_k", "ties_k_near_n", "empty_result", "top_k_zero")}:
        raise ValueError("required edge cases are missing")
    builds = receipt.get("builds")
    if not isinstance(builds, list) or len(builds) != 16 or {(item.get("document_count"), item.get("variant")) for item in builds if isinstance(item, dict)} != {(count, variant) for count in EXPECTED_COUNTS for variant in VARIANTS}:
        raise ValueError("build measurements are incomplete")
    for build in builds:
        _finite_samples([build.get("build_ms")], "build_ms", 1)
        if any(type(build.get(field)) is not int or build[field] < 0 for field in ("allocation_count", "allocated_bytes")):
            raise ValueError("build allocation measurement is invalid")


def _valid_fixture() -> dict[str, Any]:
    variants = {
        name: {
            "samples_ms": [1.0] * 8,
            "sample_count": 8,
            "allocation_counts": [1] * 8,
            "allocated_bytes": [8] * 8,
            "copy_counts": [1] * 8,
            "move_counts": [1] * 8,
            "exact_parity": True,
        }
        for name in VARIANTS
    }
    kernel = {
        "timing_scope": "kernel-only selection over filtered scored LexicalSearchResult candidates; no scoring/filter/I/O",
        "warmups": 2,
        "repeats": 8,
        "execution_orders": [
            ["full_sort", "partial_sort", "nth_element_then_sort", "bounded_heap"],
            ["partial_sort", "nth_element_then_sort", "bounded_heap", "full_sort"],
            ["nth_element_then_sort", "bounded_heap", "full_sort", "partial_sort"],
            ["bounded_heap", "full_sort", "partial_sort", "nth_element_then_sort"],
        ] * 2,
        "variants": variants,
    }
    cases = []
    for count in EXPECTED_COUNTS:
        for name in ("mixed_top_k", "ties_k_near_n", "empty_result", "top_k_zero"):
            public_count = count - (count + 4) // 5
            candidates = 0 if name == "empty_result" else public_count
            limit = 0 if name == "top_k_zero" else public_count - 1 if name == "ties_k_near_n" else 16
            result_count = min(candidates, limit)
            cases.append({
                "name": name,
                "document_count": count,
                "candidate_count": candidates,
                "candidate_order": "chunk_id_asc_pre_selection",
                "metadata_filters": [{"key": "scope", "value": "public"}],
                "top_k": limit,
                "oracle": "score_desc_id_asc",
                "io_scope": "in_memory ExactLexicalIndex; I/O bytes are zero",
                "matched_end_to_end": {"warmups": 2, "timing_scope": "matched end_to_end ExactLexicalIndex::search; identical scoring/filter/metadata; selection block only differs; no I/O", "execution_orders": copy.deepcopy(kernel["execution_orders"]), "variants": {variant: {
                    "samples_ms": [1.0] * 8,
                    "sample_count": 8,
                    "allocation_counts": [1] * 8,
                    "allocated_bytes": [8] * 8,
                    "metadata_entries_materialized": [2 * result_count] * 8,
                    "result_count": result_count,
                    "exact_parity": True,
                    "warmups": 2,
                } for variant in VARIANTS}},
                "kernel_only": copy.deepcopy(kernel),
            })
    return {
        "schema_version": 1,
        "benchmark_name": "production_shaped_exact_lexical_topk_decision",
        "status": "EXECUTED",
        "source_commit": "a" * 40,
        "actual_checkout_sha": "a" * 40,
        "expected_pr_head_sha": "b" * 40,
        "source_manifest_sha256": "c" * 64,
        "generated_source_manifest_sha256": "e" * 64,
        "build_identity": {"compiler_id": "GNU", "compiler_version": "14", "generator": "Ninja", "configuration": "Release", "configured_environment_sha256": "d" * 64, "base_cxx_flags_sha256": "d" * 64, "active_configuration_flags_sha256": "d" * 64},
        "fixture": {"io_scope": "in_memory_no_io", "metadata_and_filter_included": True, "document_counts": list(EXPECTED_COUNTS), "authorization": "NOT_EXERCISED", "tenant_isolation": "NOT_EXERCISED", "resource_revision_freshness": "NOT_EXERCISED"},
        "oracle": "score_desc_id_asc",
        "production_api_changed": False,
        "production_defaults_changed": False,
        "cases": cases,
        "builds": [{"document_count": count, "variant": variant, "build_ms": 1.0, "allocation_count": 1, "allocated_bytes": 8} for count in EXPECTED_COUNTS for variant in VARIANTS],
    }


def self_test() -> None:
    valid = _valid_fixture()
    binding = {"expected_head": "b" * 40, "actual_checkout": "a" * 40}
    validate(valid, **binding)
    mutations = []
    for field, value in (("expected_pr_head_sha", "f" * 40), ("actual_checkout_sha", "f" * 40), ("source_manifest_sha256", "fake"), ("status", "NOT_EXECUTED")):
        mutations.append((field, {**valid, field: value}))
    for name in ("production parity", "kernel parity", "sample count", "nonfinite", "missing variant", "unbalanced order", "wrong limit", "metadata", "end-to-end variant", "end-to-end order", "end-to-end parity"):
        candidate = copy.deepcopy(valid)
        case = candidate["cases"][0]
        production = case["matched_end_to_end"]["variants"]["full_sort"]
        kernel = case["kernel_only"]
        if name == "production parity": production["exact_parity"] = False
        elif name == "kernel parity": kernel["variants"]["bounded_heap"]["exact_parity"] = False
        elif name == "sample count": production["samples_ms"].pop()
        elif name == "nonfinite": production["samples_ms"][0] = float("nan")
        elif name == "missing variant": del kernel["variants"]["bounded_heap"]
        elif name == "unbalanced order": kernel["execution_orders"][1] = kernel["execution_orders"][0]
        elif name == "wrong limit": case["top_k"] = -1
        elif name == "metadata": production["metadata_entries_materialized"][0] += 1
        elif name == "end-to-end variant": del case["matched_end_to_end"]["variants"]["bounded_heap"]
        elif name == "end-to-end order": case["matched_end_to_end"]["execution_orders"][1] = case["matched_end_to_end"]["execution_orders"][0]
        elif name == "end-to-end parity": case["matched_end_to_end"]["variants"]["bounded_heap"]["exact_parity"] = False
        mutations.append((name, candidate))
    for name, candidate in mutations:
        try:
            validate(candidate, **binding)
        except ValueError:
            continue
        raise AssertionError(f"mutation accepted: {name}")
    root = Path(__file__).resolve().parents[2]
    source_bound = copy.deepcopy(valid)
    source_bound["source_manifest_sha256"] = source_manifest_sha256(root)
    source_bound["generated_source_manifest_sha256"] = generated_manifest_sha256(root)
    validate(source_bound, source_root=root, **binding)
    for field in ("source_manifest_sha256", "generated_source_manifest_sha256"):
        candidate = {**source_bound, field: "0" * 64}
        try:
            validate(candidate, source_root=root, **binding)
        except ValueError:
            pass
        else:
            raise AssertionError(f"wrong source binding accepted: {field}")
    with tempfile.TemporaryDirectory() as directory:
        receipt_path = Path(directory) / "receipt.json"
        sidecar_path = Path(directory) / "receipt.json.sha256"
        receipt_path.write_bytes(b"{}\n")
        sidecar_path.write_text(f"{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}  receipt.json\n", encoding="utf-8")
        validate_sidecar(receipt_path, sidecar_path)
        receipt_path.write_bytes(b"[]\n")
        try:
            validate_sidecar(receipt_path, sidecar_path)
        except ValueError:
            pass
        else:
            raise AssertionError("damaged receipt sidecar was accepted")
    print(f"exact lexical top-k decision validator self-test: PASS ({len(mutations) + 3} negative controls)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("--expected-head")
    parser.add_argument("--actual-checkout")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--sha256-sidecar", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.receipt is None:
        parser.error("receipt is required unless --self-test is used")
    try:
        if args.sha256_sidecar is not None:
            validate_sidecar(args.receipt, args.sha256_sidecar)
        validate(json.loads(args.receipt.read_text(encoding="utf-8")), expected_head=args.expected_head, actual_checkout=args.actual_checkout, source_root=args.source_root)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"validation failed: {error}")
        return 1
    print("exact lexical top-k decision receipt: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
