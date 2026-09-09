#!/usr/bin/env python3
"""Materialize, train, and evaluate frozen-K32 conditional set coverage."""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import importlib
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


planner = load("neuroute_r4_conditional_set_coverage_planner",
               "plan-neuroute-r4-conditional-set-coverage.py")
saturation = load("neuroute_r4_conditional_set_coverage_parent",
                  "run-neuroute-r4-coverage-saturation.py")
teacher = saturation.teacher
fine = saturation.fine
r4 = saturation.r4
base = saturation.base
prototype = saturation.prototype
multi = saturation.multi
scale = saturation.scale
task = saturation.task
ambiguity = saturation.ambiguity


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return saturation.sha256(path)


def canonical(value: Any) -> bytes:
    return saturation.canonical(value)


def array_sha256(value: numpy.ndarray) -> str:
    return saturation.array_sha256(value)


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r4-conditional-set-coverage.py",
        "run-neuroute-r4-conditional-set-coverage.py",
        "run-neuroute-r4-coverage-saturation.py",
        "run-neuroute-r4-teacher-selected-representatives.py",
        "run-neuroute-r4-fine-grained-interactions.py",
        "run-neuroute-r4-document-representatives.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any],
                                   list[str], numpy.ndarray]:
    actual = {
        "saturation_result_sha256": sha256(args.saturation_result),
        "saturation_evidence_sha256": sha256(args.saturation_evidence),
        "teacher_selection_result_sha256": sha256(args.teacher_result),
        "teacher_selection_evidence_sha256": sha256(args.teacher_evidence),
    }
    require(actual == contract["activation"],
            f"R4 conditional-coverage activation bytes differ: {actual!r}")
    saturation_result = json.loads(args.saturation_result.read_text(
        encoding="utf-8"))
    saturation_evidence = json.loads(args.saturation_evidence.read_text(
        encoding="utf-8"))
    teacher_result = json.loads(args.teacher_result.read_text(encoding="utf-8"))
    teacher_evidence = json.loads(args.teacher_evidence.read_text(
        encoding="utf-8"))
    require(saturation_result.get("family")
            == "neuroute_r4_coverage_saturation_result"
            and saturation_result.get("configuration_selection", {}).get(
                "selected_k") == 32
            and saturation_result.get("decision", {}).get(
                "internal_selected_k_still_passes_rule") is True
            and saturation_evidence.get("passed") is True
            and saturation_evidence.get("result_byte_replay_passed") is True,
            "R4 conditional-coverage saturation parent differs")
    require(teacher_result.get("family")
            == "neuroute_r4_teacher_selected_representatives_result"
            and teacher_evidence.get("passed") is True
            and teacher_evidence.get("result_byte_replay_passed") is True,
            "R4 conditional-coverage independent-win parent differs")
    parent_contract = saturation.planner.load_contract(
        THIS / "neuroute-r4-coverage-saturation.example.json")
    (fine_result, r4_result, materialization, split,
     external_ids, external_vectors) = saturation.validate_activation(
         parent_contract, args)
    return (saturation_result, teacher_result, fine_result, r4_result,
            materialization, split, external_ids, external_vectors)


def normalized_training_address_rows(
        shortlists: numpy.ndarray, targets: numpy.ndarray,
        address_count: int) -> tuple[list[list[tuple[int, float]]], dict[str, Any]]:
    require(shortlists.shape == targets.shape,
            "R4 conditional-coverage target shape differs")
    rows: list[list[tuple[int, float]]] = [[] for _ in range(address_count)]
    positive_pairs = 0
    nonzero_queries = 0
    normalized_sum = 0.0
    for query_index in range(len(shortlists)):
        target = numpy.asarray(targets[query_index], dtype=numpy.float64)
        positive = numpy.flatnonzero(target > 0.0)
        total = float(target[positive].sum(dtype=numpy.float64))
        if not len(positive) or total <= 0.0:
            continue
        nonzero_queries += 1
        for position in positive:
            address = int(shortlists[query_index, position])
            weight = float(target[position] / total)
            rows[address].append((query_index, weight))
            normalized_sum += weight
            positive_pairs += 1
    return rows, {
        "training_query_count": len(shortlists),
        "nonzero_target_query_count": nonzero_queries,
        "positive_query_address_pair_count": positive_pairs,
        "normalized_target_weight_sum": normalized_sum,
    }


