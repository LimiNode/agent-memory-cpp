#!/usr/bin/env python3
"""Evaluate fixed Top-M learned routers with sufficient pseudo-supervision."""
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
SEEDS = [2026082701, 2026082702, 2026082703]
LEARNED = ["direct_rank64_global", "centroid_k1_plus_exact_k8_delta",
    "centroid_k1_plus_actionable_pseudo_gain",
    "centroid_k1_plus_hybrid_residual"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bakeoff = load("neuroute_training_sufficient_bakeoff",
               "run-neuroute-shortlist-generator-bakeoff.py")
fixed = bakeoff.fixed
replay = fixed.replay
exact = fixed.exact


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


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-training-sufficient-router.py",
             "run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes
bakeoff.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") ==
            "neuroute_training_sufficient_router_frontier" and
            value["training"]["query_counts"] == [153, 8141] and
            value["training"]["projection_rank"] == 64 and
            value["shortlist_budgets"] == [1024, 2048, 4096, 8192] and
            value["treatments"] == ["centroid_k1_control", *LEARNED] and
            value["decision"]["production_selection_forbidden"] is True,
            "training-sufficient router contract differs")
    return value


def load_pool(data: dict[str, Any], root: Path) -> tuple[list[str], np.ndarray,
                                                         dict[str, Any]]:
    prefix = b"neuroute-v3-de-v1\0"
    query_ids = [str(value) for value in data["query_ids"]]
    ordered = sorted(query_ids, key=lambda value: (
        hashlib.sha256(prefix + value.encode("utf-8")).digest(), value))
    german_ids = ordered[:153]
    positions = {value: index for index, value in enumerate(query_ids)}
    german = np.asarray(data["queries"][[positions[value]
        for value in german_ids]], dtype=np.float32)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    id_path = root / manifest["outputs"]["query_ids"]["path"]
    vector_path = root / manifest["outputs"]["query_vectors"]["path"]
    require(sha256(id_path) == manifest["outputs"]["query_ids"]["sha256"] and
            sha256(vector_path) ==
            manifest["outputs"]["query_vectors"]["sha256"],
            "multilingual query payload differs")
    external_ids = [json.loads(line)["id"] for line in
                    id_path.read_text(encoding="utf-8").splitlines()]
    external = np.fromfile(vector_path, dtype="<f4").reshape(-1, 384)
    require(len(external_ids) == len(external) == 7988,
            "multilingual query count differs")
    ids = german_ids + external_ids
    vectors = np.ascontiguousarray(np.concatenate((german, external), axis=0),
                                   dtype=np.float32)
    require(len(ids) == len(vectors) == 8141,
            "training query pool differs")
    return ids, vectors, {"manifest_sha256": sha256(manifest_path),
        "query_ids_sha256": exact.scale.hash_ids(np.asarray(ids, dtype=object)),
        "query_vectors_sha256": hashlib.sha256(vectors.tobytes()).hexdigest()}


