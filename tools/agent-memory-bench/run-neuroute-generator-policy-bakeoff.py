#!/usr/bin/env python3
"""Compare the frozen generator studies under one product policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                       sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    name = "run-neuroute-generator-policy-bakeoff.py"
    return {name: sha256(THIS / name)}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_generator_policy_bakeoff" and
            value["shortlist_budgets"] == [1024, 2048, 4096] and
            value["excluded_budget"] == 8192 and
            value["product_policy"]["global_fp32_k8_role"] ==
            "offline_teacher_and_reference_only" and
            value["decision"]["production_selection_forbidden"] is True,
            "generator policy contract differs")
    return value


def load_bound(result_path: Path, evidence_path: Path, result_family: str,
               evidence_family: str) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    require(result.get("family") == result_family and
            evidence.get("family") == evidence_family and
            evidence.get("result_sha256") == sha256(result_path) and
            evidence.get("result_binding_and_decision_validation_passed") is True
            and evidence.get("production_licensed") is False,
            f"generator policy parent differs: {result_path.name}")
    return result, evidence


def policy_role(point: str) -> tuple[str, bool, str | None]:
    if point.startswith("fixed_top_m_control-"):
        return "historical_fixed_top_m", False, "historical_control_only"
    if point.startswith("faiss_prototype_"):
        return "prototype_ann", False, "global_k8_prototype_control_only"
    if point.startswith("independent_"):
        return "independent_width_association", False, "diagnostic_only"
    if point.startswith("exact_address_k1-"):
        return "address_k1", True, None
    if point.startswith("faiss_address_"):
        return "address_ann", True, None
    if point.startswith("direct_rank64_global-"):
        return "learned_direct", True, None
    if point.startswith("centroid_k1_plus_"):
        return "learned_k1_residual", True, None
    if point.startswith("direct_same_head_16bit_logits-"):
        return "frozen_direct16", True, None
    if point.startswith("same_head_prefix"):
        return "frozen_same_head_hierarchy", True, None
    raise RuntimeError(f"unclassified generator policy point: {point}")


def passes(row: dict[str, Any], gates: dict[str, Any]) -> bool:
    return (row["mean_ndcg_loss"] <= gates[
                "maximum_mean_downstream_ndcg_loss"] and
            row["maximum_stratum_mean_ndcg_loss"] <= gates[
                "maximum_every_seed_downstream_ndcg_loss"] and
            row["mean_final_top10_overlap"] >= gates[
                "minimum_mean_final_top10_overlap"] and
            row["mean_candidate_reference_retention"] >= gates[
                "minimum_mean_fp32_stage_retention_at_candidate"] and
            row["mean_hamming_reference_overlap"] >= gates[
                "minimum_mean_fp32_stage_overlap_at_hamming768"] and
            row["mean_adc_reference_overlap"] >= gates[
                "minimum_mean_fp32_stage_overlap_at_adc64"] and
            int(row["address_budget"]) <= gates["maximum_native_address_budget"])


def gate_distance(row: dict[str, Any], gates: dict[str, Any]) -> float:
    return (max(0.0, row["mean_ndcg_loss"] - gates[
                "maximum_mean_downstream_ndcg_loss"]) +
            max(0.0, row["maximum_stratum_mean_ndcg_loss"] - gates[
                "maximum_every_seed_downstream_ndcg_loss"]) +
            max(0.0, gates["minimum_mean_final_top10_overlap"] - row[
                "mean_final_top10_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_retention_at_candidate"] -
                row["mean_candidate_reference_retention"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_hamming768"] -
                row["mean_hamming_reference_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_adc64"] - row[
                "mean_adc_reference_overlap"]))


def compact_point(source: str, row: dict[str, Any], gates: dict[str, Any]
                  ) -> dict[str, Any]:
    role, eligible, exclusion = policy_role(row["id"])
    diagnostics = row.get("offline_router_diagnostics") or {}
    generator_ms = float(diagnostics["directional_generator_ms_per_query"])
    local_k8_ms = float(row["coarse_ms"]["p95"])
    result = {"source": source, "id": row["id"], "role": role,
        "address_budget": int(row["address_budget"]),
        "product_eligible": eligible, "exclusion_reason": exclusion,
        "passes_common_quality_gate": passes(row, gates),
        "parent_passes_registered_gate": bool(row["passes_registered_gate"]),
        "gate_distance": gate_distance(row, gates),
        "mean_ndcg_loss": float(row["mean_ndcg_loss"]),
        "maximum_stratum_mean_ndcg_loss": float(
            row["maximum_stratum_mean_ndcg_loss"]),
        "mean_final_top10_overlap": float(row["mean_final_top10_overlap"]),
        "mean_candidate_reference_retention": float(
            row["mean_candidate_reference_retention"]),
        "mean_hamming_reference_overlap": float(
            row["mean_hamming_reference_overlap"]),
        "mean_adc_reference_overlap": float(row["mean_adc_reference_overlap"]),
        "directional_generator_ms_per_query": generator_ms,
        "directional_local_k8_p95_ms": local_k8_ms,
        "directional_generator_plus_local_k8_ms": generator_ms + local_k8_ms,
        "mean_generator_model_or_index_bytes": diagnostics.get(
            "mean_generator_model_or_index_bytes"),
        "mean_prefixes_scored": diagnostics.get("mean_prefixes_scored"),
        "mean_fine_addresses_scored": diagnostics.get(
            "mean_fine_addresses_scored"),
        "mean_k8_prototypes_scored": diagnostics.get(
            "mean_k8_prototypes_scored")}
    require(all(math.isfinite(float(result[key])) for key in (
        "gate_distance", "mean_ndcg_loss", "maximum_stratum_mean_ndcg_loss",
        "mean_final_top10_overlap", "directional_generator_ms_per_query",
        "directional_local_k8_p95_ms")),
        "generator policy point contains a non-finite metric")
    return result


def build_result(contract_path: Path, bindings: dict[str, tuple[Path, Path]]
                 ) -> dict[str, Any]:
    contract = load_contract(contract_path)
    expected = contract["parent_families"]
    families = {"generator": (expected["generator"],
            "neuroute_shortlist_generator_bakeoff_evidence"),
        "training": (expected["training"],
            "neuroute_training_sufficient_router_frontier_evidence"),
        "hierarchy": (expected["hierarchy"],
            "neuroute_12_14_to_16_hierarchy_replay_evidence")}
    parents = {}
    input_bindings = {}
    for name, (result_path, evidence_path) in bindings.items():
        parents[name] = load_bound(result_path, evidence_path, *families[name])[0]
        input_bindings[name] = {"result_sha256": sha256(result_path),
                                "evidence_sha256": sha256(evidence_path)}
    shared_keys = ("configuration_protocol_sha256", "layout_manifest_sha256",
                   "k8_manifest_sha256", "authoritative_e5_receipt")
    for key in shared_keys:
        values = [canonical(value["inputs"][key]) for value in parents.values()]
        require(len(set(values)) == 1,
                f"generator policy parent input differs: {key}")
    references = [next(row for row in value["configuration"]
                       if row["id"] == "global_fp32_k8") for value in
                  parents.values()]
    quality_fields = ("mean_ndcg_loss", "maximum_stratum_mean_ndcg_loss",
        "mean_final_top10_overlap", "mean_candidate_reference_retention",
        "mean_hamming_reference_overlap", "mean_adc_reference_overlap")
    require(all(all(row[key] == references[0][key] for key in quality_fields)
                for row in references[1:]),
            "generator policy global reference quality differs")
    budgets = set(map(int, contract["shortlist_budgets"]))
    gates = contract["quality_gates"]
    points = []
    for source, parent in parents.items():
        for row in parent["configuration"]:
            if row.get("address_budget") not in budgets:
                continue
            if source == "training" and row["id"].startswith(
                    "centroid_k1_control-"):
                continue
            points.append(compact_point(source, row, gates))
    points.sort(key=lambda row: (row["address_budget"], row["role"], row["id"]))
    eligible = [row for row in points if row["product_eligible"]]
    passing = [row for row in eligible if row["passes_common_quality_gate"]]
    controls = [row for row in points if not row["product_eligible"] and
                row["passes_common_quality_gate"]]
    at_maximum = [row for row in eligible if row["address_budget"] ==
                  gates["maximum_native_address_budget"]]
    best_near_miss = min(at_maximum, key=lambda row: (row["gate_distance"],
        row["directional_generator_plus_local_k8_ms"], row["id"]))
    prototype_controls = [row for row in controls if row["role"] ==
                          "prototype_ann"]
    quality_control = min(prototype_controls, key=lambda row: (
        row["directional_generator_plus_local_k8_ms"], row["id"]))
    return {"schema_version": 1,
        "family": "neuroute_generator_policy_bakeoff_result",
        "inputs": {"contract_sha256": sha256(contract_path),
            "parents": input_bindings, "source_files_sha256": source_hashes(),
            "shared_configuration_protocol_sha256": parents["generator"][
                "inputs"]["configuration_protocol_sha256"],
            "shared_layout_manifest_sha256": parents["generator"]["inputs"][
                "layout_manifest_sha256"],
            "shared_k8_manifest_sha256": parents["generator"]["inputs"][
                "k8_manifest_sha256"],
            "parent_native_executable_sha256": {name: value["inputs"][
                "native_executable_sha256"] for name, value in parents.items()}},
        "configuration_points": points,
        "decision": {"global_fp32_k8_role":
                "offline_teacher_and_reference_only",
            "product_budget_maximum": 4096,
            "quality_control": quality_control,
            "best_product_near_miss": best_near_miss,
            "passing_product_candidates": passing,
            "cheap_selector_passed": bool(passing),
            "adaptive_prefix_training_required": not bool(passing),
            "native_integration_licensed": False,
            "production_licensed": False}}


def self_test() -> None:
    contract = load_contract(THIS /
        "neuroute-generator-policy-bakeoff.example.json")
    require(policy_role("faiss_prototype_ivf-m4096") == (
        "prototype_ann", False, "global_k8_prototype_control_only") and
        policy_role("same_head_prefix12_beam_4x-m4096")[1] is True and
        contract["shortlist_budgets"] == [1024, 2048, 4096],
        "generator policy self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-generator-policy-bakeoff.example.json")
    for name in ("generator-result", "generator-evidence", "training-result",
                 "training-evidence", "hierarchy-result", "hierarchy-evidence",
                 "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("generator_result", "generator_evidence", "training_result",
        "training_evidence", "hierarchy_result", "hierarchy_evidence", "output")
    require(all(getattr(args, name) is not None for name in required),
            "generator policy inputs are required")
    bindings = {"generator": (args.generator_result, args.generator_evidence),
        "training": (args.training_result, args.training_evidence),
        "hierarchy": (args.hierarchy_result, args.hierarchy_evidence)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(build_result(args.contract, bindings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