def greedy_coverage(posting: numpy.ndarray, scores: numpy.ndarray,
                    weights: numpy.ndarray, anchors: numpy.ndarray,
                    limit: int) -> numpy.ndarray:
    posting = numpy.asarray(posting, dtype=numpy.int32)
    selected = [int(value) for value in anchors[:limit]]
    require(len(selected) == len(set(selected))
            and set(selected).issubset(set(int(value) for value in posting)),
            "R4 conditional-coverage anchors differ")
    lookup = {int(value): index for index, value in enumerate(posting)}
    available = numpy.ones(len(posting), dtype=bool)
    coverage = numpy.full(len(weights), -1.0, dtype=numpy.float32)
    for value in selected:
        column = lookup[value]
        available[column] = False
        coverage = numpy.maximum(coverage, scores[:, column])
    while len(selected) < limit:
        improvements = numpy.maximum(
            scores - coverage[:, numpy.newaxis], numpy.float32(0.0))
        gains = numpy.asarray(weights @ improvements, dtype=numpy.float64)
        gains[~available] = -numpy.inf
        require(numpy.any(numpy.isfinite(gains)),
                "R4 conditional-coverage exhausted posting")
        order = numpy.lexsort((posting, -gains))
        column = int(next(value for value in order if available[value]))
        selected.append(int(posting[column]))
        available[column] = False
        coverage = numpy.maximum(coverage, scores[:, column])
    return numpy.asarray(selected, dtype=numpy.int32)


def select_conditional_recipes(
        documents: numpy.ndarray, index: dict[str, Any],
        occupied: numpy.ndarray, baseline: numpy.ndarray,
        effective: numpy.ndarray, queries: numpy.ndarray,
        shortlists: numpy.ndarray, targets: numpy.ndarray,
        recipes: list[dict[str, Any]]) -> tuple[dict[str, numpy.ndarray],
                                                dict[str, dict[str, Any]]]:
    require(baseline.shape[0] >= 32 and baseline.shape[1] == len(occupied)
            and len(queries) == len(shortlists),
            "R4 conditional-coverage selection input differs")
    address_rows, target_audit = normalized_training_address_rows(
        shortlists, targets, len(index["counts"]))
    outputs = {row["id"]: numpy.full((32, len(occupied)), -1,
                                      dtype=numpy.int32)
               for row in recipes}
    supported_addresses = 0
    order = numpy.asarray(index["order"], dtype=numpy.int32)
    offsets = numpy.asarray(index["offsets"], dtype=numpy.int64)
    counts = numpy.asarray(index["counts"], dtype=numpy.int64)
    for occupied_row, address_value in enumerate(occupied):
        address = int(address_value)
        limit = min(int(effective[occupied_row]), 32)
        posting = order[offsets[address]:offsets[address] + counts[address]]
        training = address_rows[address]
        if not training:
            for recipe in recipes:
                outputs[recipe["id"]][:limit, occupied_row] = baseline[
                    :limit, occupied_row]
            continue
        supported_addresses += 1
        query_positions = numpy.asarray([value[0] for value in training],
                                        dtype=numpy.int64)
        weights = numpy.asarray([value[1] for value in training],
                                dtype=numpy.float64)
        scores = numpy.asarray(
            numpy.asarray(queries[query_positions], dtype=numpy.float32)
            @ numpy.asarray(documents[posting], dtype=numpy.float32).T,
            dtype=numpy.float32)
        for recipe in recipes:
            anchor_count = min(int(recipe["farthest_first_anchor_count"]), limit)
            anchors = numpy.asarray(baseline[:anchor_count, occupied_row],
                                    dtype=numpy.int32)
            outputs[recipe["id"]][:limit, occupied_row] = greedy_coverage(
                posting, scores, weights, anchors, limit)
        del query_positions, weights, scores
    audits = {}
    for recipe in recipes:
        treatment = recipe["id"]
        value = r4.audit_representatives(
            numpy.asarray(index["addresses"], dtype=numpy.uint32)
            if "addresses" in index else numpy.empty(0, dtype=numpy.uint32),
            occupied, outputs[treatment], numpy.minimum(effective, 32),
            index["counts"])
        value.update(target_audit)
        value.update({
            "treatment": treatment,
            "farthest_first_anchor_count": recipe[
                "farthest_first_anchor_count"],
            "conditional_coverage_fill": True,
            "training_supported_address_count": supported_addresses,
            "configuration_or_internal_selection_query_count": 0,
            "runtime_query_dependent_selection": False,
        })
        audits[treatment] = value
    return outputs, audits


