#!/usr/bin/env python3
"""Measure strict-prefix actual-document coverage saturation on frozen DE-1M."""

from __future__ import annotations

import argparse
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


planner = load("neuroute_r4_coverage_saturation_planner",
               "plan-neuroute-r4-coverage-saturation.py")
teacher = load("neuroute_r4_coverage_saturation_parent",
               "run-neuroute-r4-teacher-selected-representatives.py")
fine = teacher.fine
r4 = fine.r4
base = fine.base
prototype = fine.prototype
multi = fine.multi
scale = fine.scale
task = fine.task
ambiguity = fine.ambiguity
sequential = fine.sequential
frontier = fine.frontier


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return fine.sha256(path)


def canonical(value: Any) -> bytes:
    return fine.canonical(value)


def array_sha256(value: numpy.ndarray) -> str:
    return fine.array_sha256(value)


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r4-coverage-saturation.py",
        "run-neuroute-r4-coverage-saturation.py",
        "run-neuroute-r4-document-representatives.py",
        "run-neuroute-r4-fine-grained-interactions.py",
        "run-neuroute-r4-teacher-selected-representatives.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any],
                                   list[str], numpy.ndarray]:
    actual = {
        "teacher_selection_result_sha256": sha256(args.teacher_result),
        "teacher_selection_evidence_sha256": sha256(args.teacher_evidence),
        "fine_interaction_result_sha256": sha256(args.fine_result),
        "fine_interaction_evidence_sha256": sha256(args.fine_evidence),
        "r4_representative_result_sha256": sha256(args.r4_result),
        "r4_representative_evidence_sha256": sha256(args.r4_evidence),
        "multilingual_query_manifest_sha256": sha256(
            args.multilingual_query_root / "manifest.json"),
        "width_materialization_sha256": sha256(
            args.width_materialization_root / "manifest.json"),
        "german_split_result_sha256": sha256(args.german_split_result),
        "de_1m_e5_manifest_sha256": sha256(args.de_1m_e5_root / "manifest.json"),
        "de_1m_input_manifest_sha256": sha256(
            args.de_1m_input_root / "manifest.json"),
    }
    require(actual == contract["activation"],
            f"R4 coverage-saturation activation bytes differ: {actual!r}")
    teacher_result = json.loads(args.teacher_result.read_text(encoding="utf-8"))
    teacher_evidence = json.loads(args.teacher_evidence.read_text(encoding="utf-8"))
    fine_result = json.loads(args.fine_result.read_text(encoding="utf-8"))
    fine_evidence = json.loads(args.fine_evidence.read_text(encoding="utf-8"))
    r4_result = json.loads(args.r4_result.read_text(encoding="utf-8"))
    r4_evidence = json.loads(args.r4_evidence.read_text(encoding="utf-8"))
    require(teacher_result.get("family")
            == "neuroute_r4_teacher_selected_representatives_result"
            and teacher_evidence.get("passed") is True
            and teacher_evidence.get("result_byte_replay_passed") is True,
            "R4 coverage-saturation teacher parent differs")
    require(fine_result.get("family")
            == "neuroute_r4_fine_grained_interactions_result"
            and fine_evidence.get("passed") is True
            and fine_evidence.get("result_byte_replay_passed") is True,
            "R4 coverage-saturation interaction parent differs")
    require(r4_result.get("family")
            == "neuroute_r4_document_representatives_result"
            and r4_evidence.get("passed") is True
            and r4_evidence.get("result_byte_replay_passed") is True,
            "R4 coverage-saturation representative parent differs")
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    split = json.loads(args.german_split_result.read_text(
        encoding="utf-8"))["split"]
    _, external_ids, external_vectors = base.old_nonlinear.validate_query_bundle(
        args.multilingual_query_root,
        actual["multilingual_query_manifest_sha256"])
    require(len(split["training_query_ids"]) == 153
            and len(split["configuration_selection_query_ids"]) == 76
            and len(split["internal_evaluation_query_ids"]) == 76
            and len(external_ids) == 7988,
            "R4 coverage-saturation query partitions differ")
    return (fine_result, r4_result, materialization, split,
            external_ids, external_vectors)


def artifact(path: Path, role: str, value: numpy.ndarray) -> dict[str, Any]:
    numpy.save(path, value, allow_pickle=False)
    return {
        "role": role,
        "path": path.name,
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "dtype": str(value.dtype),
        "shape": [int(current) for current in value.shape],
    }


