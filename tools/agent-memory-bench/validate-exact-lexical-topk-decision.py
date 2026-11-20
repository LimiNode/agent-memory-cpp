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
    "tools/agent-memory-bench/lexical_topk_stage_profile.hpp",
    "tools/agent-memory-bench/lexical-topk-stage-profiles.json",
    "src/agent_memory/lexical/ExactLexicalIndex.cpp",
    "src/agent_memory/lexical/ExactLexicalIndex.hpp",
    "src/agent_memory/lexical/Lexical.cpp",
    "src/agent_memory/lexical/Lexical.hpp",
    "src/agent_memory/domain/Metadata.cpp",
    "src/agent_memory/domain/Metadata.hpp",
    "src/agent_memory/domain/MetadataFilter.cpp",
    "src/agent_memory/domain/MetadataFilter.hpp",
    "src/agent_memory/retrieval/HybridRetrievalEngine.cpp",
    "src/agent_memory/retrieval/HybridRetrievalEngine.hpp",
    "src/agent_memory/retrieval/IRetrievalEngine.hpp",
    "src/agent_memory/domain/Sha256.cpp",
    "src/agent_memory/domain/Sha256.hpp",
)
PROFILE_RECIPE = Path(__file__).with_name("lexical-topk-stage-profiles.json")
STAGES = ("candidate_generation", "metadata_filtering", "bm25_scoring", "candidate_construction",
          "top_k_selection", "result_materialization", "full_search")


def source_manifest_sha256(root: Path, schema_version: int = 2) -> str:
    paths = SOURCE_PATHS if schema_version == 2 else tuple(path for path in SOURCE_PATHS if path not in {
        "tools/agent-memory-bench/lexical_topk_stage_profile.hpp",
        "tools/agent-memory-bench/lexical-topk-stage-profiles.json",
        "src/agent_memory/retrieval/HybridRetrievalEngine.cpp",
        "src/agent_memory/retrieval/HybridRetrievalEngine.hpp",
        "src/agent_memory/retrieval/IRetrievalEngine.hpp",
        "src/agent_memory/domain/Sha256.cpp",
        "src/agent_memory/domain/Sha256.hpp",
    })
    manifest = "".join(f"{path}:{hashlib.sha256((root / path).read_bytes()).hexdigest()}\n" for path in paths)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def validate_sidecar(receipt_path: Path, sidecar_path: Path) -> None:
    expected = f"{hashlib.sha256(receipt_path.read_bytes()).hexdigest()}  {receipt_path.name}"
    if sidecar_path.read_text(encoding="utf-8").strip() != expected:
        raise ValueError("receipt SHA-256 sidecar does not match receipt bytes and filename")


def generated_manifest_sha256(root: Path, schema_version: int = 2) -> str:
    spec = importlib.util.spec_from_file_location("lexical_topk_source_generator", root / SOURCE_PATHS[1])
    if spec is None or spec.loader is None:
        raise ValueError("source generator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = [
        (root / "src/agent_memory/lexical/ExactLexicalIndex.cpp").read_text(encoding="utf-8"),
        (root / "src/agent_memory/lexical/ExactLexicalIndex.hpp").read_text(encoding="utf-8"),
    ]
    if schema_version == 2:
        arguments.append((root / "tools/agent-memory-bench/lexical-topk-stage-profiles.json").read_bytes())
    generated = module.generate(*arguments)
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


def _validate_matched(payload: Any, result_count: int, metadata_width: int = 2) -> None:
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
        if production.get("metadata_entries_materialized") != [metadata_width * result_count] * 8:
            raise ValueError(f"{name} search metadata accounting is invalid")


def _profile_candidates(definition: dict[str, Any], is_empty: bool = False) -> int:
    if is_empty:
        return 0
    count = 0
    terms = set(definition["terms"])
    for index in range(definition["document_count"]):
        if index % definition["filter_modulus"] != 0:
            continue
        if definition["ties"]:
            present = {"common"}
        else:
            present = {"common" if index % definition["common_every"] == 0 else "filler",
                       "anchor" if index % definition["anchor_every"] == 0 else "filler"}
            present.update(f"word:{(index + position) % 32}" for position in range(2, definition["tokens_per_document"]))
        count += bool(terms & present)
    return count