def artifact(path: Path, role: str, value: numpy.ndarray) -> dict[str, Any]:
    numpy.save(path, value, allow_pickle=False)
    return {
        "role": role, "path": path.name, "sha256": sha256(path),
        "bytes": path.stat().st_size, "dtype": str(value.dtype),
        "shape": [int(current) for current in value.shape],
    }


def saturation_state(args: argparse.Namespace, parent: dict[str, Any],
                     seed: int) -> dict[str, numpy.ndarray]:
    namespace = argparse.Namespace(**vars(args))
    namespace.materialization_root = args.saturation_materialization_root
    return saturation.representative_state(
        namespace, parent["materializations"], seed)


def independent_wins_state(args: argparse.Namespace, parent: dict[str, Any],
                           seed: int) -> dict[str, numpy.ndarray]:
    namespace = argparse.Namespace(**vars(args))
    namespace.selection_root = args.teacher_selection_root
    return teacher.selection_state(
        namespace, parent["selection_materializations"], seed)


def materialize_selections(
        contract: dict[str, Any], saturation_result: dict[str, Any],
        materialization: dict[str, Any], pool_ids: list[str],
        pool_vectors: numpy.ndarray, data: dict[str, Any],
        args: argparse.Namespace) -> list[dict[str, Any]]:
    dataset = next(row for row in materialization["datasets"]
                   if row["id"] == "de-1m")
    recipes = [row for row in contract["selection_recipes"]
               if row["conditional_coverage_fill"]]
    rows = []
    args.selection_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        index["addresses"] = addresses
        baseline = saturation_state(args, saturation_result, seed)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        selections, audits = select_conditional_recipes(
            data["documents"], index, baseline["occupied"],
            baseline["positions"], baseline["effective"], pool_vectors,
            shortlists, targets, recipes)
        root = args.selection_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for recipe in recipes:
            treatment = recipe["id"]
            artifacts.append(artifact(
                root / f"document-positions-{treatment}.npy",
                f"document_positions_{treatment}", selections[treatment]))
        rows.append({
            "seed": seed,
            "document_addresses_sha256": route["document_addresses"]["sha256"],
            "training_cache_manifest_sha256": contract[
                "cache_manifest_sha256"][str(seed)],
            "training_query_ids_sha256": scale.hash_ids(
                numpy.asarray(pool_ids, dtype=object)),
            "baseline_positions_sha256": array_sha256(
                numpy.asarray(baseline["positions"][:32])),
            "audits": audits,
            "artifacts": artifacts,
        })
        del addresses, index, baseline, shortlists, targets, selections
        gc.collect()
    return rows


def selection_artifact(args: argparse.Namespace, rows: list[dict[str, Any]],
                       seed: int, treatment: str) -> Path:
    row = next(value for value in rows if value["seed"] == seed)
    role = f"document_positions_{treatment}"
    descriptor = next(value for value in row["artifacts"]
                      if value["role"] == role)
    path = args.selection_root / f"seed-{seed}" / descriptor["path"]
    require(path.is_file() and sha256(path) == descriptor["sha256"]
            and path.stat().st_size == descriptor["bytes"],
            f"R4 conditional-coverage artifact differs: {seed}/{treatment}")
    return path


def treatment_state(args: argparse.Namespace, selections: list[dict[str, Any]],
                    saturation_result: dict[str, Any],
                    teacher_result: dict[str, Any], seed: int,
                    treatment: str) -> dict[str, numpy.ndarray]:
    baseline = saturation_state(args, saturation_result, seed)
    if treatment == "ff32":
        return baseline
    if treatment == "independent_wins32":
        return independent_wins_state(args, teacher_result, seed)
    return {
        "occupied": baseline["occupied"],
        "positions": numpy.load(selection_artifact(
            args, selections, seed, treatment), mmap_mode="r"),
        "effective": baseline["effective"],
    }