def storage_rows(effective: numpy.ndarray, contract: dict[str, Any]
                 ) -> list[dict[str, Any]]:
    result = []
    footprints = contract["physical_footprints_bytes_per_representative"]
    for prefix in contract["representatives"]["prefixes"]:
        count = int(numpy.minimum(effective, prefix).sum(dtype=numpy.int64))
        result.append({
            "k": prefix,
            "effective_representative_count": count,
            "bytes": {name: count * int(value)
                      for name, value in footprints.items()},
        })
    return result


def materialize(contract: dict[str, Any], parent: dict[str, Any],
                materialization: dict[str, Any], data: dict[str, Any],
                args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    rows = []
    maximum = int(contract["representatives"]["maximum"])
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        root = args.materialization_root / f"seed-{seed}"
        occupied_path = root / "occupied-addresses.npy"
        positions_path = root / "actual-document-positions-k64.npy"
        effective_path = root / "actual-document-effective-count-k64.npy"
        if (occupied_path.is_file() and positions_path.is_file()
                and effective_path.is_file()):
            occupied = numpy.load(occupied_path)
            positions = numpy.load(positions_path)
            effective = numpy.load(effective_path)
        else:
            occupied, positions, effective = r4.build_actual_representatives(
                data["documents"], addresses, index, maximum)
        parent_state = fine.representative_state(args, parent, seed)
        require(numpy.array_equal(occupied, parent_state["occupied"])
                and numpy.array_equal(positions[:32], parent_state["positions"]),
                f"R4 coverage-saturation K32 parent prefix differs: {seed}")
        audit = r4.audit_representatives(
            addresses, occupied, positions, effective, index["counts"])
        audit.pop("effective_count_matches_min_posting_count_32")
        audit["effective_count_matches_min_posting_count_64"] = True
        audit["k8_k16_k32_parent_prefix_byte_replay_passed"] = True
        root.mkdir(parents=True, exist_ok=True)
        artifacts = [
            artifact(root / "occupied-addresses.npy", "occupied_addresses",
                     occupied),
            artifact(root / "actual-document-positions-k64.npy",
                     "actual_document_positions_k64", positions),
            artifact(root / "actual-document-effective-count-k64.npy",
                     "actual_document_effective_count_k64", effective),
        ]
        rows.append({
            "seed": seed,
            "document_addresses_sha256": route["document_addresses"]["sha256"],
            "occupied_address_count": len(occupied),
            "posting_count": int(index["counts"].sum(dtype=numpy.int64)),
            "audit": audit,
            "storage": storage_rows(effective, contract),
            "artifacts": artifacts,
        })
        del addresses, index, occupied, positions, effective, parent_state
        gc.collect()
    return rows


def materialization_artifact(args: argparse.Namespace,
                             rows: list[dict[str, Any]], seed: int,
                             role: str) -> Path:
    row = next(value for value in rows if value["seed"] == seed)
    descriptor = next(value for value in row["artifacts"] if value["role"] == role)
    path = args.materialization_root / f"seed-{seed}" / descriptor["path"]
    require(path.is_file() and sha256(path) == descriptor["sha256"]
            and path.stat().st_size == descriptor["bytes"],
            f"R4 coverage-saturation artifact differs: {seed}/{role}")
    return path


def representative_state(args: argparse.Namespace,
                         rows: list[dict[str, Any]], seed: int
                         ) -> dict[str, numpy.ndarray]:
    return {
        "occupied": numpy.load(materialization_artifact(
            args, rows, seed, "occupied_addresses"), mmap_mode="r"),
        "positions": numpy.load(materialization_artifact(
            args, rows, seed, "actual_document_positions_k64"), mmap_mode="r"),
        "effective": numpy.load(materialization_artifact(
            args, rows, seed, "actual_document_effective_count_k64"),
            mmap_mode="r"),
    }


def interaction_identity(contract: dict[str, Any], seed: int,
                         queries: numpy.ndarray, shortlists: numpy.ndarray,
                         state: dict[str, numpy.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "neuroute_r4_coverage_saturation_cache_identity",
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
        "seed": seed,
        "query_vectors_sha256": array_sha256(queries),
        "shortlists_sha256": array_sha256(shortlists),
        "occupied_sha256": array_sha256(state["occupied"]),
        "positions_sha256": array_sha256(state["positions"]),
        "effective_sha256": array_sha256(state["effective"]),
        "prefixes": contract["representatives"]["prefixes"],
    }


def maximum_interactions(queries: numpy.ndarray, shortlists: numpy.ndarray,
                         documents: numpy.ndarray,
                         state: dict[str, numpy.ndarray], prefixes: list[int],
                         batch_queries: int, output: Path | None = None
                         ) -> numpy.ndarray:
    shape = (len(queries), shortlists.shape[1], len(prefixes))
    if output is None:
        result: numpy.ndarray = numpy.empty(shape, dtype=numpy.float32)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        result = numpy.lib.format.open_memmap(
            output, mode="w+", dtype=numpy.float32, shape=shape)
    lookup = fine.address_lookup(state["occupied"])
    positions_by_slot = numpy.asarray(state["positions"], dtype=numpy.int32)
    for start in range(0, len(queries), batch_queries):
        stop = min(len(queries), start + batch_queries)
        for query_index in range(start, stop):
            rows = lookup[numpy.asarray(shortlists[query_index], dtype=numpy.uint32)]
            require(numpy.all(rows >= 0),
                    "R4 coverage-saturation shortlist is unoccupied")
            positions = positions_by_slot[:, rows].T
            valid = positions >= 0
            safe = numpy.where(valid, positions, 0)
            vectors = numpy.asarray(documents[safe], dtype=numpy.float32)
            scores = numpy.einsum(
                "kpd,d->kp", vectors,
                numpy.asarray(queries[query_index], dtype=numpy.float32),
                dtype=numpy.float32, optimize=True)
            scores[~valid] = -numpy.inf
            cumulative = numpy.maximum.accumulate(scores, axis=1)
            for column, prefix in enumerate(prefixes):
                value = cumulative[:, prefix - 1]
                result[query_index, :, column] = numpy.where(
                    numpy.isfinite(value), value, -1.0)
            del vectors, scores, cumulative, positions, valid, safe
    if isinstance(result, numpy.memmap):
        result.flush()
    return result


def cached_interactions(root: Path, identity: dict[str, Any],
                        queries: numpy.ndarray, shortlists: numpy.ndarray,
                        documents: numpy.ndarray, state: dict[str, numpy.ndarray],
                        prefixes: list[int], batch_queries: int
                        ) -> tuple[numpy.ndarray, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    output_path = root / "maximum-interactions.npy"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity and output_path.is_file()
                and sha256(output_path) == manifest["output"]["sha256"],
                "R4 coverage-saturation interaction cache differs")
        return numpy.load(output_path, mmap_mode="r"), manifest
    value = maximum_interactions(
        queries, shortlists, documents, state, prefixes, batch_queries,
        output_path)
    value.flush()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_r4_coverage_saturation_interaction_cache",
        "identity": identity,
        "output": {
            "path": output_path.name,
            "sha256": sha256(output_path),
            "bytes": output_path.stat().st_size,
            "shape": [int(current) for current in value.shape],
            "dtype": str(value.dtype),
        },
    }
    manifest_path.write_bytes(canonical(manifest))
    return numpy.load(output_path, mmap_mode="r"), manifest


class DummyInteractionView:
    def __init__(self, maximums: numpy.ndarray):
        self.maximums = maximums

    def __getitem__(self, key: Any) -> numpy.ndarray:
        shape = numpy.asarray(self.maximums[key]).shape[:-1]
        return numpy.zeros((*shape, 3, 8), dtype=numpy.float32)


class MaximumAggregateView:
    def __init__(self, maximums: numpy.ndarray, column: int):
        self.maximums = maximums
        self.column = column

    def __getitem__(self, key: Any) -> numpy.ndarray:
        value = numpy.asarray(self.maximums[key][..., self.column],
                              dtype=numpy.float32)
        result = numpy.zeros((*value.shape, 3), dtype=numpy.float32)
        result[..., 0] = value
        return result


def maximum_normalizers(maximums: numpy.ndarray, column: int
                        ) -> dict[str, numpy.ndarray]:
    values = numpy.asarray(maximums[..., column], dtype=numpy.float32)
    mean = numpy.float32(values.mean(dtype=numpy.float64))
    deviation = numpy.float32(values.std(dtype=numpy.float64))
    if deviation < 1.0e-6:
        deviation = numpy.float32(1.0)
    aggregate_mean = numpy.zeros(3, dtype=numpy.float32)
    aggregate_deviation = numpy.ones(3, dtype=numpy.float32)
    aggregate_mean[0] = mean
    aggregate_deviation[0] = deviation
    return {
        "r4_interaction_mean": numpy.zeros((3, 8), dtype=numpy.float32),
        "r4_interaction_deviation": numpy.ones((3, 8), dtype=numpy.float32),
        "r4_aggregate_mean": aggregate_mean,
        "r4_aggregate_deviation": aggregate_deviation,
    }


def train_all(contract: dict[str, Any], materializations: list[dict[str, Any]],
              split: dict[str, Any], external_ids: list[str],
              external_vectors: numpy.ndarray, data: dict[str, Any],
              args: argparse.Namespace) -> list[dict[str, Any]]:
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = [by_id[value] for value in split["training_query_ids"]]
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][training_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    require(pool_vectors.shape == (8141, 384),
            "R4 coverage-saturation training pool differs")
    parent_contract = fine.planner.load_contract(
        THIS / "neuroute-r4-fine-grained-interactions.example.json")
    prefixes = contract["representatives"]["prefixes"]
    models = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        state = representative_state(args, materializations, seed)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        scalar_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        identity = interaction_identity(
            contract, seed, pool_vectors, shortlists, state)
        maximums, interaction_manifest = cached_interactions(
            args.interaction_cache_root / f"seed-{seed}", identity,
            pool_vectors, shortlists, data["documents"], state, prefixes,
            int(contract["training"]["interaction_batch_queries"]))
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        for column, prefix in enumerate(prefixes):
            if prefix == 32:
                continue
            treatment = f"actual_k{prefix}_max"
            model_seed = seed ^ 0x13579BD ^ 8141
            path = args.model_root / f"model-{treatment}-{seed}.npz"
            if path.is_file():
                arrays, saved_mean, saved_deviation, metadata = base.read_model(path)
                require(metadata.get("family")
                        == "neuroute_r4_coverage_saturation_model"
                        and metadata.get("seed") == seed
                        and metadata.get("treatment") == treatment
                        and metadata.get("contract_sha256") == sha256(args.contract)
                        and metadata.get("interaction_cache_sha256")
                        == interaction_manifest["output"]["sha256"]
                        and numpy.array_equal(saved_mean, scalar_mean)
                        and numpy.array_equal(saved_deviation, scalar_deviation),
                        "R4 coverage-saturation resumable model differs")
            else:
                arrays, training = fine.train_model(
                    contract["model"]["parent_architecture"], pool_vectors,
                    scalar_features, targets, DummyInteractionView(maximums),
                    MaximumAggregateView(maximums, column), scalar_mean,
                    scalar_deviation, maximum_normalizers(maximums, column),
                    model_seed, parent_contract)
                training["representative_prefix_k"] = prefix
                training["maximum_interaction_only"] = True
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_r4_coverage_saturation_model",
                    "seed": seed,
                    "treatment": treatment,
                    "representative_prefix_k": prefix,
                    "model_seed": model_seed,
                    "architecture": contract["model"]["parent_architecture"],
                    "training_query_count": len(pool_vectors),
                    "training_query_ids_sha256": scale.hash_ids(
                        numpy.asarray(pool_ids, dtype=object)),
                    "contract_sha256": sha256(args.contract),
                    "cache_manifest_sha256": contract["cache_manifest_sha256"][
                        str(seed)],
                    "interaction_cache_sha256": interaction_manifest["output"][
                        "sha256"],
                    "training": training,
                }
                fine.save_model(
                    path, arrays, scalar_mean, scalar_deviation, metadata)
            models.append({
                "seed": seed, "treatment": treatment, "k": prefix,
                "file": path.name, "sha256": sha256(path),
                "metadata": metadata,
            })
        del state, shortlists, scalar_features, targets, maximums
        gc.collect()
    return models


