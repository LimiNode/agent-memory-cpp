#!/usr/bin/env python3
"""Validate compact evidence for the training-sufficient router frontier."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_training_sufficient_evidence_runner",
              "run-neuroute-training-sufficient-router.py")
generator_evidence = load("neuroute_training_sufficient_generator_evidence",
    "write-neuroute-shortlist-generator-bakeoff-evidence.py")
require = runner.require
sha256 = runner.sha256
canonical = runner.canonical


def validate_manifest(binding: dict[str, Any]) -> None:
    path = Path(binding["path"])
    require(path.is_absolute() and sha256(path) == binding["sha256"],
            "training-sufficient shortlist manifest binding differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") ==
            "neuroute_local_k8_address_shortlist_materialization",
            "training-sufficient shortlist family differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        require(payload.is_absolute() and payload.stat().st_size == row["bytes"]
                and sha256(payload) == row["sha256"],
                "training-sufficient shortlist payload differs")


def expected_internal_open(result: dict[str, Any], gates: dict[str, Any]
                           ) -> list[str]:
    selected = {runner.candidate_id(row["treatment"],
        row["training_query_count"], row["ridge_lambda"],
        row["residual_weight"]): row
        for row in result["selected_hyperparameters"]}
    names = ["centroid_k1_control", *selected]
    family_rows = []
    for name in names:
        rows = [row for row in result["configuration"]
                if row.get("address_budget") is not None and
                row["id"].startswith(name + "-m")]
        require(len(rows) > 0,
                f"training-sufficient configuration family missing: {name}")
        passing = [row for row in rows if row["passes_registered_gate"]]
        family_rows.append(min(passing, key=lambda row: (
            row["address_budget"], row["coarse_ms"]["p95"] +
            row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))
            if passing else min(rows, key=lambda row: (
                runner.replay.gate_distance(row, gates),
                row["address_budget"], row["id"])))
    family_rows.sort(key=lambda row: (not row["passes_registered_gate"],
        runner.replay.gate_distance(row, gates), row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
    opened = []
    opened_treatments = set()
    for row in family_rows:
        name = row["id"].rsplit("-m", 1)[0]
        treatment = selected.get(name, {}).get("treatment", name)
        if treatment == "centroid_k1_control" or treatment in opened_treatments:
            continue
        opened.append(row["id"])
        opened_treatments.add(treatment)
        if len(opened) == 2:
            break
    require(len(opened) == 2,
            "training-sufficient configuration open count differs")
    return opened


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("contract", "result", "generator-result",
                 "configuration-protocol", "layout-manifest", "k8-manifest",
                 "native-executable", "multilingual-query-root",
                 "training-cache-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(sha256(Path(__file__)).isalnum(),
                "training-sufficient evidence self-test failed")
        return 0
    required = ("contract", "result", "generator_result",
        "configuration_protocol", "layout_manifest", "k8_manifest",
        "native_executable", "multilingual_query_root", "training_cache_root",
        "output")
    require(all(getattr(args, name) is not None for name in required),
            "training-sufficient evidence inputs are required")
    contract = runner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") ==
            "neuroute_training_sufficient_router_frontier_result",
            "training-sufficient result family differs")
    generator = json.loads(args.generator_result.read_text(encoding="utf-8"))
    parent = runner.exact.parent_protocol(json.loads(
        args.configuration_protocol.read_text(encoding="utf-8")))
    data = runner.exact.load_data(parent)
    _, _, pool_identity = runner.load_pool(data, args.multilingual_query_root)
    caches = runner.training_caches(args.training_cache_root, pool_identity)
    for binding in caches.values():
        runner.open_cache(binding)
    generator_evidence.validate_input_payloads(args.layout_manifest,
                                               args.k8_manifest)
    expected = {"contract_sha256": sha256(args.contract),
        "generator_result_sha256": sha256(args.generator_result),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "configuration_protocol_sha256": sha256(args.configuration_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "multilingual_query_manifest_sha256":
            pool_identity["manifest_sha256"],
        "training_pool": pool_identity,
        "training_cache_manifests_sha256": {str(seed): sha256(
            caches[seed]["path"]) for seed in runner.SEEDS},
        "source_files_sha256": runner.source_hashes(),
        "authoritative_e5_receipt": runner.exact.authoritative_receipt(parent)}
    require(all(result["inputs"].get(name) == value
                for name, value in expected.items()),
            "training-sufficient input bytes differ")
    require(generator.get("family") ==
            "neuroute_shortlist_generator_bakeoff_result",
            "training-sufficient comparison parent differs")
    for binding in result["shortlist_manifests"].values():
        validate_manifest(binding)
    for binding in result["internal_shortlist_manifests"].values():
        validate_manifest(binding)
    require(len(result["selected_hyperparameters"]) ==
            2 * (len(contract["treatments"]) - 1),
            "training-sufficient selected hyperparameter count differs")
    require(result["hyperparameter_selection_budget"] ==
            contract["quality_gates"]["maximum_native_address_budget"],
            "training-sufficient selection budget differs")
    selected_keys = {(row["treatment"], row["training_query_count"])
                     for row in result["selected_hyperparameters"]}
    require(selected_keys == {(treatment, count)
        for treatment in runner.LEARNED
        for count in contract["training"]["query_counts"]},
        "training-sufficient selected hyperparameter cells differ")
    selected_names = {runner.candidate_id(row["treatment"],
        row["training_query_count"], row["ridge_lambda"],
        row["residual_weight"]) for row in result["selected_hyperparameters"]}
    require(set(result["shortlist_manifests"]) == {
        "centroid_k1_control", *selected_names} and
        len(result["configuration"]) == 1 +
        len(result["shortlist_manifests"]) * len(contract["shortlist_budgets"]),
        "training-sufficient result matrix differs")
    gates = contract["quality_gates"]
    for partition in ("configuration", "locked_internal"):
        for row in result[partition]:
            require(row["passes_quality_gates"] ==
                    generator_evidence.passes(row, gates),
                    f"training-sufficient gate differs: {partition}/{row['id']}")
            if row.get("address_budget") is not None:
                require(row["passes_registered_gate"] ==
                        (row["passes_quality_gates"] and row["address_budget"] <=
                         gates["maximum_native_address_budget"]),
                        "training-sufficient registered gate differs")
                diagnostics = row["offline_router_diagnostics"]
                require(all(isinstance(diagnostics.get(name), (int, float))
                    and math.isfinite(float(diagnostics[name])) for name in (
                        "mean_rank_and_k8_margin_weighted_coverage",
                        "mean_generator_model_or_index_bytes",
                        "directional_generator_ms_per_query")),
                    "training-sufficient generator diagnostics differ")
            require(all(isinstance(row.get(name), (int, float)) and
                math.isfinite(float(row[name])) for name in (
                    "maximum_query_ndcg_loss", "p95_query_ndcg_loss")),
                "training-sufficient query loss diagnostics differ")
    expected_opened = expected_internal_open(result, gates)
    require(result["internal_opened_from_configuration"] == expected_opened and
            [row["id"] for row in result["locked_internal"]] ==
            ["global_fp32_k8", *expected_opened] and
            set(result["internal_shortlist_manifests"]) ==
            {value.rsplit("-m", 1)[0] for value in expected_opened} and
            result["decision"] == {
                "global_fp32_k8_role": "offline_teacher_and_reference_only",
                "common_generator_bakeoff_required": True,
                "native_integration_licensed": False,
                "production_licensed": False},
            "training-sufficient locked decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_training_sufficient_router_frontier_evidence",
        "result_sha256": sha256(args.result), "inputs": expected,
        "selected_hyperparameters": result["selected_hyperparameters"],
        "internal_opened_from_configuration":
            result["internal_opened_from_configuration"],
        "configuration_points": len(result["configuration"]),
        "locked_internal_points": len(result["locked_internal"]),
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