def interaction_identity(contract: dict[str, Any], seed: int, treatment: str,
                         queries: numpy.ndarray, shortlists: numpy.ndarray,
                         state: dict[str, numpy.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "neuroute_r4_conditional_set_coverage_cache_identity",
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
        "seed": seed, "treatment": treatment,
        "query_vectors_sha256": array_sha256(queries),
        "shortlists_sha256": array_sha256(shortlists),
        "occupied_sha256": array_sha256(state["occupied"]),
        "positions_sha256": array_sha256(state["positions"]),
        "effective_sha256": array_sha256(state["effective"]),
        "representative_k": 32,
    }


def cached_maximums(root: Path, identity: dict[str, Any],
                    queries: numpy.ndarray, shortlists: numpy.ndarray,
                    documents: numpy.ndarray, state: dict[str, numpy.ndarray],
                    batch_queries: int) -> tuple[numpy.ndarray, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    output_path = root / "maximum-interactions.npy"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity and output_path.is_file()
                and sha256(output_path) == manifest["output"]["sha256"],
                "R4 conditional-coverage interaction cache differs")
        return numpy.load(output_path, mmap_mode="r"), manifest
    value = saturation.maximum_interactions(
        queries, shortlists, documents, state, [32], batch_queries, output_path)
    value.flush()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_r4_conditional_set_coverage_interaction_cache",
        "identity": identity,
        "output": {
            "path": output_path.name, "sha256": sha256(output_path),
            "bytes": output_path.stat().st_size,
            "shape": [int(current) for current in value.shape],
            "dtype": str(value.dtype),
        },
    }
    manifest_path.write_bytes(canonical(manifest))
    return numpy.load(output_path, mmap_mode="r"), manifest


def training_pool(split: dict[str, Any], external_ids: list[str],
                  external_vectors: numpy.ndarray, data: dict[str, Any]
                  ) -> tuple[list[str], numpy.ndarray]:
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [by_id[value] for value in split["training_query_ids"]]
    ids = list(split["training_query_ids"]) + external_ids
    vectors = numpy.concatenate((
        numpy.asarray(data["queries"][positions], dtype=numpy.float32),
        external_vectors), axis=0)
    require(vectors.shape == (8141, 384),
            "R4 conditional-coverage training pool differs")
    return ids, vectors


def train_all(contract: dict[str, Any], selections: list[dict[str, Any]],
              saturation_result: dict[str, Any],
              teacher_result: dict[str, Any], split: dict[str, Any],
              external_ids: list[str], external_vectors: numpy.ndarray,
              data: dict[str, Any], args: argparse.Namespace
              ) -> list[dict[str, Any]]:
    pool_ids, pool_vectors = training_pool(
        split, external_ids, external_vectors, data)
    parent_contract = fine.planner.load_contract(
        THIS / "neuroute-r4-fine-grained-interactions.example.json")
    models = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        scalar_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        for treatment in planner.learned_treatments(contract):
            state = treatment_state(
                args, selections, saturation_result, teacher_result, seed,
                treatment)
            identity = interaction_identity(
                contract, seed, treatment, pool_vectors, shortlists, state)
            maximums, interaction_manifest = cached_maximums(
                args.interaction_cache_root / f"seed-{seed}" / treatment,
                identity, pool_vectors, shortlists, data["documents"], state,
                int(contract["training"]["interaction_batch_queries"]))
            model_seed = seed ^ 0x13579BD ^ 8141
            path = args.model_root / f"model-{treatment}-{seed}.npz"
            if path.is_file():
                arrays, saved_mean, saved_deviation, metadata = base.read_model(path)
                require(metadata.get("family")
                        == "neuroute_r4_conditional_set_coverage_model"
                        and metadata.get("seed") == seed
                        and metadata.get("treatment") == treatment
                        and metadata.get("contract_sha256") == sha256(args.contract)
                        and metadata.get("interaction_cache_sha256")
                        == interaction_manifest["output"]["sha256"]
                        and numpy.array_equal(saved_mean, scalar_mean)
                        and numpy.array_equal(saved_deviation, scalar_deviation),
                        "R4 conditional-coverage resumable model differs")
            else:
                arrays, training = fine.train_model(
                    contract["model"]["architecture"], pool_vectors,
                    scalar_features, targets,
                    saturation.DummyInteractionView(maximums),
                    saturation.MaximumAggregateView(maximums, 0), scalar_mean,
                    scalar_deviation,
                    saturation.maximum_normalizers(maximums, 0), model_seed,
                    parent_contract)
                training["representative_selection_treatment"] = treatment
                training["maximum_interaction_only"] = True
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_r4_conditional_set_coverage_model",
                    "seed": seed, "treatment": treatment,
                    "model_seed": model_seed,
                    "architecture": contract["model"]["architecture"],
                    "training_query_count": len(pool_vectors),
                    "training_query_ids_sha256": scale.hash_ids(
                        numpy.asarray(pool_ids, dtype=object)),
                    "contract_sha256": sha256(args.contract),
                    "cache_manifest_sha256": contract[
                        "cache_manifest_sha256"][str(seed)],
                    "selected_positions_sha256": array_sha256(
                        numpy.asarray(state["positions"][:32])),
                    "interaction_cache_sha256": interaction_manifest[
                        "output"]["sha256"],
                    "training": training,
                }
                fine.save_model(
                    path, arrays, scalar_mean, scalar_deviation, metadata)
            models.append({
                "seed": seed, "treatment": treatment,
                "file": path.name, "sha256": sha256(path),
                "metadata": metadata,
            })
            del state, maximums
            gc.collect()
        del shortlists, scalar_features, targets
        gc.collect()
    return models


