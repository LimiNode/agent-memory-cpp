#!/usr/bin/env python3
"""Validate compact evidence for the frozen 12/14-to-16 hierarchy replay."""
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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_hierarchy_evidence_runner",
              "run-neuroute-12-to-16-hierarchy-replay.py")
training_evidence = load("neuroute_hierarchy_training_evidence",
    "write-neuroute-training-sufficient-router-evidence.py")
generator_evidence = training_evidence.generator_evidence
require = runner.require
sha256 = runner.sha256
canonical = runner.canonical


def expected_opened(result: dict[str, Any], budgets: list[int],
                    gates: dict[str, Any]) -> list[str]:
    opened = []
    families = []
    for treatment in runner.NATIVE_TREATMENTS:
        family = runner.treatment_family(treatment)
        if family not in families:
            families.append(family)
    for family in families:
        rows = [row for row in result["configuration"]
                if row.get("address_budget") and
                ((row["id"].startswith(family + "_beam_") if
                  family.startswith("same_head_prefix") else
                  row["id"].startswith(family + "-m")))]
        opened.append(runner.configuration_open(rows, gates)["id"])
    return opened


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-12-to-16-hierarchy-replay.example.json")
    for name in ("result", "width-result", "width-materialization-manifest",
                 "width-model-root", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(runner.treatment_family("same_head_prefix12_beam_4x") ==
                "same_head_prefix12", "hierarchy evidence self-test failed")
        return 0
    required = ("result", "width_result", "width_materialization_manifest",
        "width_model_root", "configuration_protocol", "layout_manifest",
        "k8_manifest", "native_executable", "output")
    require(all(getattr(args, name) is not None for name in required),
            "hierarchy evidence inputs are required")
    contract = runner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") ==
            "neuroute_12_14_to_16_hierarchy_replay_result",
            "hierarchy evidence result family differs")
    width_result = json.loads(args.width_result.read_text(encoding="utf-8"))
    expected_models = {}
    for row in width_result["models"]:
        if row["width"] not in (12, 14, 16) or row["seed"] not in runner.SEEDS:
            continue
        path = args.width_model_root / row["file"]
        require(path.is_file() and sha256(path) == row["sha256"],
                "hierarchy evidence model bytes differ")
        expected_models[f"{row['width']}/{row['seed']}"] = row["sha256"]
    require(len(expected_models) == 9,
            "hierarchy evidence model matrix differs")
    parent = runner.exact.parent_protocol(json.loads(
        args.configuration_protocol.read_text(encoding="utf-8")))
    expected_inputs = {"contract_sha256": sha256(args.contract),
        "width_result_sha256": sha256(args.width_result),
        "width_materialization_manifest_sha256": sha256(
            args.width_materialization_manifest),
        "width_model_files_sha256": expected_models,
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "configuration_protocol_sha256": sha256(args.configuration_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": runner.source_hashes(),
        "authoritative_e5_receipt": runner.exact.authoritative_receipt(parent)}
    require(result["inputs"] == expected_inputs,
            "hierarchy evidence input binding differs")
    generator_evidence.validate_input_payloads(args.layout_manifest,
                                               args.k8_manifest)
    budgets = list(map(int, contract["shortlist_budgets"]))
    require(set(result["offline_prefix_frontier"]) ==
            set(runner.NATIVE_TREATMENTS) and
            all(set(points) == set(map(str, budgets)) for points in
                result["offline_prefix_frontier"].values()),
            "hierarchy evidence offline matrix differs")
    required_diagnostics = {"mean_rank_and_k8_margin_weighted_coverage",
        "mean_generator_model_or_index_bytes", "mean_prefixes_scored",
        "mean_fine_addresses_scored", "directional_generator_ms_per_query"}
    for points in result["offline_prefix_frontier"].values():
        for partitions in points.values():
            for metrics in partitions.values():
                require(required_diagnostics <= set(metrics) and
                        all(metrics[name] is not None and
                            math.isfinite(float(metrics[name]))
                            for name in required_diagnostics),
                        "hierarchy evidence diagnostics differ")
    for by_budget in result["shortlist_manifests"].values():
        for binding in by_budget.values():
            training_evidence.validate_manifest(binding)
    for binding in result["confirmation_shortlist_manifests"].values():
        training_evidence.validate_manifest(binding)
    gates = contract["quality_gates"]
    for partition in ("configuration", "reused_confirmation"):
        for row in result[partition]:
            require(row["passes_quality_gates"] ==
                    generator_evidence.passes(row, gates),
                    "hierarchy evidence quality gate differs")
            if row.get("address_budget") is not None:
                require(row["passes_registered_gate"] ==
                        (row["passes_quality_gates"] and row["address_budget"] <=
                         gates["maximum_native_address_budget"]),
                        "hierarchy evidence registered gate differs")
            require(math.isfinite(float(row["maximum_query_ndcg_loss"])) and
                    math.isfinite(float(row["p95_query_ndcg_loss"])),
                    "hierarchy evidence query loss differs")
    opened = expected_opened(result, budgets, gates)
    require(result["opened_from_configuration"] == opened and
            [row["id"] for row in result["reused_confirmation"]] ==
            ["global_fp32_k8", *opened],
            "hierarchy evidence confirmation open differs")
    passing = [row for row in result["reused_confirmation"]
               if row.get("passes_registered_gate") and
               row["id"] != "global_fp32_k8"]
    selected = min(passing, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"])) if passing else None
    require(result["decision"]["global_fp32_k8_role"] ==
            "offline_teacher_and_reference_only" and
            result["decision"]["selected"] == selected and
            result["decision"]["hierarchy_passed"] == (selected is not None) and
            result["decision"]["native_integration_licensed"] is False and
            result["decision"]["production_licensed"] is False,
            "hierarchy evidence decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_12_14_to_16_hierarchy_replay_evidence",
        "result_sha256": sha256(args.result), "inputs": expected_inputs,
        "configuration_points": len(result["configuration"]),
        "confirmation_points": len(result["reused_confirmation"]),
        "opened_from_configuration": opened,
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