def representative_work(shortlists: numpy.ndarray, state: dict[str, numpy.ndarray],
                        prefix: int) -> numpy.ndarray:
    lookup = fine.address_lookup(state["occupied"])
    rows = lookup[numpy.asarray(shortlists, dtype=numpy.uint32)]
    require(numpy.all(rows >= 0),
            "R4 coverage-saturation work shortlist is unoccupied")
    counts = numpy.minimum(
        numpy.asarray(state["effective"][rows], dtype=numpy.int64), prefix)
    return counts.sum(axis=1, dtype=numpy.int64)


def treatment_rows(treatment: str, orders: list[numpy.ndarray],
                   shortlists: numpy.ndarray, addresses: numpy.ndarray,
                   index: dict[str, Any], data: dict[str, Any],
                   positions: list[int], oracle: dict[int, numpy.ndarray],
                   discounts: numpy.ndarray, contract: dict[str, Any],
                   work: numpy.ndarray | None = None) -> dict[str, Any]:
    gains_by_query = [
        sequential.target_gains(oracle[position], addresses, discounts)
        for position in positions]
    queries = []
    for local, position in enumerate(positions):
        gains = gains_by_query[local]
        budgets = [frontier.budget_row(
            orders[local], fraction, index["counts"], index, data, position,
            oracle[position], discounts, gains, contract["cascade"])
            for fraction in contract["evaluation"]["candidate_fraction_budgets"]]
        row = {
            "query_id": str(data["query_ids"][position]),
            "shortlist_target_address_recall": (
                len(set(shortlists[local].tolist()) & set(gains))
                / max(len(gains), 1)),
            "addresses_scored": shortlists.shape[1],
            "budgets": budgets,
        }
        if work is not None:
            row["representative_dot_products"] = int(work[local])
        queries.append(row)
    result = {
        "treatment": treatment,
        "query_count": len(queries),
        "frontier": frontier.aggregate(
            queries, contract["evaluation"]["candidate_fraction_budgets"]),
        "queries": queries,
        "addresses_scored_per_query": shortlists.shape[1],
    }
    if work is not None:
        result["representative_work"] = {
            "mean_dot_products_per_query": float(numpy.mean(
                work, dtype=numpy.float64)),
            "p50_dot_products_per_query": float(numpy.percentile(work, 50)),
            "p95_dot_products_per_query": float(numpy.percentile(work, 95)),
            "maximum_dot_products_per_query": int(numpy.max(work)),
        }
    return result