def renamed_parent_row(parent: dict[str, Any], partition: str, seed: int
                       ) -> dict[str, Any]:
    value = copy.deepcopy(next(
        row for row in parent[f"{partition}_rows"]
        if row["seed"] == seed and row["treatment"] == "actual_k32_max"))
    value["treatment"] = "ff32"
    return value


def evaluate_partition(
        name: str, contract: dict[str, Any], materialization: dict[str, Any],
        selections: list[dict[str, Any]], saturation_result: dict[str, Any],
        teacher_result: dict[str, Any], data: dict[str, Any],
        positions: list[int], models: list[dict[str, Any]],
        args: argparse.Namespace) -> list[dict[str, Any]]:
    oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    dataset = next(row for row in materialization["datasets"]
                   if row["id"] == "de-1m")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        training_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            training_features)
        targets = prototype.density_targets(
            shortlists, oracle, positions, addresses, index["counts"], discounts)
        orders: dict[str, list[numpy.ndarray]] = {
            "prototype_order": [row.copy() for row in shortlists],
            "privileged_gain_density": [prototype.ordered(
                targets[row], shortlists[row], index["counts"])
                for row in range(len(shortlists))],
        }
        works: dict[str, numpy.ndarray] = {}
        for treatment in planner.learned_treatments(contract):
            state = treatment_state(
                args, selections, saturation_result, teacher_result, seed,
                treatment)
            maximums = saturation.maximum_interactions(
                queries, shortlists, data["documents"], state, [32],
                int(contract["training"]["interaction_batch_queries"]))
            model = next(row for row in models
                         if row["seed"] == seed
                         and row["treatment"] == treatment)
            arrays, saved_mean, saved_deviation, metadata = base.read_model(
                args.model_root / model["file"])
            require(metadata == model["metadata"]
                    and numpy.array_equal(saved_mean, scalar_mean)
                    and numpy.array_equal(saved_deviation, scalar_deviation),
                    "R4 conditional-coverage evaluation model differs")
            scores = fine.numpy_scores(
                contract["model"]["architecture"], queries, scalar_features,
                saturation.DummyInteractionView(maximums)[:],
                saturation.MaximumAggregateView(maximums, 0)[:], arrays,
                saved_mean, saved_deviation)
            orders[treatment] = [prototype.ordered(scores[row], shortlists[row])
                                 for row in range(len(shortlists))]
            works[treatment] = saturation.representative_work(
                shortlists, state, 32)
            del state, maximums, arrays, scores
        ff_state = treatment_state(
            args, selections, saturation_result, teacher_result, seed, "ff32")
        works["ff32"] = saturation.representative_work(shortlists, ff_state, 32)
        parent_row = renamed_parent_row(saturation_result, name, seed)
        parent_row["partition"] = name
        parent_row["representative_work"] = {
            "mean_dot_products_per_query": float(numpy.mean(
                works["ff32"], dtype=numpy.float64)),
            "p50_dot_products_per_query": float(numpy.percentile(
                works["ff32"], 50)),
            "p95_dot_products_per_query": float(numpy.percentile(
                works["ff32"], 95)),
            "maximum_dot_products_per_query": int(numpy.max(works["ff32"])),
        }
        for query, work in zip(parent_row["queries"], works["ff32"]):
            query["representative_dot_products"] = int(work)
            query["addresses_scored"] = 1024
        for treatment in ["prototype_order", *planner.treatments(contract),
                          "privileged_gain_density"]:
            if treatment == "ff32":
                rows.append(parent_row)
                continue
            value = saturation.treatment_rows(
                treatment, orders[treatment], shortlists, addresses, index,
                data, positions, oracle, discounts, contract,
                works.get(treatment))
            rows.append({"partition": name, "dataset": "de-1m",
                         "seed": seed, **value})
        del addresses, index, occupied, prototypes, effective, queries
        del shortlists, scalar_features, training_features, targets, orders
        del works, ff_state
        gc.collect()
    return rows