def training_caches(root: Path, identity: dict[str, Any]
                    ) -> dict[int, dict[str, Any]]:
    matches: dict[int, list[dict[str, Any]]] = {seed: [] for seed in SEEDS}
    for path in root.rglob("manifest.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        cache = value.get("identity", {})
        seed = int(cache.get("seed", 0))
        if (value.get("family") == "neuroute_nonlinear_listwise_training_cache" and
                seed in SEEDS and cache.get("query_ids_sha256") ==
                identity["query_ids_sha256"] and
                cache.get("query_vectors_sha256") ==
                identity["query_vectors_sha256"]):
            matches[seed].append({"path": path, "manifest": value})
    require(all(len(matches[seed]) == 1 for seed in SEEDS),
            "training cache set is missing or ambiguous")
    return {seed: matches[seed][0] for seed in SEEDS}


def open_cache(binding: dict[str, Any]) -> tuple[np.ndarray, np.ndarray,
                                                 np.ndarray]:
    root = binding["path"].parent
    value = binding["manifest"]
    arrays = []
    for name in ("shortlists", "features", "targets"):
        path = root / value["outputs"][name]["path"]
        require(sha256(path) == value["outputs"][name]["sha256"],
                f"training cache payload differs: {name}")
        arrays.append(np.load(path, mmap_mode="r"))
    require(arrays[0].shape == arrays[2].shape == (8141, 1024) and
            arrays[1].shape == (8141, 1024, 22),
            "training cache shape differs")
    return arrays[0], arrays[1], arrays[2]


def projected_queries(train: np.ndarray, evaluate: np.ndarray, rank: int,
                      partition_size: int | None = None
                      ) -> tuple[np.ndarray, np.ndarray, dict[str, Any],
                                 dict[str, float]]:
    mean = train.mean(axis=0, dtype=np.float64)
    centered = train.astype(np.float64) - mean
    covariance = centered.T @ centered
    values, vectors = np.linalg.eigh(covariance)
    basis = vectors[:, np.argsort(values)[::-1][:rank]]
    train_z = centered @ basis
    deviation = train_z.std(axis=0, dtype=np.float64)
    deviation[deviation < 1.0e-8] = 1.0
    train_z /= deviation
    train_z = np.column_stack((train_z, np.ones(len(train_z))))
    split = len(evaluate) if partition_size is None else partition_size
    require(0 < split <= len(evaluate), "projection timing partition differs")
    started = time.perf_counter()
    config_z = ((evaluate[:split].astype(np.float64) - mean) @ basis) / deviation
    config_z = np.column_stack((config_z, np.ones(len(config_z))))
    config_ms = (time.perf_counter() - started) * 1000.0 / len(config_z)
    if split < len(evaluate):
        started = time.perf_counter()
        internal_z = (((evaluate[split:].astype(np.float64) - mean) @ basis) /
                      deviation)
        internal_z = np.column_stack((internal_z, np.ones(len(internal_z))))
        internal_ms = ((time.perf_counter() - started) * 1000.0 /
                       len(internal_z))
        evaluate_z = np.concatenate((config_z, internal_z), axis=0)
    else:
        internal_ms = config_ms
        evaluate_z = config_z
    return train_z, evaluate_z, {"rank": rank,
        "mean_sha256": hashlib.sha256(mean.tobytes()).hexdigest(),
        "basis_sha256": hashlib.sha256(basis.tobytes()).hexdigest(),
        "deviation_sha256": hashlib.sha256(deviation.tobytes()).hexdigest()}, {
            "configuration": config_ms, "internal": internal_ms}


def cross_product(latent: np.ndarray, rows: np.ndarray,
                  values: np.ndarray, row_count: int) -> np.ndarray:
    cross = np.zeros((latent.shape[1], row_count), dtype=np.float64)
    for query in range(len(latent)):
        cross[:, rows[query]] += (latent[query, :, None] *
                                  values[query, None, :])
    return cross


def row_standardize(values: np.ndarray) -> np.ndarray:
    mean = values.mean(axis=1, keepdims=True, dtype=np.float64)
    deviation = values.std(axis=1, keepdims=True, dtype=np.float64)
    return ((values - mean) / np.maximum(deviation, 1.0e-8)).astype(np.float32)


def top_orders(scores: np.ndarray, occupied: np.ndarray,
               maximum: int) -> np.ndarray:
    return np.asarray([replay.top_order(row, occupied, maximum)
                       for row in scores], dtype=np.uint32)


def timed_top_orders(scores: np.ndarray, occupied: np.ndarray,
                     budgets: list[int]) -> tuple[np.ndarray, dict[int, float]]:
    maximum = max(budgets)
    maximum_orders = None
    timings = {}
    for budget in budgets:
        started = time.perf_counter()
        orders = top_orders(scores, occupied, budget)
        timings[budget] = ((time.perf_counter() - started) * 1000.0 /
                           len(scores))
        if budget == maximum:
            maximum_orders = orders
    require(maximum_orders is not None, "maximum Top-M order differs")
    return maximum_orders, timings


def selected_k1(queries: np.ndarray, centroids: np.ndarray,
                rows: np.ndarray) -> np.ndarray:
    result = np.empty(rows.shape, dtype=np.float32)
    for start in range(0, len(queries), 16):
        stop = min(start + 16, len(queries))
        result[start:stop] = np.einsum("bd,bkd->bk", queries[start:stop],
            centroids[rows[start:stop]], dtype=np.float32, optimize=True)
    return result


def actionable_rows(oracle: dict[int, np.ndarray], doc_rows: np.ndarray
                    ) -> list[np.ndarray]:
    return [np.unique(doc_rows[np.asarray(oracle[query], dtype=np.int64)])
            for query in range(152)]


def generator_metrics(orders: dict[int, np.ndarray], common: dict[int, Any],
                      budget: int, query_range: range,
                      timings: dict[int, dict[int, float]],
                      model_bytes: dict[int, int] | None = None
                      ) -> dict[str, Any]:
    unweighted, weighted, margin_weighted, actionable, missed_ranks, lost = (
        [], [], [], [], [], [])
    prototypes = []
    discounts = 1.0 / np.log2(np.arange(1024, dtype=np.float64) + 2.0)
    denominator = float(discounts.sum())
    for seed in SEEDS:
        for query in query_range:
            chosen = set(map(int, orders[seed][query, :budget]))
            teacher = common[seed]["global_rows"][query]
            teacher_scores = common[seed]["global_scores"][query]
            mask = np.asarray([int(row) in chosen for row in teacher])
            unweighted.append(float(mask.mean()))
            weighted.append(float(discounts[mask].sum()) / denominator)
            margins = np.maximum(teacher_scores - teacher_scores[-1], 0.0)
            utilities = margins.astype(np.float64) * discounts
            margin_denominator = float(utilities.sum())
            margin_weighted.append(float(utilities[mask].sum()) /
                margin_denominator if margin_denominator > 0.0 else
                float(discounts[mask].sum()) / denominator)
            useful = common[seed]["actionable_rows"][query]
            kept = sum(int(row) in chosen for row in useful)
            actionable.append(kept / max(1, len(useful)))
            lost.append(len(useful) - kept)
            missed_ranks.extend((np.flatnonzero(~mask) + 1).tolist())
            selected = orders[seed][query, :budget]
            prototypes.append(int(np.minimum(
                common[seed]["counts"][selected], 8).sum()))
    return {"mean_global_k8_top1024_coverage": float(np.mean(unweighted)),
        "mean_rank_discounted_global_k8_coverage": float(np.mean(weighted)),
        "mean_rank_and_k8_margin_weighted_coverage":
            float(np.mean(margin_weighted)),
        "mean_actionable_address_coverage": float(np.mean(actionable)),
        "mean_lost_final_top10_addresses": float(np.mean(lost)),
        "mean_missed_teacher_rank": (float(np.mean(missed_ranks))
                                      if missed_ranks else None),
        "mean_k8_prototypes_scored": float(np.mean(prototypes)),
        "mean_generator_model_or_index_bytes": (float(np.mean(list(
            model_bytes.values()))) if model_bytes is not None else None),
        "directional_generator_ms_per_query": float(np.mean(
            [timings[seed][budget] for seed in SEEDS]))}


def aggregate(rows: list[dict[str, Any]], reference: list[dict[str, Any]],
              treatment: dict[str, Any], budget: int | None,
              gates: dict[str, Any], offline: dict[str, Any] | None
              ) -> dict[str, Any]:
    value = replay.aggregate(rows, reference, treatment, budget, gates, offline)
    reference_ndcg = {(row["seed"], row["request"]): row["ndcg_at_10"]
                      for row in reference}
    losses = [max(0.0, float(reference_ndcg[(row["seed"], row["request"])]) -
                  float(row["ndcg_at_10"])) for row in rows]
    value["maximum_query_ndcg_loss"] = max(losses)
    value["p95_query_ndcg_loss"] = float(np.percentile(losses, 95.0))
    return value


def candidate_id(treatment: str, count: int, ridge: float,
                 weight: float | None) -> str:
    suffix = f"-{weight:g}" if weight is not None else ""
    return f"{treatment}-n{count}-l{ridge:g}{suffix}"


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    generator_result = json.loads(args.generator_result.read_text(encoding="utf-8"))
    require(generator_result.get("family") ==
            "neuroute_shortlist_generator_bakeoff_result",
            "shortlist-generator comparison parent differs")
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    internal_value = dict(config_value)
    internal_value["partition"] = "locked_internal"
    internal_value["requests"] = parent["requests"]
    require(len(config_value["requests"]) == len(parent["requests"]) == 76,
            "training-sufficient partitions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    data = exact.load_data(parent)
    pool_ids, pool, pool_identity = load_pool(data, args.multilingual_query_root)
    caches = training_caches(args.training_cache_root, pool_identity)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    request_rows = sorted(list(config_value["requests"]) + list(parent["requests"]),
                          key=lambda row: int(row["request"]))
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_native[native]
              for request, native in enumerate(native_positions)}
    maximum = max(contract["shortlist_budgets"])
    selection_budget = max(budget for budget in contract["shortlist_budgets"]
        if budget <= contract["quality_gates"]["maximum_native_address_budget"])
    candidates: dict[str, dict[str, Any]] = {}
    common: dict[int, dict[str, Any]] = {}
    occupied_counts = {}
    k1_orders, k1_config_ms, k1_internal_ms = {}, {}, {}
    discounts = (1.0 / np.log2(np.arange(1024, dtype=np.float64) + 2.0)
                 ).astype(np.float32)
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
        scalar_features = np.fromfile(root / fixed.descriptor(
            layout_seed["mappings"], "scalar_features")["file"],
            dtype="<f4").reshape(152, 1024, 22)
        cache_identity = caches[seed]["manifest"]["identity"]
        require(cache_identity.get("prototypes_sha256") ==
                k8_seed["dense_prototypes_sha256"] and
                cache_identity.get("effective_sha256") ==
                k8_seed["effective_sha256"],
                "training cache K8 topology differs")
        centroids = replay.first_prototypes(k8_seed, counts)
        cache_addresses, cache_features, cache_targets = open_cache(caches[seed])
        address_to_row = np.full(1 << 16, -1, dtype=np.int32)
        address_to_row[occupied] = np.arange(len(occupied), dtype=np.int32)
        cache_rows = address_to_row[cache_addresses]
        require(np.all(cache_rows >= 0),
                "training cache contains an unoccupied address")
        cache_rows = np.asarray(cache_rows, dtype=np.uint32)
        common[seed] = {"counts": counts, "global_rows": global_rows,
            "global_scores": np.asarray(scalar_features[:, :, 8],
                                        dtype=np.float32),
            "actionable_rows": actionable_rows(oracle, doc_rows[seed])}
        occupied_counts[seed] = len(occupied)
        started = time.perf_counter()
        config_k1 = evaluate[:76] @ centroids.T
        config_k1_score_ms = ((time.perf_counter() - started) * 1000.0 /
                              76.0)
        started = time.perf_counter()
        internal_k1 = evaluate[76:] @ centroids.T
        internal_k1_score_ms = ((time.perf_counter() - started) * 1000.0 /
                                76.0)
        config_k1_orders, config_k1_select_ms = timed_top_orders(
            config_k1, occupied, contract["shortlist_budgets"])
        internal_k1_orders, internal_k1_select_ms = timed_top_orders(
            internal_k1, occupied, contract["shortlist_budgets"])
        k1_orders[seed] = np.concatenate(
            (config_k1_orders, internal_k1_orders), axis=0)
        k1_config_ms[seed] = {budget: config_k1_score_ms + elapsed
            for budget, elapsed in config_k1_select_ms.items()}
        k1_internal_ms[seed] = {budget: internal_k1_score_ms + elapsed
            for budget, elapsed in internal_k1_select_ms.items()}
        k1_at_teacher = selected_k1(pool, centroids, cache_rows)
        maximum_scores = np.asarray(cache_features[:, :, 8], dtype=np.float32)
        delta = maximum_scores - k1_at_teacher
        actionable = np.asarray(cache_targets, dtype=np.float32)
        delta_scale = np.maximum(np.abs(delta).max(axis=1, keepdims=True),
                                 1.0e-8)
        action_scale = np.maximum(actionable.max(axis=1, keepdims=True), 1.0e-8)
        targets = {"direct_rank64_global": np.broadcast_to(discounts,
                    cache_rows.shape),
            "centroid_k1_plus_exact_k8_delta": delta,
            "centroid_k1_plus_actionable_pseudo_gain": actionable,
            "centroid_k1_plus_hybrid_residual":
                0.5 * delta / delta_scale + 0.5 * actionable / action_scale}
        for count in contract["training"]["query_counts"]:
            train_z, evaluate_z, projection, projection_timings = projected_queries(
                pool[:count], evaluate,
                int(contract["training"]["projection_rank"]), 76)
            gram = train_z.T @ train_z
            for treatment in LEARNED:
                cross = cross_product(train_z, cache_rows[:count],
                                      targets[treatment][:count], len(occupied))
                for ridge in contract["training"]["ridge_lambdas"]:
                    regularized = gram.copy()
                    regularized.flat[::len(regularized) + 1] += float(ridge)
                    weights = np.linalg.solve(regularized, cross)
                    started = time.perf_counter()
                    config_prediction = np.asarray(
                        evaluate_z[:76] @ weights, dtype=np.float32)
                    if treatment in ("direct_rank64_global",
                            "centroid_k1_plus_actionable_pseudo_gain"):
                        np.maximum(config_prediction, 0.0,
                                   out=config_prediction)
                    config_elapsed = ((time.perf_counter() - started) *
                                      1000.0 / 76.0)
                    started = time.perf_counter()
                    internal_prediction = np.asarray(
                        evaluate_z[76:] @ weights, dtype=np.float32)
                    if treatment in ("direct_rank64_global",
                            "centroid_k1_plus_actionable_pseudo_gain"):
                        np.maximum(internal_prediction, 0.0,
                                   out=internal_prediction)
                    internal_elapsed = ((time.perf_counter() - started) *
                                        1000.0 / 76.0)
                    if treatment == "direct_rank64_global":
                        variants = [None]
                    else:
                        variants = [float(weight) for weight in
                            contract["training"]["residual_weights"]]
                    config_standardize_ms = 0.0
                    internal_standardize_ms = 0.0
                    if treatment not in ("direct_rank64_global",
                            "centroid_k1_plus_exact_k8_delta"):
                        started = time.perf_counter()
                        config_predicted = row_standardize(config_prediction)
                        config_base = row_standardize(config_k1)
                        config_standardize_ms = ((time.perf_counter() - started) *
                                                 1000.0 / 76.0)
                        started = time.perf_counter()
                        internal_predicted = row_standardize(internal_prediction)
                        internal_base = row_standardize(internal_k1)
                        internal_standardize_ms = ((time.perf_counter() - started) *
                                                   1000.0 / 76.0)
                    for weight in variants:
                        started = time.perf_counter()
                        if treatment == "direct_rank64_global":
                            config_scores = config_prediction
                        elif treatment == "centroid_k1_plus_exact_k8_delta":
                            config_scores = config_k1 + weight * config_prediction
                        else:
                            config_scores = config_base + weight * config_predicted
                        config_combine_ms = ((time.perf_counter() - started) *
                                             1000.0 / 76.0)
                        started = time.perf_counter()
                        if treatment == "direct_rank64_global":
                            internal_scores = internal_prediction
                        elif treatment == "centroid_k1_plus_exact_k8_delta":
                            internal_scores = internal_k1 + weight * internal_prediction
                        else:
                            internal_scores = internal_base + weight * internal_predicted
                        internal_combine_ms = ((time.perf_counter() - started) *
                                               1000.0 / 76.0)
                        config_orders, config_select_ms = timed_top_orders(
                            config_scores, occupied, contract["shortlist_budgets"])
                        internal_orders, internal_select_ms = timed_top_orders(
                            internal_scores, occupied, contract["shortlist_budgets"])
                        name = candidate_id(treatment, count, float(ridge), weight)
                        entry = candidates.setdefault(name, {"treatment": treatment,
                            "training_query_count": count, "ridge_lambda": float(ridge),
                            "residual_weight": weight, "orders": {},
                            "config_timings": {}, "internal_timings": {},
                            "projection": {}, "model_bytes": {}})
                        entry["orders"][seed] = np.concatenate(
                            (config_orders, internal_orders), axis=0)
                        residual_config_ms = (0.0 if treatment ==
                            "direct_rank64_global" else config_k1_score_ms)
                        residual_internal_ms = (0.0 if treatment ==
                            "direct_rank64_global" else internal_k1_score_ms)
                        entry["config_timings"][seed] = {budget:
                            projection_timings["configuration"] + config_elapsed +
                            residual_config_ms +
                            config_standardize_ms + config_combine_ms + elapsed
                            for budget, elapsed in config_select_ms.items()}
                        entry["internal_timings"][seed] = {budget:
                            projection_timings["internal"] + internal_elapsed +
                            residual_internal_ms +
                            internal_standardize_ms + internal_combine_ms + elapsed
                            for budget, elapsed in internal_select_ms.items()}
                        entry["projection"][seed] = projection
                        projection_bytes = (384 + 384 * int(projection["rank"]) +
                                            int(projection["rank"])) * 8
                        entry["model_bytes"][seed] = int(weights.nbytes +
                            projection_bytes + (0 if treatment ==
                            "direct_rank64_global" else centroids.nbytes))
        del (cache_addresses, cache_features, cache_targets, cache_rows,
             k1_at_teacher, maximum_scores, delta, actionable, targets,
             train_z, evaluate_z, gram, cross, regularized, weights,
             config_prediction, internal_prediction, config_scores,
             internal_scores, config_orders, internal_orders)
        gc.collect()
    selected_hyperparameters = []
    for treatment in LEARNED:
        for count in contract["training"]["query_counts"]:
            rows = [value for value in candidates.values()
                    if value["treatment"] == treatment and
                    value["training_query_count"] == count]
            for value in rows:
                value["configuration_diagnostics"] = generator_metrics(
                    value["orders"], common, selection_budget, range(76),
                    value["config_timings"], value["model_bytes"])
            rows.sort(key=lambda value: (-value["configuration_diagnostics"][
                "mean_rank_discounted_global_k8_coverage"],
                -value["configuration_diagnostics"]["mean_actionable_address_coverage"],
                -value["configuration_diagnostics"]["mean_global_k8_top1024_coverage"],
                candidate_id(value["treatment"], value["training_query_count"],
                    value["ridge_lambda"], value["residual_weight"])))
            selected_hyperparameters.append(rows[0])
    selected_entries = {candidate_id(value["treatment"],
        value["training_query_count"], value["ridge_lambda"],
        value["residual_weight"]): value for value in selected_hyperparameters}
    del candidates, pool
    gc.collect()
    all_entries = {"centroid_k1_control": {"orders": k1_orders,
        "config_timings": k1_config_ms, "internal_timings": k1_internal_ms,
        "model_bytes": {seed: int(common[seed]["counts"].size * 384 * 4)
                        for seed in SEEDS}}}
    for name, value in selected_entries.items():
        all_entries[name] = value
    manifests = {name: bakeoff.materialize(args.output_root / "shortlists" / name,
        name, value["orders"], occupied_counts, args, {key: value[key]
        for key in ("treatment", "training_query_count", "ridge_lambda",
                    "residual_weight") if key in value})
        for name, value in all_entries.items()}
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
    configuration = [aggregate(config_reference, config_reference,
        reference_treatment, None, gates, None)]
    for name, value in all_entries.items():
        for budget in contract["shortlist_budgets"]:
            point = f"{name}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol,
                manifests[name], budget, args.output_root / "protocols" /
                f"configuration-{point}.json", contract)
            treatment = {"id": point, "kind": "learned_router", "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol,
                point, treatment, config_inputs, config_references, False)
            configuration.append(aggregate(rows, config_reference,
                treatment, budget, gates, generator_metrics(value["orders"], common,
                    budget, range(76), value["config_timings"],
                    value["model_bytes"])))
    family_rows = []
    for name in all_entries:
        rows = [row for row in configuration if row.get("address_budget") and
                row["id"].startswith(name + "-m")]
        passing = [row for row in rows if row["passes_registered_gate"]]
        family_rows.append(min(passing, key=lambda row: (row["address_budget"],
            row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))
            if passing else min(rows,
            key=lambda row: (replay.gate_distance(row, gates),
                             row["address_budget"], row["id"])))
    family_rows.sort(key=lambda row: (not row["passes_registered_gate"],
        replay.gate_distance(row, gates), row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
    opened = []
    opened_treatments = set()
    for row in family_rows:
        name = row["id"].rsplit("-m", 1)[0]
        treatment = all_entries[name].get("treatment", name)
        if treatment == "centroid_k1_control" or treatment in opened_treatments:
            continue
        opened.append(row)
        opened_treatments.add(treatment)
        if len(opened) == 2:
            break
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "locked_internal",
        internal_ref_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal = [aggregate(internal_reference, internal_reference,
        reference_treatment, None, gates, None)]
    internal_manifests = {}
    for selected in opened:
        name, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        value = all_entries[name]
        internal_manifests[name] = {"path": str(manifests[name].resolve()),
                                    "sha256": sha256(manifests[name])}
        protocol = replay.protocol(internal_source, manifests[name], budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        treatment = {"id": selected["id"], "kind": "learned_router",
                     "record_bytes": 0}
        rows = replay.run_point(args, contract, "locked_internal", protocol,
            selected["id"], treatment, internal_inputs, internal_references, False)
        internal.append(aggregate(rows, internal_reference, treatment,
            budget, gates, generator_metrics(value["orders"], common, budget,
                range(76, 152), value["internal_timings"],
                value["model_bytes"])))
    result = {"schema_version": 1,
        "family": "neuroute_training_sufficient_router_frontier_result",
        "inputs": {"contract_sha256": sha256(args.contract),
            "generator_result_sha256": sha256(args.generator_result),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "multilingual_query_manifest_sha256":
                pool_identity["manifest_sha256"],
            "training_pool": pool_identity,
            "training_cache_manifests_sha256": {str(seed): sha256(
                caches[seed]["path"]) for seed in SEEDS},
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "selected_hyperparameters": [{key: value[key] for key in
            ("treatment", "training_query_count", "ridge_lambda",
             "residual_weight", "configuration_diagnostics")}
            for value in selected_hyperparameters],
        "hyperparameter_selection_budget": selection_budget,
        "configuration": configuration, "locked_internal": internal,
        "internal_opened_from_configuration": [row["id"] for row in opened],
        "shortlist_manifests": {name: {"path": str(path.resolve()),
            "sha256": sha256(path)} for name, path in manifests.items()},
        "internal_shortlist_manifests": internal_manifests,
        "decision": {"global_fp32_k8_role": "offline_teacher_and_reference_only",
            "common_generator_bakeoff_required": True,
            "native_integration_licensed": False,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    values = np.asarray([[3.0, 1.0, 2.0]], dtype=np.float32)
    standardized = row_standardize(values)
    train = np.eye(4, dtype=np.float32)
    train_z, evaluate_z, projection, projection_timings = projected_queries(
        train, train, 2)
    rows = np.asarray([[0, 2], [1, 2]], dtype=np.uint32)
    latent = np.asarray([[1.0, 2.0], [3.0, 4.0]])
    targets = np.asarray([[1.0, 0.5], [2.0, 1.0]])
    cross = cross_product(latent, rows, targets, 3)
    orders, timings = timed_top_orders(values,
        np.asarray([10, 11, 12], dtype=np.uint32), [1, 2])
    require(abs(float(standardized.mean())) < 1.0e-6 and
            candidate_id("x", 153, 0.1, 2.0) == "x-n153-l0.1-2" and
            train_z.shape == evaluate_z.shape == (4, 3) and
            projection["rank"] == 2 and set(projection_timings) == {
                "configuration", "internal"} and cross.shape == (2, 3) and
            np.allclose(cross[:, 2], [3.5, 5.0]) and
            orders.tolist() == [[0, 2]] and set(timings) == {1, 2} and
            all(value >= 0.0 for value in timings.values()),
            "training-sufficient router self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-training-sufficient-router.example.json")
    for name in ("generator-result", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "multilingual-query-root", "training-cache-root",
                 "output-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("generator_result", "configuration_protocol", "layout_manifest",
        "k8_manifest", "native_executable", "multilingual_query_root",
        "training_cache_root", "output_root", "output")
    require(all(getattr(args, name) is not None for name in required),
            "training-sufficient router inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
