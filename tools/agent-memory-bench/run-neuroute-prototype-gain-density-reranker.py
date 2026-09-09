#!/usr/bin/env python3
"""Train and evaluate a gain-density reranker inside frozen prototype shortlists."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
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


planner = load("neuroute_prototype_gain_density_planner",
               "plan-neuroute-prototype-gain-density-reranker.py")
multi = load("neuroute_prototype_gain_density_parent",
             "run-neuroute-address-multi-prototype.py")
sequential = multi.sequential
scale = multi.scale
task = multi.task


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
        "plan-neuroute-prototype-gain-density-reranker.py",
        "run-neuroute-prototype-gain-density-reranker.py",
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
        "multi_prototype_result_sha256": sha256(args.multi_prototype_result),
        "multi_prototype_evidence_sha256": sha256(args.multi_prototype_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root /
                                                 "manifest.json"),
        "german_split_result_sha256": sha256(args.german_split_result),
    }
    require(actual == contract["activation"],
            "prototype gain-density activation bytes differ")
    parent = json.loads(args.multi_prototype_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.multi_prototype_evidence.read_text(encoding="utf-8"))
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_address_multi_prototype_frontier_result"
            and parent.get("decision", {}).get("coarse_shortlist_sufficient") is True
            and parent.get("decision", {}).get(
                "learned_gain_density_reranker_followup_licensed") is True
            and parent.get("decision", {}).get("production_selection_licensed") is False,
            "prototype gain-density multi-prototype parent differs")
    require(evidence.get("family") == "neuroute_address_multi_prototype_frontier_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["multi_prototype_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "prototype gain-density parent evidence differs")
    split = split_result["split"]
    names = ("training_query_ids", "configuration_selection_query_ids",
             "internal_evaluation_query_ids")
    expected = (contract["partitions"]["training"]["queries"],
                contract["partitions"]["configuration"]["queries"],
                contract["partitions"]["internal_evaluation"]["queries"])
    require(all(len(split[name]) == count for name, count in zip(names, expected))
            and all(set(split[left]).isdisjoint(split[right])
                    for index, left in enumerate(names)
                    for right in names[index + 1:]),
            "prototype gain-density query partitions differ")
    return evidence, materialization, split


def maximum_scores(queries: numpy.ndarray, prototypes: numpy.ndarray,
                   effective: numpy.ndarray) -> numpy.ndarray:
    scores = numpy.full((len(queries), effective.size), -numpy.inf,
                        dtype=numpy.float32)
    for slot in range(prototypes.shape[0]):
        active = effective > slot
        similarities = numpy.asarray(queries @ prototypes[slot, active].T,
                                     dtype=numpy.float32)
        scores[:, active] = numpy.maximum(scores[:, active], similarities)
    return scores


def query_shortlists(queries: numpy.ndarray, maximum: numpy.ndarray,
                     occupied: numpy.ndarray, prototypes: numpy.ndarray,
                     effective: numpy.ndarray, counts: numpy.ndarray,
                     shortlist: int, document_count: int) -> tuple[
                         numpy.ndarray, numpy.ndarray]:
    addresses = numpy.empty((len(queries), shortlist), dtype=numpy.uint32)
    feature_rows = []
    slots = prototypes.shape[0]
    log_denominator = numpy.log1p(float(document_count))
    for query_index, query in enumerate(queries):
        order = numpy.lexsort((occupied, -maximum[query_index]))[:shortlist]
        selected = occupied[order].astype(numpy.uint32)
        addresses[query_index] = selected
        local = prototypes[:, order]
        similarities = numpy.einsum("ksd,d->ks", local, query,
                                    dtype=numpy.float32, optimize=True)
        valid = numpy.arange(slots)[:, None] < effective[order][None, :]
        masked = numpy.where(valid, similarities, -1.0).astype(numpy.float32)
        sorted_cosines = numpy.sort(masked, axis=0)[::-1].T
        valid_float = valid.astype(numpy.float32)
        valid_count = effective[order].astype(numpy.float32)
        means = (numpy.where(valid, similarities, 0.0).sum(axis=0)
                 / valid_count)
        centered = numpy.where(valid, similarities - means[None, :], 0.0)
        standard_deviation = numpy.sqrt(
            (centered * centered).sum(axis=0) / valid_count)
        maximum_value = sorted_cosines[:, 0]
        second = sorted_cosines[:, 1]
        margin = maximum_value - second
        log_cost = (numpy.log1p(counts[selected].astype(numpy.float32))
                    / log_denominator)
        rank = (numpy.arange(shortlist, dtype=numpy.float32)
                / max(shortlist - 1, 1))
        capacity = valid_count / float(slots)
        base = numpy.column_stack((
            sorted_cosines, maximum_value, second, means,
            standard_deviation, margin, log_cost, rank, capacity,
            maximum_value * log_cost, second * log_cost,
            margin * log_cost, means * log_cost,
            maximum_value * maximum_value,
            standard_deviation * standard_deviation,
        )).astype(numpy.float32)
        feature_rows.append(base)
    return addresses, numpy.stack(feature_rows).astype(numpy.float32)


def density_targets(shortlists: numpy.ndarray, oracle: dict[int, numpy.ndarray],
                    positions: list[int], document_addresses: numpy.ndarray,
                    counts: numpy.ndarray, discounts: numpy.ndarray) -> numpy.ndarray:
    targets = numpy.zeros(shortlists.shape, dtype=numpy.float64)
    for local, position in enumerate(positions):
        gains = sequential.target_gains(oracle[position], document_addresses, discounts)
        for shortlist_position, address in enumerate(shortlists[local].tolist()):
            targets[local, shortlist_position] = (
                gains.get(int(address), 0.0) / max(int(counts[address]), 1))
    return targets


def global_density_totals(oracle: dict[int, numpy.ndarray], positions: list[int],
                          document_addresses: numpy.ndarray,
                          counts: numpy.ndarray,
                          discounts: numpy.ndarray) -> numpy.ndarray:
    totals = numpy.empty(len(positions), dtype=numpy.float64)
    for local, position in enumerate(positions):
        gains = sequential.target_gains(oracle[position], document_addresses, discounts)
        totals[local] = sum(gain / max(int(counts[address]), 1)
                            for address, gain in gains.items())
    return totals


def fit_pairwise_models(features: numpy.ndarray, targets: numpy.ndarray,
                        alphas: list[float], negatives_per_positive: int) -> tuple[
                            dict[float, numpy.ndarray], numpy.ndarray, numpy.ndarray,
                            dict[str, Any]]:
    flattened = features.reshape(-1, features.shape[-1]).astype(numpy.float64)
    mean = flattened.mean(axis=0, dtype=numpy.float64)
    deviation = flattened.std(axis=0, dtype=numpy.float64)
    deviation[deviation < 1.0e-8] = 1.0
    normalized = ((features.astype(numpy.float64) - mean) / deviation)
    dimension = features.shape[-1]
    gram = numpy.zeros((dimension, dimension), dtype=numpy.float64)
    vector = numpy.zeros(dimension, dtype=numpy.float64)
    pair_count = 0
    positive_count = 0
    for query_index in range(len(features)):
        positives = numpy.flatnonzero(targets[query_index] > 0.0)
        negatives = numpy.flatnonzero(targets[query_index] == 0.0)[:negatives_per_positive]
        if not positives.size or not negatives.size:
            continue
        total_density = float(targets[query_index, positives].sum(dtype=numpy.float64))
        for positive in positives.tolist():
            differences = (normalized[query_index, positive]
                           - normalized[query_index, negatives])
            weight = targets[query_index, positive] / max(total_density, 1.0e-30)
            gram += weight * (differences.T @ differences)
            vector += weight * differences.sum(axis=0)
            pair_count += len(negatives)
            positive_count += 1
    require(pair_count > 0, "prototype gain-density training pairs are empty")
    models = {}
    identity = numpy.eye(dimension, dtype=numpy.float64)
    for alpha in alphas:
        models[alpha] = numpy.linalg.solve(gram + alpha * identity, vector)
    metadata = {
        "feature_count": dimension,
        "positive_address_instances": positive_count,
        "pair_count": pair_count,
        "mean_sha256": bytes_sha256(mean),
        "deviation_sha256": bytes_sha256(deviation),
        "gram_sha256": bytes_sha256(gram),
        "target_vector_sha256": bytes_sha256(vector),
    }
    return models, mean, deviation, metadata


def learned_scores(features: numpy.ndarray, weights: numpy.ndarray,
                   mean: numpy.ndarray, deviation: numpy.ndarray) -> numpy.ndarray:
    normalized = ((features.astype(numpy.float64) - mean) / deviation)
    return numpy.asarray(normalized @ weights, dtype=numpy.float64)


def ordered(values: numpy.ndarray, addresses: numpy.ndarray,
            posting_counts: numpy.ndarray | None = None) -> numpy.ndarray:
    if posting_counts is None:
        order = numpy.lexsort((addresses, -values))
    else:
        order = numpy.lexsort((addresses, posting_counts[addresses], -values))
    return addresses[order].astype(numpy.uint32)


def static_calibration(shortlists: numpy.ndarray, features: numpy.ndarray,
                       targets: numpy.ndarray, weights: numpy.ndarray,
                       mean: numpy.ndarray, deviation: numpy.ndarray,
                       counts: numpy.ndarray, document_count: int,
                       budget: int, target_totals: numpy.ndarray) -> dict[str, float]:
    scores = learned_scores(features, weights, mean, deviation)
    gain = []
    candidate = []
    for query_index in range(len(shortlists)):
        order = ordered(scores[query_index], shortlists[query_index])
        selected = order[:budget]
        positions = {int(value): index
                     for index, value in enumerate(shortlists[query_index].tolist())}
        total = float(target_totals[query_index])
        gain.append(sum(targets[query_index, positions[int(value)]]
                        for value in selected.tolist()) / max(total, 1.0e-30))
        candidate.append(int(counts[selected].sum(dtype=numpy.int64)) / document_count)
    return {
        "static_gain_density_coverage": float(numpy.mean(gain, dtype=numpy.float64)),
        "candidate_fraction": float(numpy.mean(candidate, dtype=numpy.float64)),
    }


def select_configuration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return min(rows, key=lambda row: (
        -row["static_gain_density_coverage"], row["candidate_fraction"],
        row["shortlist_size"], row["ridge_alpha"]))


def fixed_budget_rows(order: numpy.ndarray, gains: dict[int, float],
                      index: dict[str, Any], data: dict[str, Any], position: int,
                      target: numpy.ndarray, discounts: numpy.ndarray,
                      contract: dict[str, Any]) -> list[dict[str, Any]]:
    maximum_mass = int(len(data["document_ids"])
                       * contract["diagnostic"]["candidate_mass_target"])
    total_gain = sum(gains.values())
    rows = []
    for budget in contract["diagnostic"]["address_budgets"]:
        requested = order[:min(budget, len(order))].tolist()
        candidates, accepted, _ = scale.candidate_union(requested, index, maximum_mass)
        state = sequential.cascade_state(data, position, candidates, target,
                                         discounts, contract["cascade"])
        rows.append({
            "address_budget": budget,
            "accepted_address_count": len(accepted),
            "candidate_count": int(candidates.size),
            "candidate_fraction": candidates.size / len(data["document_ids"]),
            "static_gain_coverage": sum(gains.get(value, 0.0) for value in accepted)
                                    / max(total_gain, 1.0e-30),
            "actionable_gain_coverage": state["coverage"],
            "exact_ndcg_at_10": state["ndcg_at_10"],
            "selected_address_sha256": scale.sequence_sha256(
                numpy.asarray(accepted, dtype=numpy.uint32)),
        })
    return rows


def evaluate_treatment(treatment: str, shortlists: numpy.ndarray,
                       features: numpy.ndarray, targets: numpy.ndarray,
                       weights: numpy.ndarray, mean: numpy.ndarray,
                       deviation: numpy.ndarray, addresses: numpy.ndarray,
                       index: dict[str, Any], data: dict[str, Any],
                       positions: list[int], oracle: dict[int, numpy.ndarray],
                       discounts: numpy.ndarray, contract: dict[str, Any]) -> dict[str, Any]:
    queries = []
    learned = learned_scores(features, weights, mean, deviation)
    heuristic_alpha = contract["diagnostic"]["posting_cost_heuristic_alpha"]
    for local, position in enumerate(positions):
        shortlist = shortlists[local]
        gains = sequential.target_gains(oracle[position], addresses, discounts)
        if treatment == "prototype_score":
            order = shortlist.copy()
        elif treatment == "posting_cost_heuristic":
            values = (features[local, :, 8].astype(numpy.float64)
                      / numpy.power(index["counts"][shortlist].astype(numpy.float64),
                                    heuristic_alpha))
            order = ordered(values, shortlist)
        elif treatment == "learned_pairwise_gain_density":
            order = ordered(learned[local], shortlist)
        elif treatment in ("privileged_gain_density_teacher",
                           "privileged_gain_density_teacher_maximum_shortlist"):
            order = ordered(targets[local], shortlist, index["counts"])
        else:
            raise ValueError(f"unknown prototype gain-density treatment: {treatment}")
        positives = set(gains)
        queries.append({
            "query_id": str(data["query_ids"][position]),
            "shortlist_target_address_recall": len(set(shortlist.tolist()) & positives)
                                               / max(len(positives), 1),
            "shortlist_average_precision": multi.centroid.average_precision(
                order, positives),
            "budgets": fixed_budget_rows(order, gains, index, data, position,
                                          oracle[position], discounts, contract),
        })
    budgets = []
    for budget in contract["diagnostic"]["address_budgets"]:
        rows = [next(value for value in query["budgets"]
                     if value["address_budget"] == budget) for query in queries]
        budgets.append({
            "address_budget": budget,
            "candidate_fraction": float(numpy.mean([
                value["candidate_fraction"] for value in rows], dtype=numpy.float64)),
            "static_gain_coverage": float(numpy.mean([
                value["static_gain_coverage"] for value in rows], dtype=numpy.float64)),
            "actionable_gain_coverage": float(numpy.mean([
                value["actionable_gain_coverage"] for value in rows],
                dtype=numpy.float64)),
            "exact_ndcg_at_10": float(numpy.mean([
                value["exact_ndcg_at_10"] for value in rows], dtype=numpy.float64)),
        })
    return {
        "treatment": treatment,
        "query_count": len(queries),
        "shortlist_target_address_recall": float(numpy.mean([
            query["shortlist_target_address_recall"] for query in queries],
            dtype=numpy.float64)),
        "shortlist_average_precision": float(numpy.mean([
            query["shortlist_average_precision"] for query in queries],
            dtype=numpy.float64)),
        "budgets": budgets,
        "queries": queries,
    }


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
            "training": split["training_query_ids"],
            "configuration": split["configuration_selection_query_ids"],
            "internal_evaluation": split["internal_evaluation_query_ids"],
        }
        positions = {name: [by_id[value] for value in values]
                     for name, values in partition_ids.items()}
        all_positions = (positions["training"] + positions["configuration"]
                         + positions["internal_evaluation"])
        oracle, _ = scale.exact_oracle(data, all_positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in materialization["datasets"]
                                if row["id"] == config["id"])
        calibration_rows = []
        internal_rows = []
        artifacts = []
        for seed in contract["route"]["seeds"]:
            route = task.route_entry(manifest_dataset, 16, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(
                route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, 16)
            occupied, prototypes, effective, members = multi.build_nested_prototypes(
                data["documents"], addresses, index,
                contract["prototype_shortlist"]["requested_prototypes_per_address"])
            maximum_shortlist = contract["prototype_shortlist"]["training_shortlist"]

            training_queries = numpy.asarray(data["queries"][positions["training"]],
                                             dtype=numpy.float32)
            training_maximum = maximum_scores(training_queries, prototypes, effective)
            training_shortlists, training_features = query_shortlists(
                training_queries, training_maximum, occupied, prototypes, effective,
                index["counts"], maximum_shortlist, len(data["document_ids"]))
            training_targets = density_targets(
                training_shortlists, oracle, positions["training"], addresses,
                index["counts"], discounts)
            models, feature_mean, feature_deviation, training_metadata = (
                fit_pairwise_models(
                    training_features, training_targets,
                    contract["model"]["ridge_alphas"],
                    contract["model"]["hard_negatives_per_positive"]))
            del training_queries, training_maximum, training_shortlists
            del training_features, training_targets
            gc.collect()

            configuration_queries = numpy.asarray(
                data["queries"][positions["configuration"]], dtype=numpy.float32)
            configuration_maximum = maximum_scores(
                configuration_queries, prototypes, effective)
            configuration_shortlists, configuration_features = query_shortlists(
                configuration_queries, configuration_maximum, occupied, prototypes,
                effective, index["counts"], maximum_shortlist,
                len(data["document_ids"]))
            configuration_targets = density_targets(
                configuration_shortlists, oracle, positions["configuration"],
                addresses, index["counts"], discounts)
            configuration_target_totals = global_density_totals(
                oracle, positions["configuration"], addresses,
                index["counts"], discounts)
            local_calibration = []
            for alpha in contract["model"]["ridge_alphas"]:
                for shortlist_size in contract["prototype_shortlist"][
                        "configuration_shortlists"]:
                    summary = static_calibration(
                        configuration_shortlists[:, :shortlist_size],
                        configuration_features[:, :shortlist_size],
                        configuration_targets[:, :shortlist_size], models[alpha],
                        feature_mean, feature_deviation, index["counts"],
                        len(data["document_ids"]),
                        contract["diagnostic"]["selection_address_budget"],
                        configuration_target_totals)
                    row = {
                        "dataset": config["id"], "seed": seed,
                        "ridge_alpha": alpha, "shortlist_size": shortlist_size,
                        **summary,
                    }
                    local_calibration.append(row)
                    calibration_rows.append(row)
            selected = select_configuration(local_calibration)
            selections.append(selected)

            selected_alpha = float(selected["ridge_alpha"])
            selected_shortlist = int(selected["shortlist_size"])
            artifacts.append({
                "seed": seed,
                "selected_ridge_alpha": selected_alpha,
                "selected_shortlist_size": selected_shortlist,
                "selected_weights": models[selected_alpha].tolist(),
                "selected_weights_sha256": bytes_sha256(models[selected_alpha]),
                "feature_mean": feature_mean.tolist(),
                "feature_deviation": feature_deviation.tolist(),
                "training": training_metadata,
                "prototype_sha256": bytes_sha256(prototypes),
                "member_document_positions_sha256": bytes_sha256(members),
                "document_addresses_sha256": route["document_addresses"]["sha256"],
            })
            del configuration_queries, configuration_maximum
            del configuration_shortlists, configuration_features
            del configuration_targets, configuration_target_totals
            gc.collect()

            internal_queries = numpy.asarray(
                data["queries"][positions["internal_evaluation"]], dtype=numpy.float32)
            internal_maximum = maximum_scores(internal_queries, prototypes, effective)
            internal_shortlists, internal_features = query_shortlists(
                internal_queries, internal_maximum, occupied, prototypes, effective,
                index["counts"], maximum_shortlist, len(data["document_ids"]))
            full_internal_targets = density_targets(
                internal_shortlists, oracle, positions["internal_evaluation"],
                addresses, index["counts"], discounts)
            for treatment in contract["treatments"]:
                if treatment == "privileged_gain_density_teacher_maximum_shortlist":
                    treatment_shortlists = internal_shortlists
                    treatment_features = internal_features
                    treatment_targets = full_internal_targets
                else:
                    treatment_shortlists = internal_shortlists[:, :selected_shortlist]
                    treatment_features = internal_features[:, :selected_shortlist]
                    treatment_targets = full_internal_targets[:, :selected_shortlist]
                row = evaluate_treatment(
                    treatment, treatment_shortlists, treatment_features,
                    treatment_targets, models[selected_alpha], feature_mean,
                    feature_deviation, addresses, index, data,
                    positions["internal_evaluation"], oracle, discounts, contract)
                internal_rows.append({
                    "dataset": config["id"], "seed": seed,
                    "selected_ridge_alpha": selected_alpha,
                    "selected_shortlist_size": selected_shortlist,
                    "evaluated_shortlist_size": int(treatment_shortlists.shape[1]),
                    **row,
                })
            del internal_queries, internal_maximum, internal_shortlists
            del internal_features, full_internal_targets, models
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
            "model_artifacts": artifacts,
            "calibration_rows": calibration_rows,
            "internal_rows": internal_rows,
        })
        del data, oracle
        gc.collect()
    return datasets, selections


def budget(row: dict[str, Any], value: int) -> dict[str, Any]:
    return next(item for item in row["budgets"] if item["address_budget"] == value)


def decision(datasets: list[dict[str, Any]], selections: list[dict[str, Any]],
             contract: dict[str, Any]) -> dict[str, Any]:
    comparisons = []
    for seed in contract["route"]["seeds"]:
        dataset = next(value for value in datasets if value["id"] == "de-1m")
        prototype = next(row for row in dataset["internal_rows"]
                         if row["seed"] == seed
                         and row["treatment"] == "prototype_score")
        learned = next(row for row in dataset["internal_rows"]
                       if row["seed"] == seed
                       and row["treatment"] == "learned_pairwise_gain_density")
        teacher = next(row for row in dataset["internal_rows"]
                       if row["seed"] == seed
                       and row["treatment"]
                       == "privileged_gain_density_teacher_maximum_shortlist")
        prototype_budget = budget(prototype, 256)
        learned_budget = budget(learned, 256)
        teacher_budget = budget(teacher, 256)
        comparisons.append({
            "seed": seed,
            "selected_ridge_alpha": learned["selected_ridge_alpha"],
            "selected_shortlist_size": learned["selected_shortlist_size"],
            "prototype_actionable_gain_at_256": prototype_budget[
                "actionable_gain_coverage"],
            "learned_actionable_gain_at_256": learned_budget[
                "actionable_gain_coverage"],
            "learned_absolute_improvement": learned_budget[
                "actionable_gain_coverage"] - prototype_budget[
                    "actionable_gain_coverage"],
            "prototype_candidate_fraction_at_256": prototype_budget[
                "candidate_fraction"],
            "learned_candidate_fraction_at_256": learned_budget[
                "candidate_fraction"],
            "learned_candidate_fraction_ratio": learned_budget[
                "candidate_fraction"] / max(prototype_budget["candidate_fraction"],
                                             1.0e-30),
            "teacher_actionable_gain_at_256": teacher_budget[
                "actionable_gain_coverage"],
            "teacher_candidate_fraction_at_256": teacher_budget[
                "candidate_fraction"],
        })
    learned_better = all(
        row["learned_absolute_improvement"] >= contract["decision"][
            "minimum_all_seed_learned_actionable_gain_at_256_improvement"]
        and row["learned_candidate_fraction_ratio"] <= contract["decision"][
            "maximum_all_seed_learned_candidate_fraction_ratio"]
        for row in comparisons)
    learned_direct = all(
        row["learned_actionable_gain_at_256"] >= contract["decision"][
            "direct_minimum_actionable_gain_at_256"]
        and row["learned_candidate_fraction_at_256"] <= contract["decision"][
            "direct_maximum_candidate_fraction_at_256"]
        for row in comparisons)
    teacher_direct = all(
        row["teacher_actionable_gain_at_256"] >= contract["decision"][
            "direct_minimum_actionable_gain_at_256"]
        and row["teacher_candidate_fraction_at_256"] <= contract["decision"][
            "direct_maximum_candidate_fraction_at_256"]
        for row in comparisons)
    return {
        "configuration_selected": selections,
        "de_1m_internal_comparisons": comparisons,
        "learned_reranker_materially_better": learned_better,
        "learned_direct_router_sufficient": learned_direct,
        "privileged_teacher_direct_router_sufficient": teacher_direct,
        "richer_model_or_training_followup_licensed": teacher_direct and not learned_direct,
        "native_confirmation_licensed": learned_direct,
        "internal_evaluation_opened_after_configuration_selection": True,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    _, materialization, split = validate_activation(contract, args)
    datasets, selections = evaluate(contract, materialization, split, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_prototype_gain_density_reranker_result",
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
    features = numpy.asarray([
        [[2.0, 0.0], [1.0, 1.0], [0.0, 2.0]],
        [[1.5, 0.0], [0.5, 1.0], [0.0, 1.5]],
    ], dtype=numpy.float32)
    targets = numpy.asarray([[1.0, 0.0, 0.0], [0.5, 0.0, 0.0]],
                            dtype=numpy.float64)
    models, mean, deviation, metadata = fit_pairwise_models(
        features, targets, [0.1], 2)
    scores = learned_scores(features, models[0.1], mean, deviation)
    require(metadata["pair_count"] == 4
            and numpy.all(scores[:, 0] > scores[:, 1])
            and numpy.all(scores[:, 0] > scores[:, 2]),
            "prototype gain-density pairwise fit self-test differs")
    ordered_values = ordered(
        numpy.asarray([0.5, 0.5, 0.4]),
        numpy.asarray([3, 1, 2], dtype=numpy.uint32))
    require(ordered_values.tolist() == [1, 3, 2],
            "prototype gain-density ordering self-test differs")
    totals = global_density_totals(
        {0: numpy.asarray([0, 1], dtype=numpy.int32)}, [0],
        numpy.asarray([1, 2], dtype=numpy.uint32),
        numpy.asarray([0, 1, 2], dtype=numpy.int64),
        numpy.asarray([1.0, 0.5], dtype=numpy.float64))
    require(numpy.allclose(totals, [1.25]),
            "prototype gain-density global denominator self-test differs")
    planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")
    print("NeuRoute prototype gain-density runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prototype-gain-density-reranker.example.json")
    parser.add_argument("--multi-prototype-result", type=Path)
    parser.add_argument("--multi-prototype-evidence", type=Path)
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
            parser.error("all prototype gain-density reranker paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-prototype-gain-density-reranker: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