def evaluate_partition(name: str, contract: dict[str, Any],
                       materialization: dict[str, Any],
                       materializations: list[dict[str, Any]],
                       data: dict[str, Any], positions: list[int],
                       models: list[dict[str, Any]], parent: dict[str, Any],
                       args: argparse.Namespace
                       ) -> list[dict[str, Any]]:
    oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    prefixes = contract["representatives"]["prefixes"]
    rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = representative_state(args, materializations, seed)
        require(numpy.array_equal(occupied, state["occupied"]),
                "R4 coverage-saturation evaluation occupied state differs")
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        maximums = maximum_interactions(
            queries, shortlists, data["documents"], state, prefixes,
            int(contract["training"]["interaction_batch_queries"]))
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
        dummy = DummyInteractionView(maximums)[:]
        for column, prefix in enumerate(prefixes):
            treatment = f"actual_k{prefix}_max"
            works[treatment] = representative_work(shortlists, state, prefix)
            if prefix == 32:
                continue
            model = next(row for row in models
                         if row["seed"] == seed and row["k"] == prefix)
            arrays, saved_mean, saved_deviation, metadata = base.read_model(
                args.model_root / model["file"])
            require(metadata == model["metadata"]
                    and numpy.array_equal(saved_mean, scalar_mean)
                    and numpy.array_equal(saved_deviation, scalar_deviation),
                    "R4 coverage-saturation evaluation model differs")
            aggregate = MaximumAggregateView(maximums, column)[:]
            scores = fine.numpy_scores(
                contract["model"]["parent_architecture"], queries,
                scalar_features, dummy, aggregate, arrays,
                saved_mean, saved_deviation)
            orders[treatment] = [prototype.ordered(scores[row], shortlists[row])
                                 for row in range(len(shortlists))]
        treatments = ["prototype_order", *planner.treatments(contract),
                      "privileged_gain_density"]
        for treatment in treatments:
            if treatment == "actual_k32_max":
                value = json.loads(json.dumps(next(
                    row for row in parent[f"{name}_rows"]
                    if row["seed"] == seed
                    and row["treatment"] == "actual_k32_max")))
                value["partition"] = name
                value["addresses_scored_per_query"] = shortlists.shape[1]
                work = works[treatment]
                value["representative_work"] = {
                    "mean_dot_products_per_query": float(numpy.mean(
                        work, dtype=numpy.float64)),
                    "p50_dot_products_per_query": float(
                        numpy.percentile(work, 50)),
                    "p95_dot_products_per_query": float(
                        numpy.percentile(work, 95)),
                    "maximum_dot_products_per_query": int(numpy.max(work)),
                }
                for query, current_work in zip(value["queries"], work):
                    query["addresses_scored"] = shortlists.shape[1]
                    query["representative_dot_products"] = int(current_work)
                rows.append(value)
                continue
            value = treatment_rows(
                treatment, orders[treatment], shortlists, addresses, index,
                data, positions, oracle, discounts, contract,
                works.get(treatment))
            rows.append({"partition": name, "dataset": "de-1m",
                         "seed": seed, **value})
        del addresses, index, occupied, prototypes, effective, state
        del queries, shortlists, scalar_features, maximums, training_features
        del targets, orders, works, dummy
        gc.collect()
    return rows


