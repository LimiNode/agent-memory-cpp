#!/usr/bin/env python3
"""Validate the exhaustive binary K8 representation-ceiling evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
import numpy as np


def load_runner() -> Any:
    path = THIS / "run-neuroute-binary-k8-ceiling.py"
    spec = importlib.util.spec_from_file_location(
        "neuroute_binary_k8_ceiling_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


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


def validate_input_payloads(layout_path: Path, k8_path: Path) -> None:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    for seed in layout["seeds"]:
        root = layout_path.parent / f"seed-{seed['seed']}"
        by_role = {row["role"]: row for row in seed["mappings"]}
        for role in ("occupied_addresses", "address_counts", "query_vectors",
                     "shortlist_rows"):
            descriptor = by_role[role]
            path = root / descriptor["file"]
            runner.require(path.stat().st_size == int(descriptor["bytes"]) and
                runner.sha256(path) == descriptor["sha256"],
                f"binary K8 layout payload differs: {seed['seed']}/{role}")
    k8 = json.loads(k8_path.read_text(encoding="utf-8"))
    for seed in k8["seeds"]:
        path = Path(seed["path"])
        runner.require(path.is_absolute() and path.stat().st_size ==
            int(seed["bytes"]) and runner.sha256(path) == seed["sha256"],
            f"binary K8 prototype payload differs: {seed['seed']}")


def validate_manifest(binding: dict[str, Any], contract_hash: str,
                      layout_hash: str, k8_hash: str) -> dict[str, Any]:
    path = Path(binding["path"])
    runner.require(path.is_absolute() and path.is_file() and
        runner.sha256(path) == binding["sha256"],
        "binary K8 shortlist manifest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    runner.require(value.get("family") ==
        "neuroute_local_k8_address_shortlist_materialization" and
        value["contract_sha256"] == contract_hash and
        value["layout_manifest_sha256"] == layout_hash and
        value["k8_manifest_sha256"] == k8_hash and
        value["source_files_sha256"] == runner.source_hashes() and
        len(value["seeds"]) == len(runner.SEEDS),
        "binary K8 shortlist manifest binding differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        shape = tuple(map(int, row["shape"]))
        runner.require(payload.is_absolute() and payload.is_file() and
            payload.stat().st_size == int(row["bytes"]) and
            runner.sha256(payload) == row["sha256"] and shape == (152, 8192),
            "binary K8 shortlist payload binding differs")
        arrays = np.memmap(payload, mode="r", dtype="<u4", shape=shape)
        runner.require(int(arrays.max()) < int(row["occupied_addresses"]) and
            all(len(np.unique(values)) == shape[1] for values in arrays),
            "binary K8 shortlist payload rows differ")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-binary-k8-ceiling.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        runner.self_test()
        return 0
    runner.require(all(getattr(args, name) is not None for name in (
        "result", "layout_manifest", "k8_manifest", "configuration_protocol",
        "native_executable", "output")),
        "binary K8 evidence inputs are required")
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("Faiss is required for binary K8 evidence") from error
    contract = runner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    runner.require(result.get("family") ==
        "neuroute_binary_k8_representation_ceiling_result" and
        result.get("claim_scope") == contract["claim_scope"],
        "binary K8 evidence result family differs")
    config_value = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    parent = runner.exact.parent_protocol(config_value)
    expected_inputs = {"contract_sha256": runner.sha256(args.contract),
        "layout_manifest_sha256": runner.sha256(args.layout_manifest),
        "k8_manifest_sha256": runner.sha256(args.k8_manifest),
        "configuration_protocol_sha256": runner.sha256(
            args.configuration_protocol),
        "configuration_protocol_closure_sha256":
            runner.replay.protocol_closure(args.configuration_protocol),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "source_files_sha256": runner.source_hashes(),
        "authoritative_e5_receipt": runner.exact.authoritative_receipt(parent),
        "faiss_version": faiss.__version__}
    runner.require(result["inputs"] == expected_inputs and
        isinstance(result.get("faiss_threads"), int) and
        result["faiss_threads"] > 0,
        "binary K8 evidence input binding differs")
    validate_input_payloads(args.layout_manifest, args.k8_manifest)
    expected_treatments = {f"{codec['id']}-k{prefix}"
        for codec in contract["codecs"]
        for prefix in contract["prototype_prefixes"]}
    manifests = result["configuration_shortlist_manifests"]
    runner.require(set(manifests) == expected_treatments,
                   "binary K8 shortlist treatment matrix differs")
    for binding in manifests.values():
        validate_manifest(binding, expected_inputs["contract_sha256"],
            expected_inputs["layout_manifest_sha256"],
            expected_inputs["k8_manifest_sha256"])
    runner.require(set(result["internal_shortlist_manifests"]) == {
        name.rsplit("-m", 1)[0]
        for name in result["internal_opened_from_configuration"]},
        "binary K8 internal shortlist binding set differs")
    for treatment, binding in result["internal_shortlist_manifests"].items():
        runner.require(binding == manifests[treatment],
                       "binary K8 internal shortlist binding differs")
    gates = contract["quality_gates"]
    for partition in ("configuration", "locked_internal"):
        for row in result[partition]:
            runner.require(math.isfinite(float(row["mean_ndcg_loss"])) and
                row["passes_quality_gates"] == passes(row, gates) and
                row["eligible_budget"] == (row.get("address_budget") is None or
                    row["address_budget"] <= gates[
                        "maximum_native_address_budget"]) and
                row["passes_registered_gate"] == (passes(row, gates) and
                    row["eligible_budget"]),
                f"binary K8 gate differs: {partition}/{row['id']}")
            diagnostic = row.get("offline_router_diagnostics")
            if diagnostic is not None:
                runner.require(0.0 <= diagnostic[
                    "mean_fp32_same_k_address_overlap"] <= 1.0 and
                    0.0 <= diagnostic[
                    "mean_fp32_k8_top1024_address_recall"] <= 1.0 and
                    diagnostic["mean_prototype_code_bytes"] > 0.0 and
                    diagnostic[
                        "directional_exhaustive_scan_ms_per_query"] > 0.0,
                    "binary K8 offline diagnostic differs")
    runner.require(len(result["configuration"]) == 1 +
        len(expected_treatments) * len(contract["address_budgets"]) and
        len(result["locked_internal"]) == 3,
        "binary K8 result matrix size differs")
    candidates = [row for row in result["configuration"]
        if row.get("address_budget") is not None and row["address_budget"] <=
        contract["product_address_budget_maximum"]]
    candidates.sort(key=lambda row: (not row["passes_registered_gate"],
        runner.replay.gate_distance(row, gates), row["address_budget"],
        row["offline_router_diagnostics"]["mean_prototype_code_bytes"],
        row["offline_router_diagnostics"][
            "directional_exhaustive_scan_ms_per_query"], row["id"]))
    expected_opened = []
    families: set[str] = set()
    for row in candidates:
        family = row["id"].rsplit("-m", 1)[0]
        if family in families:
            continue
        expected_opened.append(row["id"])
        families.add(family)
        if len(expected_opened) == 2:
            break
    passing = [row for row in result["locked_internal"]
        if row.get("address_budget") is not None and
        row["passes_registered_gate"]]
    selected = (min(passing, key=lambda row: (row["address_budget"],
        row["offline_router_diagnostics"]["mean_prototype_code_bytes"],
        row["id"])) if passing else None)
    near = [row for row in candidates if row["mean_ndcg_loss"] <=
        contract["near_miss_gate"]["maximum_mean_downstream_ndcg_loss"] and
        row["mean_final_top10_overlap"] >=
        contract["near_miss_gate"]["minimum_mean_final_top10_overlap"]]
    decision = result["decision"]
    runner.require(result["internal_opened_from_configuration"] ==
        expected_opened and decision["selected"] == selected and
        decision["backend_followup_licensed"] == bool(passing) and
        decision["learned_query_followup_licensed"] ==
            (not passing and bool(near)) and
        decision["configuration_near_miss_ids"] ==
            [row["id"] for row in near] and
        decision["production_licensed"] is False,
        "binary K8 decision replay differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_binary_k8_representation_ceiling_evidence",
        "result_sha256": runner.sha256(args.result),
        "inputs": expected_inputs,
        "configuration_points": len(result["configuration"]),
        "locked_internal_points": len(result["locked_internal"]),
        "opened": expected_opened,
        "selected": selected["id"] if selected else None,
        "backend_followup_licensed": bool(passing),
        "learned_query_followup_licensed": not passing and bool(near),
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