def _profile_corpus_sha256(definition: dict[str, Any]) -> str:
    """Independent expansion of the JSON-lines token/metadata/revision corpus."""
    digest = hashlib.sha256()
    for index in range(definition["document_count"]):
        metadata = {"scope": "public" if index % definition["filter_modulus"] == 0 else "private",
                    "tenant": "alpha" if index % 2 == 0 else "beta"}
        metadata.update({f"extra:{field}": "value:" + "m" * 48 for field in range(2, definition["metadata_width"])})
        tokens = []
        for position in range(definition["tokens_per_document"]):
            if definition["ties"]: term = "common"
            elif position == 0: term = "common" if index % definition["common_every"] == 0 else "filler"
            elif position == 1: term = "anchor" if index % definition["anchor_every"] == 0 else "filler"
            else: term = f"word:{(index + position) % 32}"
            tokens.append(term)
        row = {"chunk_id": f"chunk:{index}", "resource_id": "resource:synthetic-profile",
               "revision": [1, 11, 17], "tokens": tokens, "metadata": metadata}
        digest.update((json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _validate_profiles(payload: Any, source_root: Path | None) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("kind") != "SYNTHETIC":
        raise ValueError("stage profile must declare its SYNTHETIC schema")
    recipe_path = PROFILE_RECIPE if source_root is None else source_root / "tools/agent-memory-bench/lexical-topk-stage-profiles.json"
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    if payload.get("fixture_recipe_sha256") != hashlib.sha256(recipe_bytes).hexdigest():
        raise ValueError("stage profile fixture recipe digest differs from committed bytes")
    if payload.get("fixture_hash_scope") != "exact committed recipe bytes; corpus expansion source is independently source-bound":
        raise ValueError("fixture recipe identity must not be represented as a materialized corpus hash")
    if payload.get("workload_distribution") != "NOT_AVAILABLE":
        raise ValueError("synthetic profiles cannot establish actual workload frequencies")
    profiles = payload.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(recipe["profiles"]):
        raise ValueError("stage profile matrix is incomplete")
    for profile, definition in zip(profiles, recipe["profiles"]):
        if not isinstance(profile, dict) or json.dumps(profile.get("fixture"), sort_keys=True) != json.dumps(definition, sort_keys=True):
            raise ValueError("profile does not match the frozen fixture definition")
        if profile.get("materialized_corpus_sha256") != _profile_corpus_sha256(definition):
            raise ValueError("materialized corpus digest differs from independently expanded records")
        cases = profile.get("cases")
        if not isinstance(cases, list) or [case.get("name") for case in cases] != ["small_k", "near_n", "empty"]:
            raise ValueError("profile small/near-N/empty matrix is incomplete")
        candidates = _profile_candidates(definition)
        for case in cases:
            expected_candidates = 0 if case["name"] == "empty" else candidates
            expected_k = max(1, candidates - 1) if case["name"] == "near_n" else 16
            result_count = min(expected_candidates, expected_k)
            if case.get("candidate_count") != expected_candidates or case.get("top_k") != expected_k or case.get("result_count") != result_count:
                raise ValueError("profile candidate/result count or limit differs from independently expanded recipe")
            _validate_matched(case.get("matched_uninstrumented"), result_count, definition["metadata_width"])
            direct = case.get("direct_instrumented")
            if not isinstance(direct, dict) or direct.get("timing_scope") != "DIRECT_INSTRUMENTED_SEARCH_STAGES":
                raise ValueError("profile must use directly measured stages, not inferred timing")
            expected_scopes = {
                "instrumentation": "per-record steady_clock calls; explicit timer overhead; not comparable to uninstrumented latency",
                "candidate_generation_scope": "map iterator dereference/increment; begin and loop-condition overhead unattributed",
                "result_materialization_scope": "final vector truncation/destruction only; metadata copies are candidate_construction",
            }
            if any(direct.get(key) != value for key, value in expected_scopes.items()):
                raise ValueError("profile instrumentation/materialization boundary is not explicit")
            if direct.get("warmups") != 2 or direct.get("repeats") != 8:
                raise ValueError("profile warmup/repeat contract differs")
            _validate_orders(direct, 8, VARIANTS)
            arms = direct.get("variants")
            if not isinstance(arms, dict) or set(arms) != VARIANTS:
                raise ValueError("profile variants are incomplete")
            document_count = definition["document_count"]
            filtered = (document_count + definition["filter_modulus"] - 1) // definition["filter_modulus"]
            calls = dict(zip(STAGES, (document_count, document_count, filtered, expected_candidates, 1, 1, 1)))
            for arm in arms.values():
                if not isinstance(arm, dict) or arm.get("exact_parity") is not True or arm.get("sample_count") != 8 or arm.get("result_count") != result_count:
                    raise ValueError("profile parity/result/sample gate is invalid")
                times = arm.get("elapsed_ns")
                observed_calls = arm.get("stage_calls")
                if not isinstance(times, dict) or set(times) != set(STAGES) or not isinstance(observed_calls, dict) or set(observed_calls) != set(STAGES):
                    raise ValueError("direct stage timing/call set differs")
                for stage in STAGES:
                    _finite_samples(times[stage], f"{stage}.elapsed_ns", 8)
                    if any(type(value) is not int for value in times[stage]) or not isinstance(observed_calls[stage], list) or any(type(value) is not int for value in observed_calls[stage]) or observed_calls[stage] != [calls[stage]] * 8:
                        raise ValueError("stage time units or independently expected call count differs")
                    if calls[stage] == 0 and any(times[stage]):
                        raise ValueError("unexecuted stage has claimed measured work")
                if any(sum(times[stage][sample] for stage in STAGES[:-1]) > times["full_search"][sample] for sample in range(8)):
                    raise ValueError("disjoint direct stages exceed their enclosing full-search interval")


def validate(receipt: Any, *, expected_head: str | None = None, actual_checkout: str | None = None, source_root: Path | None = None) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") not in (1, 2) or receipt.get("status") != "EXECUTED":
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
    if source_root is not None and receipt["source_manifest_sha256"] != source_manifest_sha256(source_root, receipt["schema_version"]):
        raise ValueError("receipt source manifest does not match actual source files")
    _sha(receipt.get("generated_source_manifest_sha256"), "generated_source_manifest_sha256", SHA256)
    if source_root is not None and receipt["generated_source_manifest_sha256"] != generated_manifest_sha256(source_root, receipt["schema_version"]):
        raise ValueError("receipt generated source manifest does not match pinned transformations")
    build = receipt.get("build_identity")
    if not isinstance(build, dict) or not all(isinstance(build.get(key), str) and build[key] for key in ("compiler_id", "compiler_version", "generator", "configuration")):
        raise ValueError("build_identity is incomplete")
    if receipt["schema_version"] == 2:
        if any(build.get(key) in ("unconfigured", "unknown", "") for key in ("compiler_id", "compiler_version", "generator")) or build.get("configuration") != "Release":
            raise ValueError("v2 requires an actual Release compiler/generator identity")
        if not all(isinstance(build.get(key), str) and build[key] and build[key] not in ("unconfigured", "unknown") for key in ("system_name", "system_processor")):
            raise ValueError("v2 requires explicit system and processor identities")
        if type(build.get("cxx_standard")) is not int or build["cxx_standard"] != 201703 or type(build.get("pointer_bits")) is not int or build["pointer_bits"] not in (32, 64):
            raise ValueError("v2 requires actual C++17 and pointer-width evidence")
    for field in ("configured_environment_sha256", "base_cxx_flags_sha256", "active_configuration_flags_sha256"):
        _sha(build.get(field), field, SHA256)
    if receipt.get("oracle") != "score_desc_id_asc":
        raise ValueError("oracle must be score_desc_id_asc")
    if receipt.get("production_api_changed") is not False or receipt.get("production_defaults_changed") is not False:
        raise ValueError("production implementation/defaults must remain unchanged")
    if receipt["schema_version"] == 1:
        if receipt.get("end_to_end_stage_breakdown") != "NOT_MEASURED":
            raise ValueError("historical v1 did not measure intrusive stage timings")
    else:
        if receipt.get("end_to_end_stage_breakdown") != "DIRECT_INSTRUMENTED_SEPARATE_SCOPE":
            raise ValueError("v2 must distinguish instrumented stages from uninstrumented search")
        _validate_profiles(receipt.get("stage_profiles"), source_root)
        boundary = receipt.get("retrieval_boundary")
        if not isinstance(boundary, dict) or boundary.get("hybrid_filter_source_preservation") != "PASS":
            raise ValueError("actual HybridRetrievalEngine filter/source-preservation gate failed")
        if any(boundary.get(field) != "NOT_EXECUTED" for field in ("authorization", "tenant_authority", "revision_freshness")):
            raise ValueError("metadata equality must not claim authoritative product access/freshness")
        if boundary.get("reason") != "HybridRetrievalEngine request has no authorization/pinned-revision context; RevisionBoundRetrieval does not consume ILexicalIndex":
            raise ValueError("unified product boundary limitation must be explicit")
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
        "end_to_end_stage_breakdown": "NOT_MEASURED",
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
    source_bound["source_manifest_sha256"] = source_manifest_sha256(root, 1)
    source_bound["generated_source_manifest_sha256"] = generated_manifest_sha256(root, 1)
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
    profiled = copy.deepcopy(valid)
    profiled["schema_version"] = 2
    profiled["end_to_end_stage_breakdown"] = "DIRECT_INSTRUMENTED_SEPARATE_SCOPE"
    recipe = json.loads(PROFILE_RECIPE.read_bytes())
    profiles = []
    for definition in recipe["profiles"]:
        candidates = _profile_candidates(definition)
        cases = []
        for kind in ("small_k", "near_n", "empty"):
            candidate_count = 0 if kind == "empty" else candidates
            top_k = max(1, candidates - 1) if kind == "near_n" else 16
            result_count = min(candidate_count, top_k)
            matched = copy.deepcopy(valid["cases"][0]["matched_end_to_end"])
            for arm in matched["variants"].values():
                arm["result_count"] = result_count
                arm["metadata_entries_materialized"] = [definition["metadata_width"] * result_count] * 8
            document_count = definition["document_count"]
            filtered = (document_count + definition["filter_modulus"] - 1) // definition["filter_modulus"]
            calls = dict(zip(STAGES, (document_count, document_count, filtered, candidate_count, 1, 1, 1)))
            direct = {
                "timing_scope": "DIRECT_INSTRUMENTED_SEARCH_STAGES",
                "instrumentation": "per-record steady_clock calls; explicit timer overhead; not comparable to uninstrumented latency",
                "candidate_generation_scope": "map iterator dereference/increment; begin and loop-condition overhead unattributed",
                "result_materialization_scope": "final vector truncation/destruction only; metadata copies are candidate_construction",
                "warmups": 2, "repeats": 8,
                "execution_orders": copy.deepcopy(matched["execution_orders"]),
                "variants": {name: {
                    "sample_count": 8, "result_count": result_count, "exact_parity": True,
                    "elapsed_ns": {stage: [100 if stage == "full_search" else 10 if calls[stage] else 0] * 8 for stage in STAGES},
                    "stage_calls": {stage: [calls[stage]] * 8 for stage in STAGES},
                } for name in VARIANTS},
            }
            cases.append({"name": kind, "top_k": top_k, "candidate_count": candidate_count,
                          "result_count": result_count, "matched_uninstrumented": matched, "direct_instrumented": direct})
        profiles.append({"fixture": definition, "materialized_corpus_sha256": _profile_corpus_sha256(definition), "cases": cases})
    profiled["stage_profiles"] = {
        "schema_version": 1, "kind": "SYNTHETIC", "fixture_recipe_sha256": hashlib.sha256(PROFILE_RECIPE.read_bytes()).hexdigest(),
        "fixture_hash_scope": "exact committed recipe bytes; corpus expansion source is independently source-bound",
        "workload_distribution": "NOT_AVAILABLE", "profiles": profiles,
    }
    profiled["retrieval_boundary"] = {
        "hybrid_filter_source_preservation": "PASS", "authorization": "NOT_EXECUTED",
        "tenant_authority": "NOT_EXECUTED", "revision_freshness": "NOT_EXECUTED",
        "reason": "HybridRetrievalEngine request has no authorization/pinned-revision context; RevisionBoundRetrieval does not consume ILexicalIndex",
    }
    profiled["source_manifest_sha256"] = source_manifest_sha256(root)
    profiled["generated_source_manifest_sha256"] = generated_manifest_sha256(root)
    profiled["build_identity"].update({"system_name": "Linux", "system_processor": "x86_64", "cxx_standard": 201703, "pointer_bits": 64})
    validate(profiled, source_root=root, **binding)
    profile_mutations = ("missing stage", "wrong calls", "overlap", "nonfinite stage", "wrong kind", "wrong recipe",
                         "wrong scope", "missing arm", "profile parity", "security promotion", "boundary failure", "unstable distribution", "corpus drift", "boolean calls",
                         "missing platform", "wrong standard", "wrong pointer width", "debug timing", "unconfigured compiler")
    for mutation in profile_mutations:
        candidate = copy.deepcopy(profiled)
        profile = candidate["stage_profiles"]
        direct = profile["profiles"][0]["cases"][0]["direct_instrumented"]
        arm = direct["variants"]["full_sort"]
        if mutation == "missing stage": del arm["elapsed_ns"]["bm25_scoring"]
        elif mutation == "wrong calls": arm["stage_calls"]["bm25_scoring"][0] += 1
        elif mutation == "overlap": arm["elapsed_ns"]["top_k_selection"][0] = 1000
        elif mutation == "nonfinite stage": arm["elapsed_ns"]["bm25_scoring"][0] = float("nan")
        elif mutation == "wrong kind": profile["kind"] = "REPRESENTATIVE"
        elif mutation == "wrong recipe": profile["fixture_recipe_sha256"] = "0" * 64
        elif mutation == "wrong scope": direct["timing_scope"] = "inferred by subtraction"
        elif mutation == "missing arm": del direct["variants"]["bounded_heap"]
        elif mutation == "profile parity": arm["exact_parity"] = False
        elif mutation == "security promotion": candidate["retrieval_boundary"]["authorization"] = "PASS"
        elif mutation == "boundary failure": candidate["retrieval_boundary"]["hybrid_filter_source_preservation"] = "FAIL"
        elif mutation == "unstable distribution": profile["workload_distribution"] = "production"
        elif mutation == "corpus drift": profile["profiles"][0]["materialized_corpus_sha256"] = "0" * 64
        elif mutation == "boolean calls": arm["stage_calls"]["full_search"][0] = True
        elif mutation == "missing platform": del candidate["build_identity"]["system_name"]
        elif mutation == "wrong standard": candidate["build_identity"]["cxx_standard"] = 202002
        elif mutation == "wrong pointer width": candidate["build_identity"]["pointer_bits"] = True
        elif mutation == "debug timing": candidate["build_identity"]["configuration"] = "Debug"
        elif mutation == "unconfigured compiler": candidate["build_identity"]["compiler_id"] = "unconfigured"
        try:
            validate(candidate, source_root=root, **binding)
        except ValueError:
            pass
        else:
            raise AssertionError(f"profile mutation accepted: {mutation}")
    print(f"exact lexical top-k decision validator self-test: PASS ({len(mutations) + 3 + len(profile_mutations)} negative controls)")


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
