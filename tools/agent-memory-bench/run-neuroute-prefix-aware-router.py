#!/usr/bin/env python3
"""Train prefix-utility heads and replay bounded local-K8 routing."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
TOPOLOGIES = ["prefix12", "prefix14", "recursive12_14"]
TARGETS = ["rank_mass", "k8_margin_mass"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


training = load("neuroute_prefix_training",
                "run-neuroute-training-sufficient-router.py")
policy = load("neuroute_prefix_policy",
              "run-neuroute-generator-policy-bakeoff.py")
bakeoff = training.bakeoff
replay = training.replay
exact = training.exact
fixed = training.fixed
require = training.require
sha256 = training.sha256
canonical = training.canonical
SEEDS = training.SEEDS


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-prefix-aware-router.py",
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
    require(value.get("family") == "neuroute_prefix_aware_router_frontier" and
            value["training"]["query_count"] == 8141 and
            value["training"]["projection_rank"] == 64 and
            value["training"]["prefix_widths"] == [12, 14] and
            value["training"]["targets"] == TARGETS and
            value["topologies"] == TOPOLOGIES and
            value["descendant_expansion_factors"] == [2, 4] and
            value["shortlist_budgets"] == [1024, 2048, 4096] and
            value["decision"]["global_address_k8_scan_forbidden"] is True and
            value["decision"]["production_selection_forbidden"] is True,
            "prefix-aware router contract differs")
    return value


def candidate_id(topology: str, target: str, ridge: float, factor: int) -> str:
    return f"{topology}-{target}-l{ridge:g}-x{factor}"


def sparse_prefix_cross(latent: np.ndarray, prefix_rows: np.ndarray,
                        values: np.ndarray, code_count: int
                        ) -> tuple[np.ndarray, str]:
    import scipy
    from scipy import sparse
    query_rows = np.repeat(np.arange(len(latent), dtype=np.int32),
                           prefix_rows.shape[1])
    matrix = sparse.coo_matrix((values.reshape(-1),
        (query_rows, prefix_rows.reshape(-1))),
        shape=(len(latent), code_count), dtype=np.float32).tocsr()
    matrix.sum_duplicates()
    cross = np.asarray((matrix.T @ latent).T, dtype=np.float64)
    require(cross.shape == (latent.shape[1], code_count),
            "prefix-aware sparse cross-product differs")
    return cross, scipy.__version__


def target_values(name: str, scores: np.ndarray) -> np.ndarray:
    discounts = (1.0 / np.log2(np.arange(scores.shape[1], dtype=np.float64) +
                               2.0)).astype(np.float32)
    if name == "rank_mass":
        return np.broadcast_to(discounts, scores.shape)
    require(name == "k8_margin_mass", "prefix-aware target differs")
    margins = np.maximum(scores - scores[:, -1:], 0.0)
    scale = np.maximum(margins.max(axis=1, keepdims=True), 1.0e-8)
    return discounts[None, :] * (0.1 + 0.9 * margins / scale)


def prefix_state(occupied: np.ndarray, width: int) -> dict[str, Any]:
    code_count = 1 << width
    row_prefix = occupied & np.uint32(code_count - 1)
    return {"width": width, "code_count": code_count,
        "row_prefix": row_prefix,
        "child_counts": np.bincount(row_prefix, minlength=code_count)}


def prefix_candidates(scores: np.ndarray, state: dict[str, Any],
                      target_rows: int, allowed_codes: np.ndarray | None = None
                      ) -> tuple[np.ndarray, int]:
    codes = (np.arange(state["code_count"], dtype=np.uint32)
             if allowed_codes is None else allowed_codes)
    counts = state["child_counts"][codes]
    occupied_codes = codes[counts > 0]
    counts = counts[counts > 0]
    order = np.lexsort((occupied_codes, -scores[occupied_codes]))
    ordered_codes = occupied_codes[order]
    prefix_count = int(np.searchsorted(np.cumsum(counts[order]),
                                       target_rows) + 1)
    prefix_count = min(prefix_count, len(ordered_codes))
    chosen_codes = ordered_codes[:prefix_count]
    chosen = np.flatnonzero(np.isin(state["row_prefix"], chosen_codes,
                                    assume_unique=False)).astype(np.uint32)
    return chosen, prefix_count


def generate_order(query: np.ndarray, occupied: np.ndarray,
                   centroids: np.ndarray, states: dict[int, dict[str, Any]],
                   prediction12: np.ndarray, prediction14: np.ndarray,
                   topology: str, factor: int, budget: int
                   ) -> tuple[np.ndarray, dict[str, int]]:
    if topology == "prefix12":
        candidates, prefixes12 = prefix_candidates(prediction12, states[12],
            min(len(occupied), factor * budget))
        prefixes14 = 0
    elif topology == "prefix14":
        candidates, prefixes14 = prefix_candidates(prediction14, states[14],
            min(len(occupied), factor * budget))
        prefixes12 = 0
    else:
        require(topology == "recursive12_14",
                "prefix-aware topology differs")
        coarse_target = min(len(occupied), 2 * factor * budget)
        coarse_rows, prefixes12 = prefix_candidates(prediction12, states[12],
                                                     coarse_target)
        allowed14 = np.unique(states[14]["row_prefix"][coarse_rows])
        candidates, prefixes14 = prefix_candidates(prediction14, states[14],
            min(len(occupied), factor * budget), allowed14)
    require(len(candidates) >= budget,
            "prefix-aware descendant beam is smaller than Top-M")
    k1_scores = centroids[candidates] @ query
    relative = replay.top_order(k1_scores, occupied[candidates], budget)
    return candidates[relative], {"prefixes12_scored": (1 << 12) if
        topology != "prefix14" else 0,
        "prefixes14_scored": ((1 << 14) if topology == "prefix14" else
            prefixes12 * 4 if topology == "recursive12_14" else 0),
        "prefixes12_selected": prefixes12,
        "prefixes14_selected": prefixes14,
        "fine_addresses_scored": len(candidates)}


def generate_candidate_orders(evaluate: np.ndarray, occupied: np.ndarray,
        centroids: np.ndarray, predictions: dict[int, np.ndarray],
        topology: str, factor: int, budgets: list[int],
        projection_ms: dict[str, float], head_ms: dict[int, dict[str, float]]
        ) -> tuple[dict[int, np.ndarray], dict[str, dict[int, float]],
                   dict[str, dict[int, dict[str, float]]]]:
    states = {width: prefix_state(occupied, width) for width in (12, 14)}
    orders = {budget: np.empty((152, budget), dtype=np.uint32)
              for budget in budgets}
    elapsed = {partition: {budget: 0.0 for budget in budgets}
               for partition in ("configuration", "internal")}
    work = {partition: {budget: {key: 0.0 for key in (
        "prefixes12_scored", "prefixes14_scored", "prefixes12_selected",
        "prefixes14_selected", "fine_addresses_scored")} for budget in budgets}
        for partition in ("configuration", "internal")}
    head_widths = ((12,) if topology == "prefix12" else (14,) if
                   topology == "prefix14" else (12, 14))
    for query_index in range(152):
        partition = "configuration" if query_index < 76 else "internal"
        for budget in budgets:
            started = time.perf_counter()
            order, current_work = generate_order(evaluate[query_index], occupied,
                centroids, states, predictions[12][query_index],
                predictions[14][query_index], topology, factor, budget)
            orders[budget][query_index] = order
            elapsed[partition][budget] += ((time.perf_counter() - started) *
                1000.0 + projection_ms[partition] + sum(
                    head_ms[width][partition] for width in head_widths))
            for key, value in current_work.items():
                work[partition][budget][key] += value
    for partition in elapsed:
        for budget in budgets:
            elapsed[partition][budget] /= 76.0
            for key in work[partition][budget]:
                work[partition][budget][key] /= 76.0
    return orders, elapsed, work


def metrics_for(value: dict[str, Any], common: dict[int, Any], budget: int,
                query_range: range, partition: str) -> dict[str, Any]:
    orders = {seed: value["orders"][budget][seed] for seed in SEEDS}
    timings = {seed: {budget: value["timings"][partition][budget][seed]}
               for seed in SEEDS}
    result = training.generator_metrics(orders, common, budget, query_range,
                                        timings, value["model_bytes"])
    for key in ("prefixes12_scored", "prefixes14_scored",
                "prefixes12_selected", "prefixes14_selected",
                "fine_addresses_scored"):
        result[f"mean_{key}"] = float(np.mean([
            value["work"][partition][budget][seed][key] for seed in SEEDS]))
    return result


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    policy_result = json.loads(args.policy_result.read_text(encoding="utf-8"))
    policy_evidence = json.loads(args.policy_evidence.read_text(encoding="utf-8"))
    require(policy_result.get("family") ==
            "neuroute_generator_policy_bakeoff_result" and
            policy_evidence.get("result_sha256") == sha256(args.policy_result) and
            policy_result["decision"]["adaptive_prefix_training_required"] is True,
            "prefix-aware policy activation differs")
    config_value = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    internal_value = dict(config_value)
    internal_value["partition"] = "reused_confirmation"
    internal_value["requests"] = parent["requests"]
    require(len(config_value["requests"]) == len(parent["requests"]) == 76,
            "prefix-aware partitions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    data = exact.load_data(parent)
    _, pool, pool_identity = training.load_pool(data,
                                                args.multilingual_query_root)
    caches = training.training_caches(args.training_cache_root, pool_identity)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    request_rows = sorted(list(config_value["requests"]) +
        list(parent["requests"]), key=lambda row: int(row["request"]))
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_native[native]
              for request, native in enumerate(native_positions)}
    budgets = list(map(int, contract["shortlist_budgets"]))
    rank = int(contract["training"]["projection_rank"])
    candidates: dict[str, dict[str, Any]] = {}
    common: dict[int, dict[str, Any]] = {}
    occupied_counts = {}
    scipy_version = None
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
        global_rows = np.fromfile(root / fixed.descriptor(
            layout_seed["mappings"], "shortlist_rows")["file"],
            dtype="<u4").reshape(152, 1024)
        scalar_features = np.fromfile(root / fixed.descriptor(
            layout_seed["mappings"], "scalar_features")["file"],
            dtype="<f4").reshape(152, 1024, 22)
        cache_identity = caches[seed]["manifest"]["identity"]
        require(cache_identity.get("prototypes_sha256") ==
                k8_seed["dense_prototypes_sha256"] and
                cache_identity.get("effective_sha256") ==
                k8_seed["effective_sha256"],
                "prefix-aware training cache topology differs")
        centroids = replay.first_prototypes(k8_seed, counts)
        cache_addresses, cache_features, _ = training.open_cache(caches[seed])
        address_to_row = np.full(1 << 16, -1, dtype=np.int32)
        address_to_row[occupied] = np.arange(len(occupied), dtype=np.int32)
        cache_rows = address_to_row[cache_addresses]
        require(np.all(cache_rows >= 0),
                "prefix-aware cache contains an unoccupied address")
        cache_rows = np.asarray(cache_rows, dtype=np.uint32)
        common[seed] = {"counts": counts, "global_rows": global_rows,
            "global_scores": np.asarray(scalar_features[:, :, 8],
                                        dtype=np.float32),
            "actionable_rows": training.actionable_rows(oracle, doc_rows[seed])}
        occupied_counts[seed] = len(occupied)
        train_z, evaluate_z, projection, projection_ms = (
            training.projected_queries(pool, evaluate, rank, 76))
        gram = train_z.T @ train_z
        prefix_rows = {width: (occupied[cache_rows] &
            np.uint32((1 << width) - 1)).astype(np.int32)
            for width in (12, 14)}
        maximum_scores = np.asarray(cache_features[:, :, 8], dtype=np.float32)
        for target in TARGETS:
            values = target_values(target, maximum_scores)
            crosses = {}
            for width in (12, 14):
                crosses[width], current_scipy = sparse_prefix_cross(train_z,
                    prefix_rows[width], values, 1 << width)
                scipy_version = current_scipy
            for ridge in contract["training"]["ridge_lambdas"]:
                regularized = gram.copy()
                regularized.flat[::len(regularized) + 1] += float(ridge)
                predictions = {}
                head_ms = {}
                model_weight_bytes = {}
                for width in (12, 14):
                    weights = np.linalg.solve(regularized, crosses[width])
                    model_weight_bytes[width] = weights.size * 4
                    started = time.perf_counter()
                    configuration_prediction = np.maximum(
                        evaluate_z[:76] @ weights, 0.0).astype(np.float32)
                    configuration_ms = ((time.perf_counter() - started) *
                                        1000.0 / 76.0)
                    started = time.perf_counter()
                    internal_prediction = np.maximum(
                        evaluate_z[76:] @ weights, 0.0).astype(np.float32)
                    internal_ms = ((time.perf_counter() - started) *
                                   1000.0 / 76.0)
                    predictions[width] = np.concatenate((
                        configuration_prediction, internal_prediction), axis=0)
                    head_ms[width] = {"configuration": configuration_ms,
                                      "internal": internal_ms}
                for topology in TOPOLOGIES:
                    widths = ((12,) if topology == "prefix12" else (14,) if
                              topology == "prefix14" else (12, 14))
                    for factor in contract["descendant_expansion_factors"]:
                        name = candidate_id(topology, target, float(ridge), factor)
                        orders, timings, work = generate_candidate_orders(evaluate,
                            occupied, centroids, predictions, topology, factor,
                            budgets, projection_ms, head_ms)
                        value = candidates.setdefault(name, {"topology": topology,
                            "target": target, "ridge_lambda": float(ridge),
                            "expansion_factor": factor,
                            "orders": {budget: {} for budget in budgets},
                            "timings": {partition: {budget: {} for budget in budgets}
                                for partition in ("configuration", "internal")},
                            "work": {partition: {budget: {} for budget in budgets}
                                for partition in ("configuration", "internal")},
                            "model_bytes": {}})
                        for budget in budgets:
                            value["orders"][budget][seed] = orders[budget]
                            for partition in ("configuration", "internal"):
                                value["timings"][partition][budget][seed] = (
                                    timings[partition][budget])
                                value["work"][partition][budget][seed] = (
                                    work[partition][budget])
                        projection_bytes = (384 + 384 * rank + rank) * 8
                        value["model_bytes"][seed] = int(centroids.nbytes +
                            projection_bytes + sum(model_weight_bytes[width]
                                                   for width in widths))
            del crosses, values
            gc.collect()
        del (cache_addresses, cache_features, cache_rows, prefix_rows,
             maximum_scores, train_z, evaluate_z, gram, predictions)
        gc.collect()
    selection_budget = int(contract["selection"]["offline_budget"])
    offline = []
    finalists = []
    for topology in TOPOLOGIES:
        rows = [value for value in candidates.values()
                if value["topology"] == topology]
        for value in rows:
            value["configuration_diagnostics"] = metrics_for(value, common,
                selection_budget, range(76), "configuration")
            offline.append({key: value[key] for key in ("topology", "target",
                "ridge_lambda", "expansion_factor",
                "configuration_diagnostics")})
        rows.sort(key=lambda value: (value["configuration_diagnostics"][
            "mean_lost_final_top10_addresses"],
            -value["configuration_diagnostics"][
                "mean_actionable_address_coverage"],
            -value["configuration_diagnostics"][
                "mean_rank_and_k8_margin_weighted_coverage"],
            -value["configuration_diagnostics"][
                "mean_rank_discounted_global_k8_coverage"],
            value["configuration_diagnostics"][
                "directional_generator_ms_per_query"],
            candidate_id(value["topology"], value["target"],
                         value["ridge_lambda"], value["expansion_factor"])))
        finalists.extend(rows[:int(contract["selection"][
            "offline_finalists_per_topology"])])
    selected_entries = {candidate_id(value["topology"], value["target"],
        value["ridge_lambda"], value["expansion_factor"]): value
        for value in finalists}
    manifests = {name: {budget: bakeoff.materialize(args.output_root /
        "shortlists" / name / f"m{budget}", name,
        value["orders"][budget], occupied_counts, args,
        {"topology": value["topology"], "target": value["target"],
         "ridge_lambda": value["ridge_lambda"],
         "expansion_factor": value["expansion_factor"],
         "address_budget": budget}) for budget in budgets}
        for name, value in selected_entries.items()}
    del candidates, pool
    gc.collect()
    gates = contract["quality_gates"]
    config_ref_protocol = replay.protocol(args.configuration_protocol, None, None,
        args.output_root / "protocols" / "configuration-reference.json", contract)
    internal_ref_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols" / "internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_ref_protocol, data, doc_rows)
    internal_inputs = replay.partition_inputs(internal_ref_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32",
                           "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration",
        config_ref_protocol, "global-fp32-k8", reference_treatment,
        config_inputs, config_references, True)
    configuration = [training.aggregate(config_reference, config_reference,
        reference_treatment, None, gates, None)]
    for name, value in selected_entries.items():
        for budget in budgets:
            point = f"{name}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol,
                manifests[name][budget], budget, args.output_root / "protocols" /
                f"configuration-{point}.json", contract)
            treatment = {"id": point, "kind": "prefix_aware_router",
                         "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol,
                point, treatment, config_inputs, config_references, False)
            configuration.append(training.aggregate(rows, config_reference,
                treatment, budget, gates, metrics_for(value, common, budget,
                                                       range(76), "configuration")))
    opened = []
    for topology in TOPOLOGIES:
        rows = [row for row in configuration if row.get("address_budget") ==
                selection_budget and any(row["id"].startswith(name + "-m")
                for name, value in selected_entries.items()
                if value["topology"] == topology)]
        opened.append(min(rows, key=lambda row: (replay.gate_distance(row, gates),
            row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"])))
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "reused_confirmation",
        internal_ref_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal = [training.aggregate(internal_reference, internal_reference,
        reference_treatment, None, gates, None)]
    internal_manifests = {}
    for selected in opened:
        name, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        value = selected_entries[name]
        manifest = manifests[name][budget]
        internal_manifests[name] = {"path": str(manifest.resolve()),
                                    "sha256": sha256(manifest)}
        protocol = replay.protocol(internal_source, manifest, budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        treatment = {"id": selected["id"], "kind": "prefix_aware_router",
                     "record_bytes": 0}
        rows = replay.run_point(args, contract, "reused_confirmation", protocol,
            selected["id"], treatment, internal_inputs, internal_references, False)
        internal.append(training.aggregate(rows, internal_reference, treatment,
            budget, gates, metrics_for(value, common, budget, range(76, 152),
                                        "internal")))
    passing = [row for row in internal if row["id"] != "global_fp32_k8" and
               row["passes_registered_gate"]]
    result = {"schema_version": 1,
        "family": "neuroute_prefix_aware_router_frontier_result",
        "inputs": {"contract_sha256": sha256(args.contract),
            "policy_result_sha256": sha256(args.policy_result),
            "policy_evidence_sha256": sha256(args.policy_evidence),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(
                args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "multilingual_query_manifest_sha256":
                pool_identity["manifest_sha256"],
            "training_pool": pool_identity,
            "training_cache_manifests_sha256": {str(seed): sha256(
                caches[seed]["path"]) for seed in SEEDS},
            "source_files_sha256": source_hashes(),
            "scipy_version": scipy_version,
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "offline_frontier": offline,
        "selected_finalists": list(selected_entries),
        "configuration": configuration,
        "reused_confirmation": internal,
        "opened_from_configuration": [row["id"] for row in opened],
        "shortlist_manifests": {name: {str(budget): {"path":
            str(path.resolve()), "sha256": sha256(path)} for budget, path in
            by_budget.items()} for name, by_budget in manifests.items()},
        "confirmation_shortlist_manifests": internal_manifests,
        "decision": {"global_fp32_k8_role":
                "offline_teacher_and_reference_only",
            "maximum_local_k8_addresses": 4096,
            "prefix_aware_passed": bool(passing),
            "passing_confirmation_rows": passing,
            "native_integration_licensed": False,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-prefix-aware-router.example.json")
    scores = np.asarray([[4.0, 3.0, 2.0, 1.0]], dtype=np.float32)
    rank = target_values("rank_mass", scores)
    margin = target_values("k8_margin_mass", scores)
    require(candidate_id("prefix12", "rank_mass", 0.1, 2) ==
            "prefix12-rank_mass-l0.1-x2" and rank.shape == margin.shape and
            np.all(rank > 0.0) and np.all(margin > 0.0) and
            contract["decision"]["maximum_local_k8_addresses"] == 4096,
            "prefix-aware router self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prefix-aware-router.example.json")
    for name in ("policy-result", "policy-evidence", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "multilingual-query-root", "training-cache-root",
                 "output-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("policy_result", "policy_evidence", "configuration_protocol",
        "layout_manifest", "k8_manifest", "native_executable",
        "multilingual_query_root", "training_cache_root", "output_root",
        "output")
    require(all(getattr(args, name) is not None for name in required),
            "prefix-aware router inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
