#!/usr/bin/env python3
"""Measure deterministic nested multi-prototype routing for occupied addresses."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterator

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


planner = load("neuroute_address_multi_prototype_planner",
               "plan-neuroute-address-multi-prototype.py")
centroid = load("neuroute_address_multi_prototype_parent",
                "run-neuroute-address-centroid-learnability.py")
sequential = centroid.sequential
scale = centroid.scale
task = centroid.task


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
    digest = hashlib.sha256()
    view = memoryview(numpy.ascontiguousarray(value)).cast("B")
    for start in range(0, len(view), 8 * 1024 * 1024):
        digest.update(view[start:start + 8 * 1024 * 1024])
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-address-multi-prototype.py",
        "run-neuroute-address-multi-prototype.py",
        "run-neuroute-address-centroid-learnability.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
        "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-task-aware-probe-scheduler.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "centroid_result_sha256": sha256(args.centroid_result),
        "centroid_evidence_sha256": sha256(args.centroid_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root /
                                                 "manifest.json"),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == contract["activation"],
            "multi-prototype activation bytes differ")
    parent = json.loads(args.centroid_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.centroid_evidence.read_text(encoding="utf-8"))
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_address_centroid_learnability_result"
            and parent.get("decision", {}).get("multi_prototype_followup_licensed") is True
            and parent.get("decision", {}).get("production_selection_licensed") is False,
            "multi-prototype centroid parent differs")
    require(evidence.get("family") == "neuroute_address_centroid_learnability_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["centroid_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "multi-prototype parent evidence differs")
    split = split_result["split"]
    configuration = split["configuration_selection_query_ids"]
    internal = split["internal_evaluation_query_ids"]
    require(len(configuration) == contract["partitions"]["configuration"]["queries"]
            and len(internal) == contract["partitions"]["internal_evaluation"]["queries"]
            and set(configuration).isdisjoint(internal),
            "multi-prototype query partitions differ")
    return evidence, materialization, split


def normalized_rows(values: numpy.ndarray) -> numpy.ndarray:
    result = numpy.asarray(values, dtype=numpy.float32).copy()
    norms = numpy.linalg.norm(result, axis=1).astype(numpy.float32)
    nonzero = norms > 0.0
    result[nonzero] /= norms[nonzero, None]
    return result


def build_nested_prototypes(documents: numpy.ndarray, addresses: numpy.ndarray,
                            index: dict[str, Any], maximum: int,
                            batch_size: int = 32768) -> tuple[
                                numpy.ndarray, numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    counts = index["counts"]
    occupied, means = centroid.build_centroids(documents, addresses, counts, batch_size)
    occupied_counts = counts[occupied].astype(numpy.int64)
    effective = numpy.minimum(occupied_counts, maximum).astype(numpy.uint8)
    prototypes = numpy.zeros((maximum, occupied.size, documents.shape[1]),
                             dtype=numpy.float32)
    prototypes[0] = means
    members = numpy.full((maximum - 1, occupied.size), -1, dtype=numpy.int32)
    address_to_row = numpy.full(counts.size, -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(occupied.size, dtype=numpy.int32)
    best_similarity = numpy.full(len(addresses), -numpy.inf, dtype=numpy.float32)
    selected = numpy.zeros(len(addresses), dtype=numpy.bool_)
    sorted_positions = numpy.asarray(index["order"], dtype=numpy.int32)
    starts = index["offsets"][occupied].astype(numpy.int64)
    repeated_counts = occupied_counts.astype(numpy.int64)
    sentinel = numpy.int32(len(addresses))

    for slot in range(1, maximum):
        for start in range(0, len(addresses), batch_size):
            stop = min(len(addresses), start + batch_size)
            rows = address_to_row[numpy.asarray(addresses[start:stop], dtype=numpy.uint32)]
            vectors = numpy.asarray(documents[start:stop], dtype=numpy.float32)
            reference = prototypes[slot - 1, rows]
            similarities = numpy.sum(vectors * reference, axis=1,
                                     dtype=numpy.float32)
            best_similarity[start:stop] = numpy.maximum(
                best_similarity[start:stop], similarities)
        best_similarity[selected] = numpy.inf
        sorted_similarity = best_similarity[sorted_positions]
        group_minimum = numpy.minimum.reduceat(sorted_similarity, starts)
        is_minimum = sorted_similarity == numpy.repeat(group_minimum, repeated_counts)
        candidates = numpy.where(is_minimum, sorted_positions, sentinel)
        chosen = numpy.minimum.reduceat(candidates, starts).astype(numpy.int32)
        active = effective > slot
        chosen_active = chosen[active]
        require(numpy.all(chosen_active < len(addresses)),
                "multi-prototype member selection failed")
        members[slot - 1, active] = chosen_active
        prototypes[slot, active] = normalized_rows(documents[chosen_active])
        selected[chosen_active] = True
    return occupied, prototypes, effective, members


def score_prefixes(queries: numpy.ndarray, prototypes: numpy.ndarray,
                   effective: numpy.ndarray, counts: list[int]) -> Iterator[
                       tuple[int, numpy.ndarray, int]]:
    requested = set(counts)
    scores = numpy.full((len(queries), effective.size), -numpy.inf,
                        dtype=numpy.float32)
    dot_products = 0
    for slot in range(prototypes.shape[0]):
        active = effective > slot
        similarities = numpy.asarray(queries @ prototypes[slot, active].T,
                                     dtype=numpy.float32)
        scores[:, active] = numpy.maximum(scores[:, active], similarities)
        dot_products += len(queries) * int(numpy.count_nonzero(active))
        prefix = slot + 1
        if prefix in requested:
            yield prefix, scores.copy(), dot_products


def summarize_queries(queries: list[dict[str, Any]], seed: int, partition: str,
                      prototype_count: int, effective_count: int,
                      dot_products: int, contract: dict[str, Any]) -> dict[str, Any]:
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
    dimensions = 384
    return {
        "seed": seed,
        "partition": partition,
        "prototype_count": prototype_count,
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
        "storage_and_work": {
            "effective_prototype_count": effective_count,
            "prototype_bytes": effective_count * dimensions * 4,
            "prototype_dot_products_total": dot_products,
            "prototype_dot_products_per_query": dot_products // max(len(queries), 1),
            "scalar_multiply_accumulates_per_query": (
                dot_products // max(len(queries), 1)) * dimensions,
        },
        "queries": queries,
    }


def evaluate_scores(scores: numpy.ndarray, occupied: numpy.ndarray,
                    addresses: numpy.ndarray, index: dict[str, Any],
                    data: dict[str, Any], positions: list[int],
                    oracle: dict[int, numpy.ndarray], discounts: numpy.ndarray,
                    contract: dict[str, Any]) -> list[dict[str, Any]]:
    oracle_contract = {
        "evaluation": {
            "candidate_mass_target": contract["diagnostic"]["candidate_mass_target"],
            "coverage_targets": contract["diagnostic"]["coverage_targets"],
        },
        "cascade": contract["cascade"],
    }
    rows = []
    for local, position in enumerate(positions):
        target = oracle[position]
        gains = sequential.target_gains(target, addresses, discounts)
        order = numpy.lexsort((occupied, -scores[local])).astype(numpy.int64)
        ordered = occupied[order].astype(numpy.uint32)
        positives = set(gains)
        snapshots, _, selected = sequential.snapshots_from_order(
            ordered, gains, index, data, position, target, discounts,
            oracle_contract, int(occupied.size))
        rows.append({
            "query_id": str(data["query_ids"][position]),
            "target_address_count": len(positives),
            "global_average_precision": centroid.average_precision(ordered, positives),
            "hard_negative_pairwise_auc": centroid.hard_negative_auc(
                occupied, scores[local], positives,
                contract["diagnostic"]["hard_negative_pool"]),
            "relevant_density_pairwise_accuracy":
                centroid.relevant_density_pairwise_accuracy(
                    occupied, scores[local], gains, index["counts"]),
            "global_budgets": centroid.global_budget_rows(
                ordered, gains, contract["diagnostic"]["global_address_budgets"]),
            "thresholds": sequential.threshold_rows(snapshots, oracle_contract),
            "selected_address_sha256": scale.sequence_sha256(selected),
        })
    return rows


def coverage_row(row: dict[str, Any], target: float) -> dict[str, Any]:
    return next(value for value in row["coverage"] if value["coverage_target"] == target)


def budget_row(row: dict[str, Any], budget: int) -> dict[str, Any]:
    return next(value for value in row["global_budgets"]
                if value["address_budget"] == budget)


def select_configuration(rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    target = contract["selection"]["primary_coverage_target"]
    summaries = []
    for row in rows:
        coverage = coverage_row(row, target)
        summaries.append({
            "prototype_count": row["prototype_count"],
            "reach_rate": coverage["reach_rate"],
            "candidate_fraction": coverage["censored_candidate_fraction"]["mean"],
            "gain_at_256": budget_row(row, 256)["discounted_target_gain_coverage"],
            "gain_at_1024": budget_row(row, 1024)["discounted_target_gain_coverage"],
        })
    eligible = [row for row in summaries
                if row["reach_rate"] >= contract["selection"]["minimum_reach_rate"]]
    if eligible:
        return min(eligible, key=lambda row: (
            row["candidate_fraction"], row["prototype_count"]))
    return min(summaries, key=lambda row: (
        -row["reach_rate"], row["candidate_fraction"], row["prototype_count"]))


def evaluate(contract: dict[str, Any], materialization: dict[str, Any],
             split: dict[str, Any], args: argparse.Namespace) -> tuple[
                 list[dict[str, Any]], list[dict[str, Any]]]:
    discounts = 1.0 / numpy.log2(numpy.arange(contract["cascade"]["oracle_k"],
                                               dtype=numpy.float64) + 2.0)
    datasets = []
    selections = []
    for config in contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        partition_ids = {
            "configuration": split["configuration_selection_query_ids"],
            "internal_evaluation": split["internal_evaluation_query_ids"],
        }
        positions = {name: [by_id[value] for value in values]
                     for name, values in partition_ids.items()}
        all_positions = positions["configuration"] + positions["internal_evaluation"]
        oracle, _ = scale.exact_oracle(data, all_positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in materialization["datasets"]
                                if row["id"] == config["id"])
        dataset_rows = []
        artifacts = []
        for seed in contract["route"]["seeds"]:
            route = task.route_entry(manifest_dataset, 16, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(
                route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, 16)
            occupied, prototypes, effective, members = build_nested_prototypes(
                data["documents"], addresses, index,
                max(contract["prototypes"]["counts"]))
            effective_by_count = {
                count: int(numpy.minimum(effective, count).sum(dtype=numpy.int64))
                for count in contract["prototypes"]["counts"]
            }
            artifacts.append({
                "seed": seed,
                "occupied_address_count": int(occupied.size),
                "maximum_prototype_shape": [int(value) for value in prototypes.shape],
                "effective_prototype_count_by_requested_count": {
                    str(key): value for key, value in effective_by_count.items()},
                "prototypes_sha256": bytes_sha256(prototypes),
                "member_document_positions_sha256": bytes_sha256(members),
                "document_addresses_sha256": route["document_addresses"]["sha256"],
            })

            configuration_rows = []
            config_queries = numpy.asarray(data["queries"][positions["configuration"]],
                                           dtype=numpy.float32)
            for count, scores, work in score_prefixes(
                    config_queries, prototypes, effective,
                    contract["prototypes"]["counts"]):
                query_rows = evaluate_scores(
                    scores, occupied, addresses, index, data,
                    positions["configuration"], oracle, discounts, contract)
                row = summarize_queries(
                    query_rows, seed, "configuration", count,
                    effective_by_count[count], work, contract)
                configuration_rows.append(row)
                dataset_rows.append(row)
                del scores, query_rows
            chosen = select_configuration(configuration_rows, contract)
            selections.append({"dataset": config["id"], "seed": seed, **chosen})

            internal_queries = numpy.asarray(
                data["queries"][positions["internal_evaluation"]], dtype=numpy.float32)
            for count, scores, work in score_prefixes(
                    internal_queries, prototypes, effective,
                    contract["prototypes"]["counts"]):
                query_rows = evaluate_scores(
                    scores, occupied, addresses, index, data,
                    positions["internal_evaluation"], oracle, discounts, contract)
                dataset_rows.append(summarize_queries(
                    query_rows, seed, "internal_evaluation", count,
                    effective_by_count[count], work, contract))
                del scores, query_rows
            del addresses, index, occupied, prototypes, effective, members
            gc.collect()
        datasets.append({
            "id": config["id"],
            "document_count": len(data["document_ids"]),
            "query_count_by_partition": {
                name: len(values) for name, values in positions.items()},
            "query_ids_sha256_by_partition": {
                name: scale.hash_ids(numpy.asarray(values, dtype=object))
                for name, values in partition_ids.items()},
            "prototype_artifacts": artifacts,
            "rows": dataset_rows,
        })
        del data, oracle
        gc.collect()
    return datasets, selections


def decision(datasets: list[dict[str, Any]], selections: list[dict[str, Any]],
             contract: dict[str, Any]) -> dict[str, Any]:
    internal_selected = []
    for selection in selections:
        dataset = next(value for value in datasets if value["id"] == selection["dataset"])
        row = next(value for value in dataset["rows"]
                   if value["partition"] == "internal_evaluation"
                   and int(value["seed"]) == int(selection["seed"])
                   and int(value["prototype_count"]) == int(selection["prototype_count"]))
        coverage = coverage_row(row, contract["selection"]["primary_coverage_target"])
        internal_selected.append({
            "dataset": selection["dataset"],
            "seed": selection["seed"],
            "prototype_count": selection["prototype_count"],
            "reach_rate": coverage["reach_rate"],
            "candidate_fraction": coverage["censored_candidate_fraction"]["mean"],
            "gain_at_256": budget_row(row, 256)["discounted_target_gain_coverage"],
            "gain_at_1024": budget_row(row, 1024)["discounted_target_gain_coverage"],
        })
    de_1m = [row for row in internal_selected if row["dataset"] == "de-1m"]
    require(len(de_1m) == len(contract["route"]["seeds"]),
            "multi-prototype DE-1M selection matrix differs")
    direct = all(
        row["reach_rate"] >= contract["decision"][
            "sufficient_final_minimum_internal_reach_rate"]
        and row["candidate_fraction"] <= contract["decision"][
            "sufficient_final_maximum_internal_candidate_fraction"]
        and row["gain_at_256"] >= contract["decision"][
            "sufficient_final_minimum_internal_gain_at_256"]
        for row in de_1m)
    coarse = all(
        row["gain_at_256"] >= contract["decision"][
            "coarse_shortlist_minimum_internal_gain_at_256"]
        and row["gain_at_1024"] >= contract["decision"][
            "coarse_shortlist_minimum_internal_gain_at_1024"]
        for row in de_1m)
    threshold = contract["decision"][
        "multimodality_minimum_internal_gain_at_256_improvement"]
    improvements = []
    for seed in contract["route"]["seeds"]:
        dataset = next(value for value in datasets if value["id"] == "de-1m")
        rows = [value for value in dataset["rows"]
                if value["partition"] == "internal_evaluation"
                and int(value["seed"]) == seed]
        baseline = budget_row(next(value for value in rows
                                   if value["prototype_count"] == 1), 256)[
                                       "discounted_target_gain_coverage"]
        best_row = max((value for value in rows if value["prototype_count"] > 1),
                       key=lambda value: (
                           budget_row(value, 256)["discounted_target_gain_coverage"],
                           -value["prototype_count"]))
        best = (budget_row(best_row, 256)["discounted_target_gain_coverage"],
                best_row["prototype_count"])
        improvements.append({
            "seed": seed,
            "single_prototype_gain_at_256": baseline,
            "best_multi_prototype_gain_at_256": best[0],
            "best_multi_prototype_count": best[1],
            "absolute_improvement": best[0] - baseline,
            "material_improvement": best[0] - baseline >= threshold,
        })
    supporting = sum(row["material_improvement"] for row in improvements)
    multimodality = supporting >= contract["decision"][
        "multimodality_minimum_supporting_de_1m_seeds"]
    return {
        "configuration_selected": selections,
        "internal_selected": internal_selected,
        "de_1m_multimodality_diagnostic": improvements,
        "multimodality_supported": multimodality,
        "direct_router_sufficient": direct,
        "coarse_shortlist_sufficient": coarse,
        "learned_gain_density_reranker_followup_licensed": coarse and not direct,
        "internal_evaluation_opened_after_configuration_selection": True,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    _, materialization, split = validate_activation(contract, args)
    datasets, selections = evaluate(contract, materialization, split, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_address_multi_prototype_frontier_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {"numpy_version": numpy.__version__},
        "matrix": planner.plan(contract),
        "datasets": datasets,
        "decision": decision(datasets, selections, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    documents = normalized_rows(numpy.asarray([
        [1.0, 0.0], [0.9, 0.1], [-1.0, 0.0], [0.0, 1.0],
    ], dtype=numpy.float32))
    addresses = numpy.asarray([1, 1, 1, 3], dtype=numpy.uint32)
    index = scale.build_index(addresses, 2)
    occupied, prototypes, effective, members = build_nested_prototypes(
        documents, addresses, index, 4, 2)
    require(occupied.tolist() == [1, 3]
            and effective.tolist() == [3, 1]
            and members[:, 1].tolist() == [-1, -1, -1]
            and members[0, 0] == 2
            and len(set(members[:2, 0].tolist())) == 2,
            "multi-prototype nested construction self-test differs")
    prefixes = list(score_prefixes(
        numpy.asarray([[1.0, 0.0]], dtype=numpy.float32),
        prototypes, effective, [1, 2, 4]))
    require([row[0] for row in prefixes] == [1, 2, 4]
            and prefixes[0][2] == 2
            and prefixes[1][2] == 3
            and prefixes[2][2] == 4,
            "multi-prototype score accounting self-test differs")
    planner.load_contract(THIS / "neuroute-address-multi-prototype.example.json")
    print("NeuRoute address multi-prototype runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-multi-prototype.example.json")
    parser.add_argument("--centroid-result", type=Path)
    parser.add_argument("--centroid-evidence", type=Path)
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
            parser.error("all multi-prototype frontier paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-address-multi-prototype: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
