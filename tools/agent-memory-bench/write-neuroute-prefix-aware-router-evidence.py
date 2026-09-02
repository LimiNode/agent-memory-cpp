#!/usr/bin/env python3
"""Validate the prefix-aware router frontier evidence."""
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


def load() -> Any:
    path = THIS / "run-neuroute-prefix-aware-router.py"
    spec = importlib.util.spec_from_file_location("neuroute_prefix_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load()


def validate_shortlist(binding: dict[str, Any]) -> dict[str, Any]:
    path = Path(binding["path"])
    runner.require(path.is_file() and runner.sha256(path) == binding["sha256"],
                   "prefix-aware shortlist manifest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    runner.require(value.get("family") ==
                   "neuroute_local_k8_address_shortlist_materialization" and
                   len(value["seeds"]) == len(runner.SEEDS),
                   "prefix-aware shortlist family differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        runner.require(payload.is_file() and payload.stat().st_size ==
                       int(row["bytes"]) and runner.sha256(payload) ==
                       row["sha256"],
                       "prefix-aware shortlist payload differs")
    return value


def complement_diagnostics(result: dict[str, Any], layout_path: Path,
                           k1_manifest_path: Path) -> dict[str, Any]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    k1 = json.loads(k1_manifest_path.read_text(encoding="utf-8"))
    runner.require(k1.get("router") == "centroid_k1_control" and
                   len(k1["seeds"]) == len(runner.SEEDS),
                   "prefix-aware K1 control differs")
    fractions = (0.5, 0.75, 0.875, 0.9375)
    discounts = 1.0 / np.log2(np.arange(1024, dtype=np.float64) + 2.0)
    denominator = float(discounts.sum())
    diagnostics = {}
    for opened in result["opened_from_configuration"]:
        name = opened.rsplit("-m", 1)[0]
        prefix_manifest = validate_shortlist(
            result["shortlist_manifests"][name]["4096"])
        totals = {fraction: [0.0, 0.0] for fraction in fractions}
        baseline = [0.0, 0.0]
        recovered = []
        queries = 0
        for seed_row in layout["seeds"]:
            seed = int(seed_row["seed"])
            mapping = next(row for row in seed_row["mappings"]
                           if row["role"] == "shortlist_rows")
            teacher = np.fromfile(layout_path.parent / f"seed-{seed}" /
                mapping["file"], dtype="<u4").reshape(152, 1024)[:76]
            k1_row = next(row for row in k1["seeds"] if row["seed"] == seed)
            prefix_row = next(row for row in prefix_manifest["seeds"]
                              if row["seed"] == seed)
            k1_orders = np.fromfile(k1_row["path"], dtype="<u4").reshape(
                tuple(k1_row["shape"]))[:76, :4096]
            prefix_orders = np.fromfile(prefix_row["path"], dtype="<u4").reshape(
                tuple(prefix_row["shape"]))[:76]
            for query in range(76):
                teacher_values = list(map(int, teacher[query]))
                k1_values = list(map(int, k1_orders[query]))
                prefix_values = list(map(int, prefix_orders[query]))
                k1_set = set(k1_values)
                prefix_set = set(prefix_values)
                base_mask = np.asarray([value in k1_set
                                        for value in teacher_values])
                baseline[0] += float(base_mask.mean())
                baseline[1] += float(discounts[base_mask].sum()) / denominator
                recovered.append(sum(value not in k1_set and value in prefix_set
                                     for value in teacher_values))
                for fraction in fractions:
                    base_count = int(4096 * fraction)
                    chosen = k1_values[:base_count]
                    chosen_set = set(chosen)
                    for value in prefix_values:
                        if value not in chosen_set:
                            chosen.append(value)
                            chosen_set.add(value)
                            if len(chosen) == 4096:
                                break
                    runner.require(len(chosen) == 4096,
                                   "prefix-aware hybrid size differs")
                    mask = np.asarray([value in chosen_set
                                       for value in teacher_values])
                    totals[fraction][0] += float(mask.mean())
                    totals[fraction][1] += (float(discounts[mask].sum()) /
                                            denominator)
                queries += 1
        baseline = [value / queries for value in baseline]
        points = {f"{fraction:g}": {"mean_teacher_coverage":
            totals[fraction][0] / queries,
            "mean_rank_discounted_teacher_coverage":
            totals[fraction][1] / queries} for fraction in fractions}
        maximum_coverage_gain = max(row["mean_teacher_coverage"] - baseline[0]
                                    for row in points.values())
        maximum_rank_gain = max(row[
            "mean_rank_discounted_teacher_coverage"] - baseline[1]
            for row in points.values())
        diagnostics[name] = {"mean_k1_misses_recovered_by_prefix":
            float(np.mean(recovered)),
            "k1_baseline": {"mean_teacher_coverage": baseline[0],
                "mean_rank_discounted_teacher_coverage": baseline[1]},
            "hybrid_keep_fraction": points,
            "maximum_teacher_coverage_gain": maximum_coverage_gain,
            "maximum_rank_discounted_coverage_gain": maximum_rank_gain,
            "hybrid_native_replay_licensed": bool(maximum_coverage_gain > 0.0 and
                                                   maximum_rank_gain > 0.0)}
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prefix-aware-router.example.json")
    parser.add_argument("--result", type=Path)
    for name in ("policy-result", "policy-evidence", "training-result",
                 "configuration-protocol", "layout-manifest", "k8-manifest",
                 "native-executable", "multilingual-query-root",
                 "training-cache-root", "k1-shortlist-manifest"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        runner.self_test()
        return 0
    required = ("result", "policy_result", "policy_evidence", "training_result",
        "configuration_protocol", "layout_manifest", "k8_manifest",
        "native_executable", "multilingual_query_root", "training_cache_root",
        "k1_shortlist_manifest", "output")
    runner.require(all(getattr(args, name) is not None for name in required),
                   "prefix-aware evidence inputs are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    runner.require(result.get("family") ==
                   "neuroute_prefix_aware_router_frontier_result",
                   "prefix-aware evidence result family differs")
    contract = runner.load_contract(args.contract)
    policy_result = json.loads(args.policy_result.read_text(encoding="utf-8"))
    training_result = json.loads(args.training_result.read_text(encoding="utf-8"))
    import scipy
    parent = runner.exact.parent_protocol(json.loads(
        args.configuration_protocol.read_text(encoding="utf-8")))
    expected_inputs = {"contract_sha256": runner.sha256(args.contract),
        "policy_result_sha256": runner.sha256(args.policy_result),
        "policy_evidence_sha256": runner.sha256(args.policy_evidence),
        "layout_manifest_sha256": runner.sha256(args.layout_manifest),
        "k8_manifest_sha256": runner.sha256(args.k8_manifest),
        "configuration_protocol_sha256": runner.sha256(
            args.configuration_protocol),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "multilingual_query_manifest_sha256": runner.sha256(
            args.multilingual_query_root / "manifest.json"),
        "training_pool": result["inputs"]["training_pool"],
        "training_cache_manifests_sha256": result["inputs"][
            "training_cache_manifests_sha256"],
        "source_files_sha256": runner.source_hashes(),
        "scipy_version": scipy.__version__,
        "authoritative_e5_receipt": runner.exact.authoritative_receipt(parent)}
    runner.require(result["inputs"] == expected_inputs and
        policy_result["inputs"]["parents"]["training"]["result_sha256"] ==
            runner.sha256(args.training_result) and
        training_result["shortlist_manifests"]["centroid_k1_control"][
            "sha256"] == runner.sha256(args.k1_shortlist_manifest),
        "prefix-aware evidence input binding differs")
    cache_hashes = {runner.sha256(path) for path in
        args.training_cache_root.rglob("manifest.json")}
    runner.require(set(result["inputs"][
        "training_cache_manifests_sha256"].values()) <= cache_hashes,
        "prefix-aware training cache binding differs")
    for by_budget in result["shortlist_manifests"].values():
        for binding in by_budget.values():
            validate_shortlist(binding)
    for binding in result["confirmation_shortlist_manifests"].values():
        validate_shortlist(binding)
    gates = contract["quality_gates"]
    for partition in ("configuration", "reused_confirmation"):
        for row in result[partition]:
            runner.require(math.isfinite(float(row["maximum_query_ndcg_loss"]))
                and math.isfinite(float(row["p95_query_ndcg_loss"])),
                "prefix-aware evidence query loss differs")
            if row.get("address_budget") is not None:
                runner.require(row["address_budget"] <= 4096 and
                    row["passes_registered_gate"] ==
                    runner.policy.passes(row, gates),
                    "prefix-aware evidence product budget differs")
    expected_opened = []
    for topology in runner.TOPOLOGIES:
        names = [name for name in result["selected_finalists"]
                 if name.startswith(topology + "-")]
        rows = [row for row in result["configuration"]
                if row.get("address_budget") == 4096 and
                any(row["id"].startswith(name + "-m") for name in names)]
        expected_opened.append(min(rows, key=lambda row: (
            runner.replay.gate_distance(row, gates),
            row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))["id"])
    passing = [row for row in result["reused_confirmation"]
               if row["id"] != "global_fp32_k8" and
               row["passes_registered_gate"]]
    runner.require(len(result["selected_finalists"]) == 6 and
        len(result["configuration"]) == 19 and
        len(result["reused_confirmation"]) == 4 and
        result["opened_from_configuration"] == expected_opened and
        result["decision"]["global_fp32_k8_role"] ==
            "offline_teacher_and_reference_only" and
        result["decision"]["maximum_local_k8_addresses"] == 4096 and
        result["decision"]["prefix_aware_passed"] == bool(passing) and
        result["decision"]["passing_confirmation_rows"] == passing and
        result["decision"]["native_integration_licensed"] is False and
        result["decision"]["production_licensed"] is False,
        "prefix-aware evidence matrix or decision differs")
    complement = complement_diagnostics(result, args.layout_manifest,
                                        args.k1_shortlist_manifest)
    runner.require(not any(row["hybrid_native_replay_licensed"]
                           for row in complement.values()),
                   "prefix-aware hybrid unexpectedly requires native replay")
    evidence = {"schema_version": 1,
        "family": "neuroute_prefix_aware_router_frontier_evidence",
        "result_sha256": runner.sha256(args.result),
        "inputs": result["inputs"],
        "selected_finalists": result["selected_finalists"],
        "opened_from_configuration": result["opened_from_configuration"],
        "prefix_aware_passed": result["decision"]["prefix_aware_passed"],
        "k1_prefix_complement_diagnostics": complement,
        "hybrid_native_replay_licensed": False,
        "result_binding_and_decision_validation_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