def headline(row: dict[str, Any], fraction: float) -> dict[str, Any]:
    return fine.headline(row, fraction)


def selection(rows: list[dict[str, Any]], contract: dict[str, Any]
              ) -> dict[str, Any]:
    fraction = float(contract["evaluation"]["headline_candidate_fraction"])
    prefixes = contract["representatives"]["prefixes"]
    rule = contract["configuration_selection"]
    by_k: dict[int, list[dict[str, Any]]] = {}
    for prefix in prefixes:
        by_k[prefix] = [headline(row, fraction) for row in rows
                        if row["treatment"] == f"actual_k{prefix}_max"]
        require(len(by_k[prefix]) == len(contract["route"]["seeds"]),
                f"R4 coverage-saturation selection rows differ: {prefix}")
    ceiling = by_k[int(rule["ceiling_k"])]
    values = []
    for prefix in prefixes:
        current = by_k[prefix]
        actionable_gaps = [ceiling[index]["actionable_gain_coverage"]
                           - current[index]["actionable_gain_coverage"]
                           for index in range(len(current))]
        ndcg_gaps = [ceiling[index]["exact_ndcg_at_10"]
                     - current[index]["exact_ndcg_at_10"]
                     for index in range(len(current))]
        row = {
            "k": prefix,
            "mean_actionable_gain": float(numpy.mean([
                value["actionable_gain_coverage"] for value in current],
                dtype=numpy.float64)),
            "mean_exact_ndcg_at_10": float(numpy.mean([
                value["exact_ndcg_at_10"] for value in current],
                dtype=numpy.float64)),
            "mean_candidate_fraction": float(numpy.mean([
                value["candidate_fraction"] for value in current],
                dtype=numpy.float64)),
            "mean_actionable_gap_to_k64": float(numpy.mean(
                actionable_gaps, dtype=numpy.float64)),
            "mean_ndcg_gap_to_k64": float(numpy.mean(
                ndcg_gaps, dtype=numpy.float64)),
            "maximum_every_seed_actionable_gap_to_k64": float(max(actionable_gaps)),
            "maximum_every_seed_ndcg_gap_to_k64": float(max(ndcg_gaps)),
        }
        row["passes_saturation_rule"] = bool(
            row["mean_actionable_gap_to_k64"]
            <= rule["maximum_mean_actionable_gap"]
            and row["mean_ndcg_gap_to_k64"] <= rule["maximum_mean_ndcg_gap"]
            and row["maximum_every_seed_actionable_gap_to_k64"]
            <= rule["maximum_every_seed_actionable_gap"]
            and row["maximum_every_seed_ndcg_gap_to_k64"]
            <= rule["maximum_every_seed_ndcg_gap"])
        values.append(row)
    selected = next((row["k"] for row in values
                     if row["passes_saturation_rule"]), int(rule["fallback_k"]))
    return {
        "selection_partition": "configuration",
        "selected_k": selected,
        "rows": values,
        "rule": rule,
    }


