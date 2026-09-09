#!/usr/bin/env python3
"""Validate and summarize the shortlist-generator bake-off."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def protocol_closure(path: Path) -> dict[str, str]:
    pending = [path.resolve()]
    seen: set[Path] = set()
    result: dict[str, str] = {}
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        result[str(current)] = sha256(current)
        value = json.loads(current.read_text(encoding="utf-8"))
        for field in ("routing_kernel_protocol", "parent_protocol",
                      "final_int8_layout_manifest", "coarse_k8_manifest"):
            if field in value:
                pending.append(Path(value[field]).resolve())
        for field in ("native_input_manifest", "document_id_rank_file",
                      "evaluation_document_ids", "evaluation_query_ids",
                      "evaluation_qrels"):
            if field in value:
                payload = Path(value[field]).resolve()
                result[str(payload)] = sha256(payload)
    return dict(sorted(result.items()))


def source_hashes(root: Path) -> dict[str, str]:
    names = ("run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(root / name) for name in names}


def passes(row: dict[str, Any], gates: dict[str, Any]) -> bool:
    return bool(row["mean_ndcg_loss"] <= gates[
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
            "minimum_mean_fp32_stage_overlap_at_adc64"])


def distance(row: dict[str, Any], gates: dict[str, Any]) -> float:
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
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_adc64"] -
                row["mean_adc_reference_overlap"]))


def validate_manifest(binding: dict[str, Any]) -> None:
    path = Path(binding["path"])
    require(path.is_absolute() and sha256(path) == binding["sha256"],
            "shortlist-generator manifest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") ==
            "neuroute_local_k8_address_shortlist_materialization",
            "shortlist-generator manifest family differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        require(payload.stat().st_size == row["bytes"] and
                sha256(payload) == row["sha256"],
                "shortlist-generator payload differs")


def validate_input_payloads(layout_path: Path, k8_path: Path) -> None:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    for seed in layout["seeds"]:
        root = layout_path.parent / f"seed-{seed['seed']}"
        by_role = {row["role"]: row for row in seed["mappings"]}
        for role in ("occupied_addresses", "address_counts", "query_vectors",
                     "shortlist_rows"):
            descriptor = by_role[role]
            path = root / descriptor["file"]
            require(path.stat().st_size == descriptor["bytes"] and
                    sha256(path) == descriptor["sha256"],
                    f"shortlist-generator layout payload differs: {role}")
    k8 = json.loads(k8_path.read_text(encoding="utf-8"))
    for seed in k8["seeds"]:
        path = Path(seed["path"])
        require(path.is_absolute() and path.stat().st_size == seed["bytes"] and
                sha256(path) == seed["sha256"],
                "shortlist-generator K8 payload differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--fixed-router-result", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(sha256(Path(__file__)).isalnum(),
                "shortlist-generator evidence self-test failed")
        return 0
    require(all(getattr(args, name) is not None for name in (
        "contract", "result", "fixed_router_result", "layout_manifest",
        "k8_manifest", "configuration_protocol", "native_executable", "output")),
        "shortlist-generator evidence inputs are required")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    fixed_result = json.loads(args.fixed_router_result.read_text(
        encoding="utf-8"))
    require(result.get("family") ==
            "neuroute_shortlist_generator_bakeoff_result",
            "shortlist-generator result family differs")
    require(result.get("claim_scope") == contract["claim_scope"] and
            isinstance(result.get("faiss_threads"), int) and
            result["faiss_threads"] > 0 and
            isinstance(result.get("inputs", {}).get("faiss_version"), str),
            "shortlist-generator result metadata differs")
    expected = {"contract_sha256": sha256(args.contract),
        "fixed_router_result_sha256": sha256(args.fixed_router_result),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "configuration_protocol_sha256": sha256(args.configuration_protocol),
        "configuration_protocol_closure_sha256": protocol_closure(
            args.configuration_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": source_hashes(Path(__file__).resolve().parent),
        "authoritative_e5_receipt": fixed_result["inputs"][
            "authoritative_e5_receipt"]}
    require(all(result["inputs"].get(name) == value
                for name, value in expected.items()),
            "shortlist-generator input bytes differ")
    validate_input_payloads(args.layout_manifest, args.k8_manifest)
    for binding in result["configuration_shortlist_manifests"].values():
        validate_manifest(binding)
    for binding in result["internal_shortlist_manifests"].values():
        validate_manifest(binding)
    gates = contract["quality_gates"]
    for partition in ("configuration", "locked_internal"):
        for row in result[partition]:
            require(row["passes_quality_gates"] == passes(row, gates),
                    f"shortlist-generator gate differs: {partition}/{row['id']}")
            if row.get("address_budget") is not None:
                require(row["passes_registered_gate"] ==
                        (passes(row, gates) and row["address_budget"] <=
                         gates["maximum_native_address_budget"]),
                        "shortlist-generator registered gate differs")
    require(set(result.get("parameter_frontier", {})) == set(
        family for family in contract["families"] if family.startswith("faiss_")),
        "shortlist-generator parameter frontier differs")
    require(set(result.get("selected_parameters", {})) == set(
        result["parameter_frontier"]) and
        set(result.get("configuration_shortlist_manifests", {})) ==
        set(contract["families"]) and len(result["configuration"]) ==
        1 + len(contract["families"]) * len(contract["shortlist_budgets"]) and
        len(result["locked_internal"]) == 3,
        "shortlist-generator result matrix differs")
    for family, rows in result["parameter_frontier"].items():
        require(rows and all(isinstance(row.get(
            "configuration_global_k8_coverage"), (int, float)) and
            isinstance(row.get("configuration_generator_ms_per_query"),
                       (int, float)) for row in rows),
            f"shortlist-generator parameter diagnostics differ: {family}")
        selected_parameter = min(rows, key=lambda row: (
            -row["configuration_global_k8_coverage"],
            row["configuration_generator_ms_per_query"], row["id"]))
        require(result["selected_parameters"][family]["id"] ==
                selected_parameter["id"] and result["selected_parameters"][
                    family]["configuration_global_k8_coverage"] ==
                selected_parameter["configuration_global_k8_coverage"],
                f"shortlist-generator parameter selection differs: {family}")
    family_rows = []
    for family in contract["families"]:
        rows = [row for row in result["configuration"]
            if row.get("address_budget") and row["id"].startswith(family + "-m")]
        passing_rows = [row for row in rows if row["passes_registered_gate"]]
        family_rows.append(min(passing_rows, key=lambda row: (
            row["address_budget"], row["coarse_ms"]["p95"] +
            row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))
            if passing_rows else min(rows, key=lambda row: (
                distance(row, gates), row["address_budget"], row["id"])))
    family_rows.sort(key=lambda row: (not row["passes_registered_gate"],
        distance(row, gates), row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
    require(result["internal_opened_from_configuration"] ==
            [row["id"] for row in family_rows[:2]],
            "shortlist-generator selection differs")
    require(set(result["internal_shortlist_manifests"]) == {
        row["id"].rsplit("-m", 1)[0] for row in family_rows[:2]},
        "shortlist-generator internal manifest set differs")
    passing_internal = [row for row in result["locked_internal"]
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    selected = (min(passing_internal, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
        if passing_internal else None)
    require(result["decision"]["selected"] == selected and
            result["decision"]["generator_passed"] == (selected is not None) and
            result["decision"]["native_integration_licensed"] ==
                (selected is not None) and
            result["decision"]["production_licensed"] is False,
            "shortlist-generator decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_shortlist_generator_bakeoff_evidence",
        "result_sha256": sha256(args.result), "inputs": expected,
        "faiss_version": result["inputs"]["faiss_version"],
        "parameter_frontier": result["parameter_frontier"],
        "selected_parameters": result["selected_parameters"],
        "configuration_points": len(result["configuration"]),
        "locked_internal_points": len(result["locked_internal"]),
        "selected": selected["id"] if selected else None,
        "generator_passed": selected is not None,
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
