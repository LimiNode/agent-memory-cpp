#!/usr/bin/env python3
"""Measure whether E5 address centroids expose the static gain-density teacher."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy


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


planner = load("neuroute_address_centroid_planner",
               "plan-neuroute-address-centroid-learnability.py")
sequential = load("neuroute_address_centroid_parent",
                  "run-neuroute-sequential-oracle-diagnostic.py")
scale = sequential.scale
task = sequential.task


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.ascontiguousarray(value).tobytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-address-centroid-learnability.py",
        "run-neuroute-address-centroid-learnability.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
        "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-task-aware-probe-scheduler.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "sequential_result_sha256": sha256(args.sequential_result),
        "sequential_evidence_sha256": sha256(args.sequential_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root /
                                                 "manifest.json"),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == contract["activation"],
            "address-centroid activation bytes differ")
    parent = json.loads(args.sequential_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.sequential_evidence.read_text(encoding="utf-8"))
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_sequential_oracle_diagnostic_result"
            and parent.get("decision", {}).get("student_followup_licensed") is False
            and parent.get("decision", {}).get("production_selection_licensed") is False,
            "address-centroid sequential parent differs")
    require(evidence.get("family") == "neuroute_sequential_oracle_diagnostic_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["sequential_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "address-centroid parent evidence differs")
    split = split_result["split"]
    require(len(split["configuration_selection_query_ids"])
            == contract["partition"]["queries"],
            "address-centroid configuration partition differs")
    require(set(split["configuration_selection_query_ids"]).isdisjoint(
        split["internal_evaluation_query_ids"]),
        "address-centroid forbidden evaluation partition overlaps configuration")
    return parent, materialization, split


def build_centroids(documents: numpy.ndarray, addresses: numpy.ndarray,
                    counts: numpy.ndarray, batch_size: int = 65536) -> tuple[
                        numpy.ndarray, numpy.ndarray]:
    dimensions = int(documents.shape[1])
    sums = numpy.zeros((counts.size, dimensions), dtype=numpy.float32)
    for start in range(0, len(addresses), batch_size):
        stop = min(len(addresses), start + batch_size)
        local_addresses = numpy.asarray(addresses[start:stop], dtype=numpy.uint32)
        order = numpy.argsort(local_addresses, kind="stable")
        sorted_addresses = local_addresses[order]
        starts = numpy.r_[0, numpy.flatnonzero(sorted_addresses[1:] !=
                                               sorted_addresses[:-1]) + 1]
        unique = sorted_addresses[starts]
        vectors = numpy.asarray(documents[start:stop], dtype=numpy.float32)[order]
        reduced = numpy.add.reduceat(vectors, starts, axis=0, dtype=numpy.float32)
        sums[unique] += reduced
    occupied = numpy.flatnonzero(counts > 0).astype(numpy.uint32)
    centroids = sums[occupied] / counts[occupied, None].astype(numpy.float32)
    norms = numpy.linalg.norm(centroids, axis=1).astype(numpy.float32)
    nonzero = norms > 0.0
    centroids[nonzero] /= norms[nonzero, None]
    return occupied, centroids


def ordered_addresses(occupied: numpy.ndarray, similarities: numpy.ndarray,
                      counts: numpy.ndarray, alpha: float) -> tuple[numpy.ndarray, numpy.ndarray]:
    divisor = numpy.power(counts[occupied].astype(numpy.float64), alpha)
    scores = similarities.astype(numpy.float64) / divisor
    order = numpy.lexsort((occupied, -scores))
    return occupied[order].astype(numpy.uint32), scores


def average_precision(order: numpy.ndarray, positives: set[int]) -> float:
    hits = 0
    precisions = []
    for rank, address in enumerate(order.tolist(), 1):
        if int(address) in positives:
            hits += 1
            precisions.append(hits / rank)
    return float(numpy.mean(precisions, dtype=numpy.float64)) if precisions else 0.0


def hard_negative_auc(occupied: numpy.ndarray, scores: numpy.ndarray,
                      positives: set[int], pool_size: int) -> float:
    positions = {int(address): index for index, address in enumerate(occupied.tolist())}
    positive_scores = numpy.asarray([scores[positions[address]] for address in sorted(positives)],
                                    dtype=numpy.float64)
    if not positive_scores.size:
        return 0.0
    order = numpy.lexsort((occupied, -scores))
    negative_indices = [int(index) for index in order
                        if int(occupied[index]) not in positives][:pool_size]
    if not negative_indices:
        return 1.0
    negatives = scores[numpy.asarray(negative_indices, dtype=numpy.int64)]
    greater = (positive_scores[:, None] > negatives[None, :]).sum(dtype=numpy.float64)
    ties = (positive_scores[:, None] == negatives[None, :]).sum(dtype=numpy.float64)
    return float((greater + 0.5 * ties) / (positive_scores.size * negatives.size))


def relevant_density_pairwise_accuracy(occupied: numpy.ndarray, scores: numpy.ndarray,
                                       gains: dict[int, float], counts: numpy.ndarray) -> float:
    positions = {int(address): index for index, address in enumerate(occupied.tolist())}
    addresses = sorted(gains)
    correct = 0.0
    comparisons = 0
    for left_position, left in enumerate(addresses):
        for right in addresses[left_position + 1:]:
            left_density = gains[left] / max(int(counts[left]), 1)
            right_density = gains[right] / max(int(counts[right]), 1)
            if left_density == right_density:
                continue
            expected = left_density > right_density
            left_score = float(scores[positions[left]])
            right_score = float(scores[positions[right]])
            comparisons += 1
            if left_score == right_score:
                correct += 0.5
            elif (left_score > right_score) == expected:
                correct += 1.0
    return correct / comparisons if comparisons else 1.0


def global_budget_rows(order: numpy.ndarray, gains: dict[int, float],
                       budgets: list[int]) -> list[dict[str, Any]]:
    targets = set(gains)
    total_gain = sum(gains.values())
    rows = []
    for budget in budgets:
        selected = set(int(value) for value in order[:budget].tolist())
        rows.append({
            "address_budget": budget,
            "target_address_recall": len(selected & targets) / max(len(targets), 1),
            "discounted_target_gain_coverage": sum(gains.get(value, 0.0)
                                                     for value in selected) /
                                                max(total_gain, 1.0e-30),
        })
    return rows


def summarize_queries(queries: list[dict[str, Any]], alpha: float,
                      seed: int, contract: dict[str, Any],
                      baseline: dict[str, Any]) -> dict[str, Any]:
    coverage = []
    for target in contract["diagnostic"]["coverage_targets"]:
        rows = [next(row for row in query["thresholds"]
                     if row["coverage_target"] == target) for query in queries]
        coverage.append({
            "coverage_target": target,
            "reach_rate": float(numpy.mean([row["reached"] for row in rows])),
            "censored_candidate_fraction": {
                "mean": float(numpy.mean([row["candidate_fraction"] for row in rows],
                                         dtype=numpy.float64)),
                "p50": float(numpy.quantile([row["candidate_fraction"] for row in rows], 0.5)),
                "p95": float(numpy.quantile([row["candidate_fraction"] for row in rows], 0.95)),
            },
        })
    budgets = []
    for budget in contract["diagnostic"]["global_address_budgets"]:
        rows = [next(row for row in query["global_budgets"]
                     if row["address_budget"] == budget) for query in queries]
        budgets.append({
            "address_budget": budget,
            "target_address_recall": float(numpy.mean([
                row["target_address_recall"] for row in rows], dtype=numpy.float64)),
            "discounted_target_gain_coverage": float(numpy.mean([
                row["discounted_target_gain_coverage"] for row in rows],
                dtype=numpy.float64)),
        })
    return {
        "seed": seed,
        "cost_alpha": alpha,
        "query_count": len(queries),
        "classification": {
            "global_average_precision": float(numpy.mean([
                row["global_average_precision"] for row in queries], dtype=numpy.float64)),
            "hard_negative_pairwise_auc": float(numpy.mean([
                row["hard_negative_pairwise_auc"] for row in queries],
                dtype=numpy.float64)),
        },
        "relevant_only_ranking": {
            "density_pairwise_accuracy": float(numpy.mean([
                row["relevant_density_pairwise_accuracy"] for row in queries],
                dtype=numpy.float64)),
        },
        "global_budgets": budgets,
        "coverage": coverage,
        "occupied_logit_baseline": baseline,
        "queries": queries,
    }


def parent_baseline(parent: dict[str, Any], dataset: str, seed: int,
                    target: float) -> dict[str, Any]:
    source = next(row for row in next(item for item in parent["datasets"]
                                     if item["id"] == dataset)["rows"]
                  if int(row["seed"]) == seed and row["treatment"] == "occupied_logit")
    coverage = next(row for row in source["coverage"] if row["coverage_target"] == target)
    return {
        "coverage_target": target,
        "reach_rate": coverage["reach_rate"],
        "censored_candidate_fraction": coverage["censored_candidate_fraction"]["mean"],
    }


def evaluate(contract: dict[str, Any], parent: dict[str, Any],
             materialization: dict[str, Any], split: dict[str, Any],
             args: argparse.Namespace) -> list[dict[str, Any]]:
    discounts = 1.0 / numpy.log2(numpy.arange(contract["cascade"]["oracle_k"],
                                               dtype=numpy.float64) + 2.0)
    oracle_contract = {
        "evaluation": {
            "candidate_mass_target": contract["diagnostic"]["candidate_mass_target"],
            "coverage_targets": contract["diagnostic"]["coverage_targets"],
        },
        "cascade": contract["cascade"],
    }
    datasets = []
    for config in contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        ids = split["configuration_selection_query_ids"]
        positions = [by_id[value] for value in ids]
        oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in materialization["datasets"]
                                if row["id"] == config["id"])
        rows = []
        prototypes = []
        for seed in contract["route"]["seeds"]:
            route = task.route_entry(manifest_dataset, 16, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(
                route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, 16)
            occupied, centroids = build_centroids(data["documents"], addresses,
                                                   index["counts"])
            similarities = numpy.asarray(data["queries"][positions] @ centroids.T,
                                         dtype=numpy.float32)
            prototypes.append({
                "seed": seed,
                "occupied_address_count": int(occupied.size),
                "centroid_shape": [int(value) for value in centroids.shape],
                "centroids_sha256": bytes_sha256(centroids),
                "document_addresses_sha256": route["document_addresses"]["sha256"],
            })
            baseline = parent_baseline(parent, config["id"], seed,
                                       contract["decision"]["primary_coverage_target"])
            for alpha in contract["prototype"]["cost_alphas"]:
                query_rows = []
                for local, position in enumerate(positions):
                    target = oracle[position]
                    gains = sequential.target_gains(target, addresses, discounts)
                    order, scores = ordered_addresses(occupied, similarities[local],
                                                      index["counts"], alpha)
                    positives = set(gains)
                    snapshots, _, selected = sequential.snapshots_from_order(
                        order, gains, index, data, position, target, discounts,
                        oracle_contract, int(occupied.size))
                    thresholds = sequential.threshold_rows(snapshots, oracle_contract)
                    query_rows.append({
                        "query_id": str(data["query_ids"][position]),
                        "target_address_count": len(positives),
                        "global_average_precision": average_precision(order, positives),
                        "hard_negative_pairwise_auc": hard_negative_auc(
                            occupied, scores, positives,
                            contract["diagnostic"]["hard_negative_pool"]),
                        "relevant_density_pairwise_accuracy":
                            relevant_density_pairwise_accuracy(
                                occupied, scores, gains, index["counts"]),
                        "global_budgets": global_budget_rows(
                            order, gains, contract["diagnostic"]["global_address_budgets"]),
                        "thresholds": thresholds,
                        "selected_address_sha256": scale.sequence_sha256(selected),
                    })
                rows.append(summarize_queries(query_rows, alpha, seed, contract, baseline))
            del addresses, index, occupied, centroids, similarities
            gc.collect()
        datasets.append({
            "id": config["id"],
            "document_count": len(data["document_ids"]),
            "query_count": len(positions),
            "configuration_query_ids_sha256": scale.hash_ids(numpy.asarray(ids, dtype=object)),
            "prototype_artifacts": prototypes,
            "rows": rows,
        })
        del data, oracle
        gc.collect()
    return datasets


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    primary = contract["decision"]["primary_coverage_target"]
    selected = []
    for dataset in datasets:
        for seed in contract["route"]["seeds"]:
            candidates = [row for row in dataset["rows"] if int(row["seed"]) == seed]
            summaries = []
            for row in candidates:
                coverage = next(value for value in row["coverage"]
                                if value["coverage_target"] == primary)
                summaries.append({
                    "dataset": dataset["id"],
                    "seed": seed,
                    "cost_alpha": row["cost_alpha"],
                    "reach_rate": coverage["reach_rate"],
                    "candidate_fraction": coverage["censored_candidate_fraction"]["mean"],
                    "global_average_precision": row["classification"][
                        "global_average_precision"],
                    "occupied_logit_candidate_fraction": row["occupied_logit_baseline"][
                        "censored_candidate_fraction"],
                })
            eligible = [row for row in summaries
                        if row["reach_rate"] >= contract["decision"]["minimum_reach_rate"]]
            pool = eligible if eligible else summaries
            selected.append(min(pool, key=lambda row: (
                -row["reach_rate"], row["candidate_fraction"],
                -row["global_average_precision"], row["cost_alpha"])))
    de_1m = [row for row in selected if row["dataset"] == "de-1m"]
    useful = all(row["reach_rate"] >= contract["decision"]["minimum_reach_rate"]
                 and row["candidate_fraction"] <= contract["decision"][
                     "single_centroid_useful_maximum_candidate_fraction"]
                 for row in de_1m)
    return {
        "configuration_selected": selected,
        "single_centroid_useful": useful,
        "multi_prototype_followup_licensed": contract["decision"][
            "multi_prototype_followup_predeclared"],
        "internal_evaluation_opened": False,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    parent, materialization, split = validate_activation(contract, args)
    datasets = evaluate(contract, parent, materialization, split, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_address_centroid_learnability_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {"numpy_version": numpy.__version__},
        "matrix": planner.plan(contract),
        "datasets": datasets,
        "decision": decision(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    documents = numpy.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
                              dtype=numpy.float32)
    addresses = numpy.asarray([1, 1, 3], dtype=numpy.uint32)
    counts = numpy.bincount(addresses, minlength=4).astype(numpy.int64)
    occupied, centroids = build_centroids(documents, addresses, counts, 2)
    require(occupied.tolist() == [1, 3]
            and numpy.allclose(centroids[0], [2.0 ** -0.5, 2.0 ** -0.5])
            and numpy.allclose(centroids[1], [2.0 ** -0.5, 2.0 ** -0.5]),
            "address-centroid construction self-test differs")
    order, scores = ordered_addresses(
        occupied, numpy.asarray([0.5, 0.9], dtype=numpy.float32), counts, 0.0)
    require(order.tolist() == [3, 1] and average_precision(order, {1}) == 0.5
            and hard_negative_auc(occupied, scores, {3}, 1) == 1.0,
            "address-centroid ranking self-test differs")
    planner.load_contract(THIS / "neuroute-address-centroid-learnability.example.json")
    print("NeuRoute address-centroid learnability runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-centroid-learnability.example.json")
    parser.add_argument("--sequential-result", type=Path)
    parser.add_argument("--sequential-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all address-centroid learnability paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-address-centroid-learnability: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