def headline(row: dict[str, Any], fraction: float) -> dict[str, Any]:
    return saturation.headline(row, fraction)


def select_recipe(rows: list[dict[str, Any]], contract: dict[str, Any]
                  ) -> dict[str, Any]:
    fraction = float(contract["evaluation"]["headline_candidate_fraction"])
    values = []
    for treatment in planner.treatments(contract):
        current = [headline(row, fraction) for row in rows
                   if row["treatment"] == treatment]
        require(len(current) == len(contract["route"]["seeds"]),
                f"R4 conditional-coverage selection rows differ: {treatment}")
        values.append({
            "treatment": treatment,
            "mean_actionable_gain": float(numpy.mean([
                row["actionable_gain_coverage"] for row in current],
                dtype=numpy.float64)),
            "mean_exact_ndcg_at_10": float(numpy.mean([
                row["exact_ndcg_at_10"] for row in current],
                dtype=numpy.float64)),
            "mean_candidate_fraction": float(numpy.mean([
                row["candidate_fraction"] for row in current],
                dtype=numpy.float64)),
            "per_seed_actionable_gain": [
                row["actionable_gain_coverage"] for row in current],
            "per_seed_exact_ndcg_at_10": [
                row["exact_ndcg_at_10"] for row in current],
        })
    priority = {value: index for index, value in enumerate(
        contract["configuration_selection"]["tie_break_priority"])}
    selected = min(values, key=lambda row: (
        -row["mean_actionable_gain"], priority[row["treatment"]]))
    return {
        "selection_partition": "configuration",
        "selected_treatment": selected["treatment"],
        "objective": "maximum_mean_actionable_gain_at_headline_budget",
        "rows": values,
        "tie_break_priority": contract["configuration_selection"][
            "tie_break_priority"],
    }


def parent_replay(rows: list[dict[str, Any]], parent: dict[str, Any],
                  partition: str, contract: dict[str, Any]
                  ) -> list[dict[str, Any]]:
    result = []
    for seed in contract["route"]["seeds"]:
        current = next(row for row in rows if row["seed"] == seed
                       and row["treatment"] == "ff32")
        frozen = next(row for row in parent[f"{partition}_rows"]
                      if row["seed"] == seed
                      and row["treatment"] == "actual_k32_max")
        deltas = []
        for fraction in contract["evaluation"]["candidate_fraction_budgets"]:
            current_value = headline(current, fraction)
            frozen_value = headline(frozen, fraction)
            deltas.append({
                "candidate_fraction_budget": fraction,
                "actionable_delta": current_value["actionable_gain_coverage"]
                - frozen_value["actionable_gain_coverage"],
                "ndcg_delta": current_value["exact_ndcg_at_10"]
                - frozen_value["exact_ndcg_at_10"],
                "candidate_fraction_delta": current_value["candidate_fraction"]
                - frozen_value["candidate_fraction"],
            })
        result.append({"seed": seed, "partition": partition,
                       "budget_deltas": deltas})
    return result


