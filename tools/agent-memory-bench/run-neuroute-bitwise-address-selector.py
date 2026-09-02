#!/usr/bin/env python3
"""Evaluate query-supervised 12/14/16-bit address-score selectors."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
WIDTHS = [12, 14, 16]
TARGETS = ["rank_mass", "k8_margin_mass"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The replay helpers are shared with the validated R4 local-K8 harness.
prefix = load("neuroute_bitwise_prefix", "run-neuroute-prefix-aware-router.py")
training = prefix.training
bakeoff = prefix.bakeoff
replay = prefix.replay
exact = prefix.exact
fixed = prefix.fixed
require = prefix.require
sha256 = prefix.sha256
canonical = prefix.canonical


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-bitwise-address-selector.py",
             "run-neuroute-prefix-aware-router.py",
             "run-neuroute-training-sufficient-router.py",
             "run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "run-neuroute-generator-policy-bakeoff.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes
bakeoff.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_bitwise_address_selector_frontier" and
            value["training"]["query_count"] == 8141 and
            value["training"]["projection_rank"] == 64 and
            value["training"]["widths"] == WIDTHS and
            value["training"]["targets"] == TARGETS and
            value["shortlist_budgets"] == [1024, 2048, 4096] and
            value["decision"]["global_address_k8_scan_forbidden"] is True and
            value["decision"]["production_selection_forbidden"] is True,
            "bitwise address selector contract differs")
    return value


def bit_signs(addresses: np.ndarray, width: int) -> np.ndarray:
    bits = ((np.expand_dims(addresses, axis=-1) >>
             np.arange(width, dtype=np.uint32)) & 1)
    return (bits.astype(np.float32) * 2.0) - 1.0


def target_values(name: str, scores: np.ndarray) -> np.ndarray:
    discounts = (1.0 / np.log2(np.arange(scores.shape[1], dtype=np.float64) +
                               2.0)).astype(np.float32)
    if name == "rank_mass":
        return np.broadcast_to(discounts, scores.shape)
    require(name == "k8_margin_mass", "bitwise target differs")
    margins = np.maximum(scores - scores[:, -1:], 0.0)
    scale = np.maximum(margins.max(axis=1, keepdims=True), 1.0e-8)
    return discounts[None, :] * (0.1 + 0.9 * margins / scale)


def bit_labels(cache_rows: np.ndarray, occupied: np.ndarray, scores: np.ndarray,
               width: int, target: str) -> np.ndarray:
    signs = bit_signs(occupied[cache_rows], width)
    values = target_values(target, scores)
    labels = np.einsum("qn,qnb->qb", values, signs, optimize=True)
    normalizer = np.maximum(np.abs(labels).max(axis=1, keepdims=True), 1.0e-8)
    return (labels / normalizer).astype(np.float32)


def score_orders(evaluate_z: np.ndarray, weights: np.ndarray,
                 occupied: np.ndarray, budgets: list[int]
                 ) -> tuple[dict[int, np.ndarray], dict[int, float]]:
    signs = bit_signs(occupied, weights.shape[1])
    started = time.perf_counter()
    bit_scores = np.asarray(evaluate_z @ weights, dtype=np.float32)
    scoring_ms = (time.perf_counter() - started) * 1000.0 / len(evaluate_z)
    result = {}
    timings = {}
    for budget in budgets:
        started = time.perf_counter()
        scores = bit_scores @ signs.T
        result[budget] = np.asarray([
            replay.top_order(row, occupied, budget) for row in scores],
            dtype=np.uint32)
        timings[budget] = scoring_ms + ((time.perf_counter() - started) *
                                        1000.0 / len(evaluate_z))
    return result, timings


def metrics(value: dict[str, Any], common: dict[int, Any], budget: int,
            query_range: range, partition: str) -> dict[str, Any]:
    orders = {seed: value["orders"][budget][seed] for seed in SEEDS}
    timings = {seed: {budget: value["timings"][partition][budget][seed]}
               for seed in SEEDS}
    return training.generator_metrics(orders, common, budget, query_range,
                                      timings, value["model_bytes"])


def aggregate(rows: list[dict[str, Any]], reference: list[dict[str, Any]],
              treatment: dict[str, Any], budget: int | None,
              gates: dict[str, Any], offline: dict[str, Any] | None
              ) -> dict[str, Any]:
    return training.aggregate(rows, reference, treatment, budget, gates, offline)


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    policy_result = json.loads(args.policy_result.read_text(encoding="utf-8"))
    policy_evidence = json.loads(args.policy_evidence.read_text(encoding="utf-8"))
    require(policy_result.get("family") ==
            "neuroute_generator_policy_bakeoff_result" and
            policy_evidence.get("result_sha256") == sha256(args.policy_result) and
            policy_result["decision"]["adaptive_prefix_training_required"] is True,
            "bitwise policy activation differs")
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    internal_value = dict(config_value)
    internal_value["partition"] = "reused_confirmation"
    internal_value["requests"] = parent["requests"]
    require(len(config_value["requests"]) == len(parent["requests"]) == 76,
            "bitwise partitions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    data = exact.load_data(parent)
    _, pool, pool_identity = training.load_pool(data, args.multilingual_query_root)
    caches = training.training_caches(args.training_cache_root, pool_identity)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    request_rows = sorted(list(config_value["requests"]) + list(parent["requests"]),
                          key=lambda row: int(row["request"]))
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_native[native]
              for request, native in enumerate(native_positions)}
    budgets = list(map(int, contract["shortlist_budgets"]))
    candidates: dict[str, dict[str, Any]] = {}
    common: dict[int, dict[str, Any]] = {}
    occupied_counts: dict[int, int] = {}
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "occupied_addresses")["file"], dtype="<u4")
        counts = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "address_counts")["file"], dtype="<u4")
        evaluate = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        global_rows = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
        scalar_features = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "scalar_features")["file"], dtype="<f4").reshape(152, 1024, 22)
        cache_identity = caches[seed]["manifest"]["identity"]
        require(cache_identity.get("prototypes_sha256") ==
                k8_seed["dense_prototypes_sha256"] and
                cache_identity.get("effective_sha256") ==
                k8_seed["effective_sha256"], "bitwise cache topology differs")
        centroids = replay.first_prototypes(k8_seed, counts)
        cache_addresses, cache_features, _ = training.open_cache(caches[seed])
        address_to_row = np.full(1 << 16, -1, dtype=np.int32)
        address_to_row[occupied] = np.arange(len(occupied), dtype=np.int32)
        cache_rows = address_to_row[cache_addresses]
        require(np.all(cache_rows >= 0), "bitwise cache contains an unoccupied address")
        cache_rows = np.asarray(cache_rows, dtype=np.uint32)
        common[seed] = {"counts": counts, "global_rows": global_rows,
            "global_scores": np.asarray(scalar_features[:, :, 8], dtype=np.float32),
            "actionable_rows": training.actionable_rows(oracle, doc_rows[seed])}
        occupied_counts[seed] = len(occupied)
        train_z, evaluate_z, projection, projection_ms = training.projected_queries(
            pool, evaluate, int(contract["training"]["projection_rank"]), 76)
        gram = train_z.T @ train_z
        teacher_scores = np.asarray(cache_features[:, :, 8], dtype=np.float32)
        for target in TARGETS:
            for width in WIDTHS:
                labels = bit_labels(cache_rows, occupied, teacher_scores, width, target)
                cross = train_z.T @ labels
                for ridge in contract["training"]["ridge_lambdas"]:
                    regularized = gram.copy()
                    regularized.flat[::len(regularized) + 1] += float(ridge)
                    weights = np.linalg.solve(regularized, cross)
                    orders, elapsed = score_orders(evaluate_z, weights, occupied, budgets)
                    name = f"width{width}-{target}-l{ridge:g}"
                    value = candidates.setdefault(name, {"width": width,
                        "target": target, "ridge_lambda": float(ridge),
                        "orders": {budget: {} for budget in budgets},
                        "timings": {partition: {budget: {} for budget in budgets}
                                    for partition in ("configuration", "internal")},
                        "model_bytes": {}})
                    for budget in budgets:
                        value["orders"][budget][seed] = orders[budget]
                        value["timings"]["configuration"][budget][seed] = elapsed[budget]
                        value["timings"]["internal"][budget][seed] = elapsed[budget]
                    projection_bytes = (384 + 384 * int(projection["rank"]) +
                                        int(projection["rank"])) * 8
                    value["model_bytes"][seed] = int(weights.nbytes + projection_bytes +
                                                     bit_signs(occupied, width).nbytes)
        del cache_addresses, cache_features, cache_rows, train_z, evaluate_z, gram
        gc.collect()
    selection_budget = int(contract["selection"]["offline_budget"])
    finalists: list[dict[str, Any]] = []
    offline = []
    for width in WIDTHS:
        rows = [value for value in candidates.values() if value["width"] == width]
        for value in rows:
            value["configuration_diagnostics"] = metrics(value, common,
                selection_budget, range(76), "configuration")
            offline.append({"width": width, "target": value["target"],
                "ridge_lambda": value["ridge_lambda"],
                "configuration_diagnostics": value["configuration_diagnostics"]})
        rows.sort(key=lambda value: (value["configuration_diagnostics"][
            "mean_lost_final_top10_addresses"],
            -value["configuration_diagnostics"]["mean_actionable_address_coverage"],
            -value["configuration_diagnostics"]["mean_rank_discounted_global_k8_coverage"],
            value["target"], value["ridge_lambda"]))
        finalists.append(rows[0])
    manifests = {f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}":
        {budget: bakeoff.materialize(args.output_root / "shortlists" /
            f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}" /
            f"m{budget}", f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}",
            value["orders"][budget], occupied_counts, args,
            {"width": value["width"], "target": value["target"],
             "ridge_lambda": value["ridge_lambda"], "address_budget": budget})
         for budget in budgets} for value in finalists}
    gates = contract["quality_gates"]
    config_ref_protocol = replay.protocol(args.configuration_protocol, None, None,
        args.output_root / "protocols/configuration-reference.json", contract)
    internal_ref_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols/internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_ref_protocol, data, doc_rows)
    internal_inputs = replay.partition_inputs(internal_ref_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32", "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration",
        config_ref_protocol, "global-fp32-k8", reference_treatment, config_inputs,
        config_references, True)
    configuration = [aggregate(config_reference, config_reference, reference_treatment,
                               None, gates, None)]
    for value in finalists:
        name = f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}"
        for budget in budgets:
            point = f"{name}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol, manifests[name][budget],
                budget, args.output_root / f"protocols/configuration-{point}.json", contract)
            treatment = {"id": point, "kind": "bitwise_address_router", "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol, point,
                treatment, config_inputs, config_references, False)
            configuration.append(aggregate(rows, config_reference, treatment, budget, gates,
                metrics(value, common, budget, range(76), "configuration")))
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "reused_confirmation",
        internal_ref_protocol, "global-fp32-k8", reference_treatment, internal_inputs,
        internal_references, True)
    internal = [aggregate(internal_reference, internal_reference, reference_treatment,
                          None, gates, None)]
    internal_manifests = {}
    opened = []
    for value in finalists:
        name = f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}"
        rows = [row for row in configuration if row.get("address_budget") == 4096 and
                row["id"].startswith(name + "-")]
        selected = min(rows, key=lambda row: (replay.gate_distance(row, gates), row["id"]))
        opened.append(selected["id"])
        manifest = manifests[name][4096]
        internal_manifests[name] = {"path": str(manifest.resolve()), "sha256": sha256(manifest)}
        protocol = replay.protocol(internal_source, manifest, 4096,
            args.output_root / f"protocols/internal-{selected['id']}.json", contract)
        treatment = {"id": selected["id"], "kind": "bitwise_address_router", "record_bytes": 0}
        rows = replay.run_point(args, contract, "reused_confirmation", protocol,
            selected["id"], treatment, internal_inputs, internal_references, False)
        internal.append(aggregate(rows, internal_reference, treatment, 4096, gates,
            metrics(next(value for value in finalists if selected["id"].startswith(
                f"width{value['width']}-")), common, 4096, range(76, 152), "internal")))
    passing = [row for row in internal if row["id"] != "global_fp32_k8" and
               row["passes_registered_gate"]]
    result = {"schema_version": 1, "family": "neuroute_bitwise_address_selector_frontier_result",
        "inputs": {"contract_sha256": sha256(args.contract),
            "policy_result_sha256": sha256(args.policy_result),
            "policy_evidence_sha256": sha256(args.policy_evidence),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "multilingual_query_manifest_sha256": pool_identity["manifest_sha256"],
            "training_pool": pool_identity,
            "training_cache_manifests_sha256": {str(seed): sha256(caches[seed]["path"])
                for seed in SEEDS}, "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "offline_frontier": offline,
        "selected_finalists": [f"width{value['width']}-{value['target']}-l{value['ridge_lambda']:g}"
                                for value in finalists],
        "configuration": configuration, "reused_confirmation": internal,
        "opened_from_configuration": opened,
        "shortlist_manifests": {name: {str(budget): {"path": str(path.resolve()),
            "sha256": sha256(path)} for budget, path in by_budget.items()}
            for name, by_budget in manifests.items()},
        "confirmation_shortlist_manifests": internal_manifests,
        "decision": {"global_fp32_k8_role": "offline_teacher_and_reference_only",
            "maximum_local_k8_addresses": 4096, "bitwise_selector_passed": bool(passing),
            "passing_confirmation_rows": passing, "native_integration_licensed": False,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    addresses = np.asarray([0, 1, 2, 3], dtype=np.uint32)
    require(np.array_equal(bit_signs(addresses, 2),
        np.asarray([[-1, -1], [1, -1], [-1, 1], [1, 1]], dtype=np.float32)),
        "bitwise signs differ")
    scores = np.asarray([[4.0, 2.0, 1.0]], dtype=np.float32)
    require(target_values("rank_mass", scores).shape == scores.shape and
            target_values("k8_margin_mass", scores).shape == scores.shape,
            "bitwise target shape differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-bitwise-address-selector.example.json")
    for name in ("policy-result", "policy-evidence", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "multilingual-query-root", "training-cache-root", "output-root",
                 "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("policy_result", "policy_evidence", "configuration_protocol",
        "layout_manifest", "k8_manifest", "native_executable",
        "multilingual_query_root", "training_cache_root", "output_root", "output")
    require(all(getattr(args, name) is not None for name in required),
            "bitwise address selector inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