def parent_k32_replay(rows: list[dict[str, Any]], parent: dict[str, Any],
                      partition: str, contract: dict[str, Any]
                      ) -> list[dict[str, Any]]:
    result = []
    for seed in contract["route"]["seeds"]:
        current = next(row for row in rows if row["seed"] == seed
                       and row["treatment"] == "actual_k32_max")
        frozen = next(row for row in parent[f"{partition}_rows"]
                      if row["seed"] == seed
                      and row["treatment"] == "actual_k32_max")
        budget_deltas = []
        for fraction in contract["evaluation"]["candidate_fraction_budgets"]:
            current_value = headline(current, fraction)
            frozen_value = headline(frozen, fraction)
            budget_deltas.append({
                "candidate_fraction_budget": fraction,
                "actionable_delta": current_value["actionable_gain_coverage"]
                - frozen_value["actionable_gain_coverage"],
                "ndcg_delta": current_value["exact_ndcg_at_10"]
                - frozen_value["exact_ndcg_at_10"],
                "candidate_fraction_delta": current_value["candidate_fraction"]
                - frozen_value["candidate_fraction"],
            })
        result.append({"seed": seed, "partition": partition,
                       "budget_deltas": budget_deltas})
    return result


def decision(internal: list[dict[str, Any]], configuration: dict[str, Any],
             contract: dict[str, Any]
             ) -> dict[str, Any]:
    internal_rule = selection(internal, contract)
    selected_k = int(configuration["selected_k"])
    selected_internal = next(row for row in internal_rule["rows"]
                             if row["k"] == selected_k)
    return {
        "selected_k_for_set_coverage": selected_k,
        "internal_selected_k_row": selected_internal,
        "internal_selection_rule_replayed": internal_rule,
        "internal_selected_k_still_passes_rule": selected_internal[
            "passes_saturation_rule"],
        "models_frozen_before_configuration": True,
        "internal_opened_after_configuration_selection": True,
        "compression_measured": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (fine_result, r4_result, materialization, split,
     external_ids, external_vectors) = validate_activation(contract, args)
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    configuration_positions = [by_id[value]
                               for value in split["configuration_selection_query_ids"]]
    internal_positions = [by_id[value]
                          for value in split["internal_evaluation_query_ids"]]
    materializations = materialize(
        contract, r4_result, materialization, data, args)
    models = train_all(
        contract, materializations, split, external_ids, external_vectors,
        data, args)
    configuration_rows = evaluate_partition(
        "configuration", contract, materialization, materializations, data,
        configuration_positions, models, fine_result, args)
    configuration_selection = selection(configuration_rows, contract)
    internal_rows = evaluate_partition(
        "internal", contract, materialization, materializations, data,
        internal_positions, models, fine_result, args)
    k32_replay = (parent_k32_replay(
        configuration_rows, fine_result, "configuration", contract)
        + parent_k32_replay(internal_rows, fine_result, "internal", contract))
    k32_replay_passed = all(
        abs(value[key]) <= 1.0e-12
        for row in k32_replay for value in row["budget_deltas"]
        for key in ("actionable_delta", "ndcg_delta", "candidate_fraction_delta"))
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_coverage_saturation_result",
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
        "materializations": materializations,
        "models": models,
        "frozen_k32_parent_models": [
            row for row in fine_result["models"]
            if row["variant"] == "actual_k32_max"],
        "configuration_rows": configuration_rows,
        "configuration_selection": configuration_selection,
        "internal_rows": internal_rows,
        "k32_parent_replay": k32_replay,
        "k32_parent_replay_passed": k32_replay_passed,
        "decision": decision(
            internal_rows, configuration_selection, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r4-coverage-saturation.example.json")
    documents = r4.normalized_rows(numpy.asarray([
        [1.0, 0.0], [0.8, 0.2], [0.0, 1.0], [-1.0, 0.0], [-0.8, 0.2]],
        dtype=numpy.float32))
    occupied = numpy.asarray([1, 2], dtype=numpy.uint32)
    positions = numpy.asarray([
        [0, 3], [1, 4], [2, -1], *([[-1, -1]] * 61)], dtype=numpy.int32)
    state = {"occupied": occupied, "positions": positions,
             "effective": numpy.asarray([3, 2], dtype=numpy.uint8)}
    interactions = maximum_interactions(
        numpy.asarray([[1.0, 0.0]], dtype=numpy.float32),
        numpy.asarray([[1, 2]], dtype=numpy.uint32), documents, state,
        [1, 2, 3], 1)
    require(interactions.shape == (1, 2, 3)
            and numpy.isclose(interactions[0, 0, 0], 1.0)
            and numpy.isclose(interactions[0, 1, 2], -0.9701425)
            and planner.plan(contract)["model_fits"] == 15,
            "R4 coverage-saturation runner self-test differs")
    print("NeuRoute R4 coverage-saturation runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-coverage-saturation.example.json")
    for name in [
            "teacher-result", "teacher-evidence", "fine-result", "fine-evidence",
            "r4-result", "r4-evidence", "r4-materialization-root",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "materialization-root",
            "interaction-cache-root", "model-root", "output"]:
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
            parser.error("all R4 coverage-saturation paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r4-coverage-saturation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