def decision(internal: list[dict[str, Any]],
             configuration: dict[str, Any], contract: dict[str, Any]
             ) -> dict[str, Any]:
    internal_summary = select_recipe(internal, contract)
    selected = configuration["selected_treatment"]
    selected_row = next(row for row in internal_summary["rows"]
                        if row["treatment"] == selected)
    baseline = next(row for row in internal_summary["rows"]
                    if row["treatment"] == "ff32")
    deltas = [value - reference for value, reference in zip(
        selected_row["per_seed_actionable_gain"],
        baseline["per_seed_actionable_gain"])]
    threshold = contract["decision"]
    return {
        "selected_recipe": selected,
        "internal_selected_recipe_row": selected_row,
        "internal_all_recipe_rows": internal_summary["rows"],
        "internal_mean_actionable_delta_vs_ff32": (
            selected_row["mean_actionable_gain"]
            - baseline["mean_actionable_gain"]),
        "internal_every_seed_actionable_deltas_vs_ff32": deltas,
        "conditional_coverage_progress_gate_passed": bool(
            selected != "ff32"
            and selected_row["mean_actionable_gain"]
            - baseline["mean_actionable_gain"]
            >= threshold["minimum_mean_actionable_improvement"]
            and min(deltas) >= threshold[
                "minimum_every_seed_actionable_delta"]),
        "models_frozen_before_configuration": True,
        "internal_opened_after_configuration_selection": True,
        "configuration_or_internal_selection_queries": 0,
        "compression_measured": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (saturation_result, teacher_result, fine_result, r4_result,
     materialization, split, external_ids,
     external_vectors) = validate_activation(contract, args)
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(
        scale_config, args.de_1m_e5_root, args.de_1m_input_root)
    pool_ids, pool_vectors = training_pool(
        split, external_ids, external_vectors, data)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    configuration_positions = [by_id[value] for value in split[
        "configuration_selection_query_ids"]]
    internal_positions = [by_id[value] for value in split[
        "internal_evaluation_query_ids"]]
    selections = materialize_selections(
        contract, saturation_result, materialization, pool_ids, pool_vectors,
        data, args)
    models = train_all(
        contract, selections, saturation_result, teacher_result, split,
        external_ids, external_vectors, data, args)
    configuration_rows = evaluate_partition(
        "configuration", contract, materialization, selections,
        saturation_result, teacher_result, data, configuration_positions,
        models, args)
    configuration_selection = select_recipe(configuration_rows, contract)
    internal_rows = evaluate_partition(
        "internal", contract, materialization, selections, saturation_result,
        teacher_result, data, internal_positions, models, args)
    replay = (parent_replay(
        configuration_rows, saturation_result, "configuration", contract)
        + parent_replay(internal_rows, saturation_result, "internal", contract))
    replay_passed = all(
        abs(value[key]) <= 1.0e-12
        for row in replay for value in row["budget_deltas"]
        for key in ("actionable_delta", "ndcg_delta",
                    "candidate_fraction_delta"))
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_conditional_set_coverage_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "torch_version": importlib.import_module("torch").__version__,
            "device": contract["training"]["device"],
            "torch_threads": contract["training"]["torch_threads"],
        },
        "matrix": planner.plan(contract),
        "selection_materializations": selections,
        "models": models,
        "frozen_ff32_parent_models": saturation_result[
            "frozen_k32_parent_models"],
        "configuration_rows": configuration_rows,
        "configuration_selection": configuration_selection,
        "internal_rows": internal_rows,
        "ff32_parent_replay": replay,
        "ff32_parent_replay_passed": replay_passed,
        "decision": decision(internal_rows, configuration_selection, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r4-conditional-set-coverage.example.json")
    posting = numpy.asarray([9, 4, 7], dtype=numpy.int32)
    scores = numpy.asarray([[0.8, 0.9, 0.1], [0.8, 0.0, 1.0]],
                           dtype=numpy.float32)
    weights = numpy.asarray([0.5, 0.5], dtype=numpy.float64)
    pure = greedy_coverage(
        posting, scores, weights, numpy.empty(0, dtype=numpy.int32), 2)
    anchored = greedy_coverage(
        posting, scores, weights, numpy.asarray([4], dtype=numpy.int32), 2)
    require(pure.tolist() == [9, 7]
            and anchored.tolist() == [4, 7]
            and planner.plan(contract)["model_fits"] == 15,
            "R4 conditional-coverage runner self-test differs")
    print("NeuRoute R4 conditional set-coverage runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-conditional-set-coverage.example.json")
    for name in [
            "saturation-result", "saturation-evidence",
            "saturation-materialization-root", "teacher-result",
            "teacher-evidence", "teacher-selection-root", "fine-result",
            "fine-evidence", "r4-result", "r4-evidence",
            "r4-materialization-root", "multilingual-query-root",
            "width-materialization-root", "german-split-result",
            "de-1m-e5-root", "de-1m-input-root", "parent-cache-root",
            "selection-root", "interaction-cache-root", "model-root",
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
            parser.error("all R4 conditional-coverage paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r4-conditional-set-coverage: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
