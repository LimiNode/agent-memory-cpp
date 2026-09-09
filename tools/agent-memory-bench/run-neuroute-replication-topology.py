#!/usr/bin/env python3
"""Measure global secondary-address replication topologies on frozen DE-1M."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
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


planner = load("neuroute_replication_topology_planner",
               "plan-neuroute-replication-topology.py")
decoupled = load("neuroute_replication_decoupled_parent",
                 "run-neuroute-decoupled-relevance-cost.py")
matched = decoupled.matched
base = matched.base
prototype = matched.prototype
multi = matched.multi
scale = matched.scale
task = matched.task
sequential = base.sequential


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def array_sha256(value: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.ascontiguousarray(value).view(numpy.uint8)).hexdigest()


def source_hashes() -> dict[str, str]:
    names = [
        "plan-neuroute-replication-topology.py",
        "run-neuroute-replication-topology.py",
        "run-neuroute-decoupled-relevance-cost.py",
        "run-neuroute-feasible-candidate-frontier.py",
        "run-neuroute-r3-matched-ladder.py",
        "run-neuroute-address-multi-prototype.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
    ]
    return {name: sha256(THIS / name) for name in names}


def validate_parent(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], list[str],
        numpy.ndarray, dict[str, Any]]:
    actual = {
        "decoupled_relevance_result_sha256": sha256(args.decoupled_result),
        "decoupled_relevance_evidence_sha256": sha256(args.decoupled_evidence),
    }
    require(actual == contract["activation"],
            f"replication-topology activation bytes differ: {actual!r}")
    result = json.loads(args.decoupled_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.decoupled_evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_decoupled_relevance_cost_result"
            and evidence.get("family")
            == "neuroute_decoupled_relevance_cost_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_archive_sha_map_replay_passed") is True,
            "replication-topology decoupled parent differs")
    parent_contract = decoupled.planner.load_contract(
        THIS / "neuroute-decoupled-relevance-cost.example.json")
    frontier_result, materialization, split, external_ids, external_vectors, summary = (
        decoupled.validate_parent(parent_contract, args))
    require(frontier_result.get("family")
            == "neuroute_feasible_candidate_frontier_result",
            "replication-topology frontier chain differs")
    return result, materialization, split, external_ids, external_vectors, summary


def neighbor_mappings(documents: numpy.ndarray, addresses: numpy.ndarray,
                      occupied: numpy.ndarray, means: numpy.ndarray,
                      batch_size: int = 32768
                      ) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    lookup = numpy.full(65536, -1, dtype=numpy.int32)
    lookup[occupied] = numpy.arange(len(occupied), dtype=numpy.int32)
    primary_rows = lookup[addresses]
    fallback_by_address = numpy.roll(occupied, -1)
    nearest = fallback_by_address[primary_rows].copy()
    soar = nearest.copy()
    nearest_score = numpy.full(len(addresses), -numpy.inf, dtype=numpy.float32)
    soar_score = numpy.full(len(addresses), -numpy.inf, dtype=numpy.float32)
    for bit in range(16):
        candidate = numpy.bitwise_xor(addresses, numpy.uint32(1 << bit))
        candidate_rows = lookup[candidate]
        for start in range(0, len(addresses), batch_size):
            stop = min(len(addresses), start + batch_size)
            valid = candidate_rows[start:stop] >= 0
            if not numpy.any(valid):
                continue
            positions = numpy.flatnonzero(valid) + start
            vectors = numpy.asarray(documents[positions], dtype=numpy.float32)
            primary_means = means[primary_rows[positions]]
            candidate_means = means[candidate_rows[positions]]
            semantic = numpy.sum(vectors * candidate_means, axis=1,
                                 dtype=numpy.float32)
            current = nearest_score[positions]
            chosen = ((semantic > current)
                      | ((semantic == current)
                         & (candidate[positions] < nearest[positions])))
            selected = positions[chosen]
            nearest_score[selected] = semantic[chosen]
            nearest[selected] = candidate[selected]
            residual_primary = vectors - primary_means
            residual_secondary = vectors - candidate_means
            orthogonality_penalty = numpy.abs(numpy.sum(
                residual_primary * residual_secondary, axis=1,
                dtype=numpy.float32))
            complement = semantic - 0.25 * orthogonality_penalty
            current_soar = soar_score[positions]
            chosen_soar = ((complement > current_soar)
                           | ((complement == current_soar)
                              & (candidate[positions] < soar[positions])))
            selected_soar = positions[chosen_soar]
            soar_score[selected_soar] = complement[chosen_soar]
            soar[selected_soar] = candidate[selected_soar]
    require(numpy.all(nearest != addresses) and numpy.all(soar != addresses),
            "replication secondary mapping contains primary address")
    return nearest.astype(numpy.uint32), soar.astype(numpy.uint32), {
        "nearest_document_cosine": float(numpy.mean(nearest_score,
                                                     dtype=numpy.float64)),
        "soar_document_complement_score": float(numpy.mean(
            soar_score, dtype=numpy.float64)),
        "one_bit_neighbor_coverage": float(numpy.mean(
            numpy.isfinite(nearest_score), dtype=numpy.float64)),
    }


def training_mapping(documents: numpy.ndarray, addresses: numpy.ndarray,
                     occupied: numpy.ndarray, means: numpy.ndarray,
                     fallback: numpy.ndarray, top100: numpy.ndarray,
                     maximum_candidates: int = 4,
                     batch_size: int = 32768) -> tuple[numpy.ndarray, dict[str, Any]]:
    gains = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    scores: dict[int, dict[int, float]] = {}
    for row in numpy.asarray(top100[:, :10]).tolist():
        values = [int(addresses[int(document)]) for document in row]
        for left_index, left in enumerate(values):
            current = scores.setdefault(left, {})
            for right_index, right in enumerate(values):
                if right != left:
                    current[right] = current.get(right, 0.0) + float(gains[right_index])
    occupied_set = set(int(value) for value in occupied.tolist())
    candidates = numpy.full((65536, maximum_candidates), 65536, dtype=numpy.uint32)
    priors = numpy.zeros((65536, maximum_candidates), dtype=numpy.float32)
    fitted_addresses = 0
    for address, values in scores.items():
        ranked = [value for value in sorted(
            values, key=lambda item: (-values[item], item))
                  if value != address and value in occupied_set][:maximum_candidates]
        if not ranked:
            continue
        candidates[address, :len(ranked)] = ranked
        priors[address, :len(ranked)] = [values[value] for value in ranked]
        fitted_addresses += 1
    lookup = numpy.full(65536, -1, dtype=numpy.int32)
    lookup[occupied] = numpy.arange(len(occupied), dtype=numpy.int32)
    primary_rows = lookup[addresses]
    result = fallback.copy()
    fallback_rows = lookup[fallback]
    best = numpy.full(len(addresses), -numpy.inf, dtype=numpy.float32)
    for start in range(0, len(addresses), batch_size):
        stop = min(len(addresses), start + batch_size)
        vectors = numpy.asarray(documents[start:stop], dtype=numpy.float32)
        primary_means = means[primary_rows[start:stop]]
        fallback_means = means[fallback_rows[start:stop]]
        semantic = numpy.sum(vectors * fallback_means, axis=1,
                             dtype=numpy.float32)
        residual_primary = vectors - primary_means
        residual_secondary = vectors - fallback_means
        best[start:stop] = semantic - 0.25 * numpy.abs(numpy.sum(
            residual_primary * residual_secondary, axis=1,
            dtype=numpy.float32))
    for slot in range(maximum_candidates):
        candidate = candidates[addresses, slot]
        prior = priors[addresses, slot]
        rows = numpy.where(candidate < 65536, lookup[numpy.minimum(candidate, 65535)],
                           -1)
        for start in range(0, len(addresses), batch_size):
            stop = min(len(addresses), start + batch_size)
            valid = rows[start:stop] >= 0
            if not numpy.any(valid):
                continue
            positions = numpy.flatnonzero(valid) + start
            vectors = numpy.asarray(documents[positions], dtype=numpy.float32)
            primary_means = means[primary_rows[positions]]
            candidate_means = means[rows[positions]]
            semantic = numpy.sum(vectors * candidate_means, axis=1,
                                 dtype=numpy.float32)
            residual_primary = vectors - primary_means
            residual_secondary = vectors - candidate_means
            value = (semantic - 0.25 * numpy.abs(numpy.sum(
                residual_primary * residual_secondary, axis=1,
                dtype=numpy.float32)) + 0.05 * numpy.log1p(prior[positions]))
            current = best[positions]
            chosen = ((value > current)
                      | ((value == current)
                         & (candidate[positions] < result[positions])))
            selected = positions[chosen]
            best[selected] = value[chosen]
            result[selected] = candidate[selected]
    require(numpy.all(result != addresses),
            "training-fitted secondary mapping contains primary address")
    return result, {
        "training_fitted_address_count": fitted_addresses,
        "documents_changed_from_soar_fallback": int(numpy.count_nonzero(
            result != fallback)),
        "training_query_count": int(len(top100)),
        "maximum_cooccurrence_candidates_per_primary": maximum_candidates,
    }


def save_mapping(root: Path, seed: int, treatment: str,
                 primary: numpy.ndarray, secondary: numpy.ndarray,
                 metrics: dict[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"mapping-{treatment}-{seed}.npy"
    numpy.save(path, secondary, allow_pickle=False)
    return {
        "seed": seed, "treatment": treatment,
        "file": path.name, "sha256": sha256(path),
        "primary_addresses_sha256": array_sha256(primary),
        "secondary_addresses_sha256": array_sha256(secondary),
        "mapped_document_count": int(len(secondary)),
        "all_secondary_addresses_differ": bool(numpy.all(secondary != primary)),
        "metrics": metrics,
    }


def replicated_index(addresses: numpy.ndarray, occupied: numpy.ndarray,
                     secondary: numpy.ndarray) -> dict[str, numpy.ndarray]:
    require(secondary.shape == addresses.shape
            and numpy.all(secondary != addresses),
            "replicated document secondary equals primary")
    posting_addresses = numpy.concatenate((addresses, secondary)).astype(numpy.uint32)
    posting_documents = numpy.concatenate((
        numpy.arange(len(addresses), dtype=numpy.int32),
        numpy.arange(len(addresses), dtype=numpy.int32)))
    order = numpy.lexsort((posting_documents, posting_addresses))
    posting_addresses = posting_addresses[order]
    posting_documents = posting_documents[order]
    counts = numpy.bincount(posting_addresses, minlength=65536).astype(numpy.int64)
    offsets = numpy.zeros(65537, dtype=numpy.int64)
    offsets[1:] = numpy.cumsum(counts, dtype=numpy.int64)
    require(int(counts.sum()) == 2 * len(addresses),
            "replicated raw posting count differs")
    return {"counts": counts, "offsets": offsets,
            "posting_addresses": posting_addresses,
            "posting_documents": posting_documents}


def rebuilt_prototypes(documents: numpy.ndarray, addresses: numpy.ndarray,
                       base_occupied: numpy.ndarray, base_means: numpy.ndarray,
                       base_counts: numpy.ndarray, secondary: numpy.ndarray,
                       index: dict[str, numpy.ndarray], maximum: int = 8,
                       batch_size: int = 32768) -> tuple[
                           numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
    require(numpy.array_equal(occupied, base_occupied),
            "replicated occupied address set differs")
    sums = numpy.zeros((65536, documents.shape[1]), dtype=numpy.float32)
    sums[base_occupied] = (base_means
                           * base_counts[base_occupied, None].astype(numpy.float32))
    for start in range(0, len(addresses), batch_size):
        stop = min(len(addresses), start + batch_size)
        numpy.add.at(sums, secondary[start:stop],
                     numpy.asarray(documents[start:stop], dtype=numpy.float32))
    means = multi.normalized_rows(
        sums[occupied] / index["counts"][occupied, None].astype(numpy.float32))
    effective = numpy.minimum(index["counts"][occupied], maximum).astype(numpy.uint8)
    prototypes = numpy.zeros((maximum, len(occupied), documents.shape[1]),
                             dtype=numpy.float32)
    prototypes[0] = means
    address_to_row = numpy.full(65536, -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(len(occupied), dtype=numpy.int32)
    posting_addresses = index["posting_addresses"]
    posting_documents = index["posting_documents"]
    rows = address_to_row[posting_addresses]
    best_similarity = numpy.full(len(posting_documents), -numpy.inf,
                                 dtype=numpy.float32)
    selected = numpy.zeros(len(posting_documents), dtype=numpy.bool_)
    starts = index["offsets"][occupied]
    repeated = index["counts"][occupied]
    sentinel = numpy.int64(len(posting_documents))
    for slot in range(1, maximum):
        for start in range(0, len(posting_documents), batch_size):
            stop = min(len(posting_documents), start + batch_size)
            vectors = numpy.asarray(documents[posting_documents[start:stop]],
                                    dtype=numpy.float32)
            reference = prototypes[slot - 1, rows[start:stop]]
            similarity = numpy.sum(vectors * reference, axis=1,
                                   dtype=numpy.float32)
            best_similarity[start:stop] = numpy.maximum(
                best_similarity[start:stop], similarity)
        best_similarity[selected] = numpy.inf
        group_minimum = numpy.minimum.reduceat(best_similarity, starts)
        is_minimum = best_similarity == numpy.repeat(group_minimum, repeated)
        candidates = numpy.where(is_minimum,
                                 numpy.arange(len(posting_documents)), sentinel)
        chosen = numpy.minimum.reduceat(candidates, starts).astype(numpy.int64)
        active = effective > slot
        chosen_active = chosen[active]
        require(numpy.all(chosen_active < len(posting_documents)),
                "replicated K8 member selection failed")
        prototypes[slot, active] = multi.normalized_rows(
            documents[posting_documents[chosen_active]])
        selected[chosen_active] = True
    return occupied, prototypes, effective


def posting_documents(index: dict[str, Any], address: int) -> numpy.ndarray:
    start, stop = int(index["offsets"][address]), int(index["offsets"][address + 1])
    if "posting_documents" in index:
        return numpy.asarray(index["posting_documents"][start:stop],
                             dtype=numpy.int32)
    return numpy.asarray(index["order"][start:stop], dtype=numpy.int32)


def hard_unique_candidates(order: numpy.ndarray, index: dict[str, Any],
                           documents: int, maximum: int) -> tuple[
                               numpy.ndarray, numpy.ndarray]:
    selected = numpy.zeros(documents, dtype=numpy.bool_)
    opened = []
    count = 0
    for value in order[:1024].tolist():
        address = int(value)
        docs = posting_documents(index, address)
        new = docs[~selected[docs]]
        if count + len(new) > maximum:
            continue
        selected[new] = True
        count += len(new)
        opened.append(address)
        if count == maximum:
            break
    return numpy.flatnonzero(selected).astype(numpy.int64), numpy.asarray(
        opened, dtype=numpy.uint32)


def query_orders(queries: numpy.ndarray, occupied: numpy.ndarray,
                 prototypes: numpy.ndarray, effective: numpy.ndarray
                 ) -> list[numpy.ndarray]:
    _, scores, _ = next(multi.score_prefixes(
        queries, prototypes, effective, [8]))
    return [occupied[numpy.lexsort((occupied, -scores[row]))]
            for row in range(len(queries))]


def evaluate_orders(treatment: str, orders: list[numpy.ndarray],
                    index: dict[str, Any], addresses: numpy.ndarray,
                    data: dict[str, Any], positions: list[int],
                    top100: numpy.ndarray, discounts: numpy.ndarray,
                    contract: dict[str, Any], storage_factor: float,
                    privileged: bool = False) -> dict[str, Any]:
    maximum = int(len(data["document_ids"])
                  * contract["route"]["candidate_fraction_budget"])
    queries = []
    for local, position in enumerate(positions):
        target = numpy.asarray(top100[local, :10], dtype=numpy.int64)
        reserve = len(set(target.tolist())) if privileged else 0
        candidates, opened = hard_unique_candidates(
            orders[local], index, len(data["document_ids"]), maximum - reserve)
        if privileged:
            candidates = numpy.union1d(candidates, target).astype(numpy.int64)
        require(len(candidates) <= maximum,
                "replication hard unique-candidate budget exceeded")
        state = sequential.cascade_state(
            data, position, candidates, target, discounts, contract["cascade"])
        queries.append({
            "query_id": str(data["query_ids"][position]),
            "opened_address_count": int(len(opened)),
            "unique_candidate_count": int(len(candidates)),
            "candidate_fraction": len(candidates) / len(data["document_ids"]),
            "static_gain_coverage": float(discounts[numpy.isin(
                target, candidates)].sum(dtype=numpy.float64)
                / discounts.sum(dtype=numpy.float64)),
            "actionable_gain_coverage": state["coverage"],
            "exact_ndcg_at_10": state["ndcg_at_10"],
            "hamming_input_count": state["hamming_distance_evaluations"],
            "adc_input_count": state["adc_distance_evaluations"],
            "opened_address_sha256": scale.sequence_sha256(opened),
        })
    keys = ["opened_address_count", "unique_candidate_count", "candidate_fraction",
            "static_gain_coverage", "actionable_gain_coverage",
            "exact_ndcg_at_10", "hamming_input_count", "adc_input_count"]
    return {
        "treatment": treatment,
        "query_count": len(queries),
        "physical_storage_replication_factor": storage_factor,
        "raw_posting_count": int(len(addresses) * storage_factor),
        "privileged_diagnostic": privileged,
        **{key: float(numpy.mean([row[key] for row in queries],
                                 dtype=numpy.float64)) for key in keys},
        "queries": queries,
    }


def decision(rows: list[dict[str, Any]], contract: dict[str, Any]
             ) -> dict[str, Any]:
    comparisons = []
    for seed in contract["route"]["seeds"]:
        control = next(row for row in rows if row["seed"] == seed
                       and row["treatment"] == "single_assignment_control")
        for treatment in ["nearest_semantic_secondary",
                          "soar_complementary_secondary",
                          "training_fitted_complementary"]:
            learned = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == treatment)
            comparisons.append({
                "seed": seed, "treatment": treatment,
                "actionable_gain_improvement": learned[
                    "actionable_gain_coverage"] - control[
                        "actionable_gain_coverage"],
                "exact_ndcg_delta": learned["exact_ndcg_at_10"]
                                    - control["exact_ndcg_at_10"],
                "candidate_fraction": learned["candidate_fraction"],
            })
    success = {}
    for treatment in ["nearest_semantic_secondary",
                      "soar_complementary_secondary",
                      "training_fitted_complementary"]:
        current = [row for row in comparisons if row["treatment"] == treatment]
        success[treatment] = all(
            row["actionable_gain_improvement"] >= contract["decision"][
                "minimum_actionable_gain_improvement"]
            and row["candidate_fraction"] <= contract["decision"][
                "maximum_candidate_fraction"] for row in current)
    return {
        "de_1m_internal_comparisons": comparisons,
        "deployable_topology_success": success,
        "replication_topology_gate_passed": any(success.values()),
        "privileged_per_query_kept_diagnostic": True,
        "learned_reranker_used": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], materialization: dict[str, Any],
             split: dict[str, Any], external_ids: list[str],
             external_vectors: numpy.ndarray, args: argparse.Namespace) -> tuple[
                 list[dict[str, Any]], list[dict[str, Any]],
                 list[dict[str, Any]]]:
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = [by_id[value] for value in split["training_query_ids"]]
    configuration_positions = [by_id[value]
                               for value in split["configuration_selection_query_ids"]]
    internal_positions = [by_id[value]
                          for value in split["internal_evaluation_query_ids"]]
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][training_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    require(len(split["training_query_ids"]) + len(external_ids) == 8141,
            "replication training pool differs")
    training_top100, training_teacher = decoupled.exact_teacher(
        args.decoupled_teacher_cache_root / "training-top100",
        data["documents"], data["document_ids"], pool_vectors, 100)
    configuration_queries = numpy.asarray(
        data["queries"][configuration_positions], dtype=numpy.float32)
    internal_queries = numpy.asarray(data["queries"][internal_positions],
                                     dtype=numpy.float32)
    configuration_top100, _ = decoupled.exact_teacher(
        args.decoupled_teacher_cache_root / "configuration-top100",
        data["documents"], data["document_ids"], configuration_queries, 100)
    internal_top100, _ = decoupled.exact_teacher(
        args.decoupled_teacher_cache_root / "internal-top100",
        data["documents"], data["document_ids"], internal_queries, 100)
    discounts = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    mapping_artifacts = []
    mappings: dict[tuple[int, str], numpy.ndarray] = {}
    base_states: dict[int, tuple[Any, ...]] = {}
    # Freeze every global mapping before any configuration/internal evaluation.
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        means = prototypes[0]
        nearest, soar, neighbor_metrics = neighbor_mappings(
            data["documents"], addresses, occupied, means)
        fitted, fitted_metrics = training_mapping(
            data["documents"], addresses, occupied, means, soar,
            training_top100)
        for treatment, mapping, metrics in [
                ("nearest_semantic_secondary", nearest, neighbor_metrics),
                ("soar_complementary_secondary", soar, neighbor_metrics),
                ("training_fitted_complementary", fitted, fitted_metrics)]:
            mappings[(seed, treatment)] = mapping
            mapping_artifacts.append(save_mapping(
                args.mapping_root, seed, treatment, addresses, mapping, metrics))
        base_states[seed] = (addresses, index, occupied, prototypes, effective, means)
    configuration_rows = []
    internal_rows = []
    for seed in contract["route"]["seeds"]:
        addresses, control_index, occupied, control_prototypes, control_effective, means = (
            base_states[seed])
        control_config_orders = query_orders(
            configuration_queries, occupied, control_prototypes, control_effective)
        control_internal_orders = query_orders(
            internal_queries, occupied, control_prototypes, control_effective)
        control_config = evaluate_orders(
            "single_assignment_control", control_config_orders, control_index,
            addresses, data, configuration_positions, configuration_top100,
            discounts, contract, 1.0)
        control_internal = evaluate_orders(
            "single_assignment_control", control_internal_orders, control_index,
            addresses, data, internal_positions, internal_top100, discounts,
            contract, 1.0)
        configuration_rows.append({"dataset": "de-1m", "seed": seed,
                                   **control_config})
        internal_rows.append({"dataset": "de-1m", "seed": seed,
                              **control_internal})
        for treatment in ["nearest_semantic_secondary",
                          "soar_complementary_secondary",
                          "training_fitted_complementary"]:
            secondary = mappings[(seed, treatment)]
            index = replicated_index(addresses, occupied, secondary)
            current_occupied, prototypes, effective = rebuilt_prototypes(
                data["documents"], addresses, occupied, means,
                control_index["counts"], secondary, index)
            config_orders = query_orders(
                configuration_queries, current_occupied, prototypes, effective)
            internal_orders = query_orders(
                internal_queries, current_occupied, prototypes, effective)
            configuration_rows.append({
                "dataset": "de-1m", "seed": seed,
                **evaluate_orders(
                    treatment, config_orders, index, addresses, data,
                    configuration_positions, configuration_top100, discounts,
                    contract, 2.0)})
            internal_rows.append({
                "dataset": "de-1m", "seed": seed,
                **evaluate_orders(
                    treatment, internal_orders, index, addresses, data,
                    internal_positions, internal_top100, discounts,
                    contract, 2.0)})
            del index, prototypes, effective, config_orders, internal_orders
            gc.collect()
        configuration_rows.append({
            "dataset": "de-1m", "seed": seed,
            **evaluate_orders(
                "privileged_per_query_replication_ceiling", control_config_orders,
                control_index, addresses, data, configuration_positions,
                configuration_top100, discounts, contract, 2.0, True)})
        internal_rows.append({
            "dataset": "de-1m", "seed": seed,
            **evaluate_orders(
                "privileged_per_query_replication_ceiling", control_internal_orders,
                control_index, addresses, data, internal_positions,
                internal_top100, discounts, contract, 2.0, True)})
        del base_states[seed]
        gc.collect()
    mapping_artifacts.sort(key=lambda row: (row["seed"], row["treatment"]))
    return mapping_artifacts, configuration_rows, internal_rows


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    _, materialization, split, external_ids, external_vectors, _ = validate_parent(
        contract, args)
    mappings, configuration, internal = evaluate(
        contract, materialization, split, external_ids, external_vectors, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_replication_topology_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "matrix": planner.plan(contract),
        "mapping_artifacts": mappings,
        "configuration_rows": configuration,
        "internal_rows": internal,
        "decision": decision(internal, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-replication-topology.example.json")
    addresses = numpy.asarray([0, 0, 1, 2], dtype=numpy.uint32)
    occupied = numpy.asarray([0, 1, 2], dtype=numpy.uint32)
    secondary = numpy.asarray([1, 1, 2, 0], dtype=numpy.uint32)
    index = replicated_index(addresses, occupied, secondary)
    require(index["counts"].sum() == 8
            and posting_documents(index, 0).tolist() == [0, 1, 3]
            and planner.plan(contract)["global_mapping_count"] == 9,
            "replication-topology index self-test differs")
    candidates, opened = hard_unique_candidates(
        numpy.asarray([0, 1, 2], dtype=numpy.uint32), index, 4, 4)
    require(candidates.tolist() == [0, 1, 2, 3] and opened.tolist() == [0, 1],
            "replication unique-union self-test differs")
    print("NeuRoute replication-topology runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-replication-topology.example.json")
    for name in [
            "decoupled-result", "decoupled-evidence",
            "feasible-frontier-result", "feasible-frontier-evidence",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "decoupled-teacher-cache-root", "mapping-root",
            "output"]:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all replication-topology paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-replication-topology: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
