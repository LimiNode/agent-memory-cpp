#!/usr/bin/env python3
"""Materialize, train, and evaluate teacher-selected R4 representatives."""

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


planner = load("neuroute_r4_teacher_selected_representatives_planner",
               "plan-neuroute-r4-teacher-selected-representatives.py")
fine = load("neuroute_r4_teacher_selected_representatives_parent",
            "run-neuroute-r4-fine-grained-interactions.py")
r4 = fine.r4
base = fine.base
prototype = fine.prototype
multi = fine.multi
scale = fine.scale
task = fine.task
ambiguity = fine.ambiguity
sequential = fine.sequential


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
        "plan-neuroute-r4-teacher-selected-representatives.py",
        "run-neuroute-r4-teacher-selected-representatives.py",
        "run-neuroute-r4-fine-grained-interactions.py",
        "run-neuroute-r4-document-representatives.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any],
                                   list[str], numpy.ndarray]:
    actual = {
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
            f"R4 teacher-selection activation bytes differ: {actual!r}")
    parent = json.loads(args.fine_result.read_text(encoding="utf-8"))
    parent_evidence = json.loads(args.fine_evidence.read_text(encoding="utf-8"))
    representative = json.loads(args.r4_result.read_text(encoding="utf-8"))
    representative_evidence = json.loads(args.r4_evidence.read_text(
        encoding="utf-8"))
    require(parent.get("family")
            == "neuroute_r4_fine_grained_interactions_result"
            and parent.get("configuration_selection", {}).get(
                "selected_variant") == contract["model"]["frozen_architecture"]
            and parent.get("decision", {}).get("r0_frozen_replay_passed") is True
            and parent_evidence.get("passed") is True
            and parent_evidence.get("result_byte_replay_passed") is True,
            "R4 teacher-selection fine-interaction parent differs")
    require(representative.get("family")
            == "neuroute_r4_document_representatives_result"
            and representative.get("decision", {}).get(
                "materialization_audit_passed") is True
            and representative_evidence.get("passed") is True,
            "R4 teacher-selection representative parent differs")
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    split = json.loads(args.german_split_result.read_text(
        encoding="utf-8"))["split"]
    external_manifest, external_ids, external_vectors = (
        base.old_nonlinear.validate_query_bundle(
            args.multilingual_query_root,
            actual["multilingual_query_manifest_sha256"]))
    require(len(split["training_query_ids"]) == 153
            and len(split["configuration_selection_query_ids"]) == 76
            and len(split["internal_evaluation_query_ids"]) == 76
            and len(external_ids) == 7988,
            "R4 teacher-selection query partitions differ")
    del external_manifest
    return (parent, representative, materialization, split,
            external_ids, external_vectors)


def parent_artifact(args: argparse.Namespace, result: dict[str, Any],
                    seed: int, role: str) -> Path:
    return fine.representative_artifact(args, result, seed, role)


def parent_state(args: argparse.Namespace, result: dict[str, Any],
                 seed: int) -> dict[str, numpy.ndarray]:
    return fine.representative_state(args, result, seed)


def select_teacher_representatives(
        documents: numpy.ndarray, addresses: numpy.ndarray,
        index: dict[str, Any], occupied: numpy.ndarray,
        baseline: numpy.ndarray, effective: numpy.ndarray,
        queries: numpy.ndarray, shortlists: numpy.ndarray,
        targets: numpy.ndarray, local_limit: int
        ) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    require(shortlists.shape == targets.shape
            and len(queries) == len(shortlists)
            and baseline.shape == (32, len(occupied)),
            "R4 teacher-selection input shape differs")
    support = numpy.zeros(len(addresses), dtype=numpy.float64)
    positive_pair_count = 0
    rank_weight = 1.0 / numpy.log2(
        numpy.arange(local_limit, dtype=numpy.float64) + 2.0)
    order = numpy.asarray(index["order"], dtype=numpy.int32)
    offsets = numpy.asarray(index["offsets"], dtype=numpy.int64)
    counts = numpy.asarray(index["counts"], dtype=numpy.int64)
    for query_index in range(len(queries)):
        target = numpy.asarray(targets[query_index], dtype=numpy.float64)
        positive = numpy.flatnonzero(target > 0.0)
        total = float(target[positive].sum(dtype=numpy.float64))
        if not len(positive) or total <= 0.0:
            continue
        query = numpy.asarray(queries[query_index], dtype=numpy.float32)
        for shortlist_position in positive:
            address = int(shortlists[query_index, shortlist_position])
            count = int(counts[address])
            require(count > 0, "R4 teacher-selection positive address is empty")
            positions = order[offsets[address]:offsets[address] + count]
            scores = numpy.asarray(
                numpy.asarray(documents[positions], dtype=numpy.float32) @ query,
                dtype=numpy.float32)
            chosen_count = min(local_limit, count)
            local_order = numpy.lexsort((positions, -scores))[:chosen_count]
            chosen = positions[local_order]
            weights = rank_weight[:chosen_count]
            weights = weights / weights.sum(dtype=numpy.float64)
            support[chosen] += (target[shortlist_position] / total) * weights
            positive_pair_count += 1

    selected = numpy.full_like(baseline, -1)
    selected_support = numpy.zeros(baseline.shape, dtype=numpy.float64)
    teacher_slots = 0
    replaced_slots = 0
    supported_addresses = 0
    for row, address_value in enumerate(occupied):
        address = int(address_value)
        count = int(counts[address])
        posting = order[offsets[address]:offsets[address] + count]
        supported = posting[support[posting] > 0.0]
        if len(supported):
            supported = supported[numpy.lexsort((supported, -support[supported]))]
            supported_addresses += 1
        values: list[int] = []
        seen: set[int] = set()
        limit = int(effective[row])
        for position in supported[:limit]:
            current = int(position)
            values.append(current)
            seen.add(current)
        teacher_count = len(values)
        for position in baseline[:limit, row]:
            if len(values) == limit:
                break
            current = int(position)
            if current not in seen:
                values.append(current)
                seen.add(current)
        require(len(values) == limit,
                f"R4 teacher-selection baseline fill differs at {address}")
        selected[:limit, row] = numpy.asarray(values, dtype=numpy.int32)
        selected_support[:limit, row] = support[selected[:limit, row]]
        teacher_slots += teacher_count
        replaced_slots += len(set(values) - set(
            int(value) for value in baseline[:limit, row]))
    audit = r4.audit_representatives(
        addresses, occupied, selected, effective, index["counts"])
    audit.update({
        "training_query_count": len(queries),
        "positive_query_address_pair_count": positive_pair_count,
        "training_supported_document_count": int(numpy.count_nonzero(support > 0.0)),
        "training_supported_address_count": supported_addresses,
        "teacher_supported_selected_slot_count": teacher_slots,
        "deterministic_fill_slot_count": int(effective.sum(dtype=numpy.int64))
        - teacher_slots,
        "selected_slots_outside_deterministic_k32": replaced_slots,
        "selection_support_sum": float(support.sum(dtype=numpy.float64)),
        "configuration_or_internal_selection_query_count": 0,
        "runtime_query_dependent_selection": False,
    })
    return selected, selected_support, audit


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


def materialize_selections(
        contract: dict[str, Any], representative: dict[str, Any],
        materialization: dict[str, Any], pool_ids: list[str],
        pool_vectors: numpy.ndarray, data: dict[str, Any],
        args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    rows = []
    args.selection_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        baseline_state = parent_state(args, representative, seed)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        selected, selected_support, audit = select_teacher_representatives(
            data["documents"], addresses, index, baseline_state["occupied"],
            baseline_state["positions"], baseline_state["effective"],
            pool_vectors, shortlists, targets,
            int(contract["teacher_selection"][
                "local_documents_per_positive_address"]))
        root = args.selection_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        artifacts = [
            artifact(root / "occupied-addresses.npy", "occupied_addresses",
                     numpy.asarray(baseline_state["occupied"])),
            artifact(root / "teacher-selected-document-positions-k32.npy",
                     "teacher_selected_document_positions_k32", selected),
            artifact(root / "teacher-selected-support-k32.npy",
                     "teacher_selected_support_k32", selected_support),
            artifact(root / "teacher-selected-effective-count.npy",
                     "teacher_selected_effective_count",
                     numpy.asarray(baseline_state["effective"])),
        ]
        rows.append({
            "seed": seed,
            "document_addresses_sha256": route["document_addresses"]["sha256"],
            "training_cache_manifest_sha256": contract["cache_manifest_sha256"][
                str(seed)],
            "training_query_ids_sha256": scale.hash_ids(
                numpy.asarray(pool_ids, dtype=object)),
            "baseline_positions_sha256": array_sha256(
                numpy.asarray(baseline_state["positions"])),
            "selection_audit": audit,
            "artifacts": artifacts,
        })
        del addresses, index, baseline_state, shortlists, targets
        del selected, selected_support
        gc.collect()
    return rows


def selection_artifact(args: argparse.Namespace, rows: list[dict[str, Any]],
                       seed: int, role: str) -> Path:
    row = next(value for value in rows if value["seed"] == seed)
    descriptor = next(value for value in row["artifacts"] if value["role"] == role)
    path = args.selection_root / f"seed-{seed}" / descriptor["path"]
    require(path.is_file() and sha256(path) == descriptor["sha256"]
            and path.stat().st_size == descriptor["bytes"],
            f"R4 teacher-selection artifact differs: {seed}/{role}")
    return path


def selection_state(args: argparse.Namespace, rows: list[dict[str, Any]],
                    seed: int) -> dict[str, numpy.ndarray]:
    return {
        "occupied": numpy.load(selection_artifact(
            args, rows, seed, "occupied_addresses"), mmap_mode="r"),
        "positions": numpy.load(selection_artifact(
            args, rows, seed, "teacher_selected_document_positions_k32"),
            mmap_mode="r"),
        "effective": numpy.load(selection_artifact(
            args, rows, seed, "teacher_selected_effective_count"), mmap_mode="r"),
    }


def interaction_identity(contract: dict[str, Any], seed: int,
                         queries: numpy.ndarray, shortlists: numpy.ndarray,
                         state: dict[str, numpy.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "neuroute_r4_teacher_selected_interaction_cache_identity",
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
        "seed": seed,
        "query_vectors_sha256": array_sha256(queries),
        "shortlists_sha256": array_sha256(shortlists),
        "occupied_sha256": array_sha256(state["occupied"]),
        "selected_positions_sha256": array_sha256(state["positions"]),
        "selected_effective_sha256": array_sha256(state["effective"]),
        "retained_sorted_scores": 8,
    }


def k32_interactions(queries: numpy.ndarray, shortlists: numpy.ndarray,
                     documents: numpy.ndarray, state: dict[str, numpy.ndarray],
                     batch_queries: int, output: Path | None = None
                     ) -> numpy.ndarray:
    shape = (len(queries), shortlists.shape[1], 8)
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
                    "R4 teacher-selection shortlist is unoccupied")
            positions = positions_by_slot[:, rows].T
            valid = positions >= 0
            safe = numpy.where(valid, positions, 0)
            vectors = numpy.asarray(documents[safe], dtype=numpy.float32)
            scores = numpy.einsum(
                "kpd,d->kp", vectors,
                numpy.asarray(queries[query_index], dtype=numpy.float32),
                dtype=numpy.float32, optimize=True)
            scores[~valid] = -numpy.inf
            ordered = numpy.sort(scores, axis=1)[:, ::-1][:, :8]
            result[query_index] = numpy.where(
                numpy.isfinite(ordered), ordered, -1.0)
            del vectors, scores, positions, valid, safe
    if isinstance(result, numpy.memmap):
        result.flush()
    return result


def cached_interactions(root: Path, identity: dict[str, Any],
                        queries: numpy.ndarray, shortlists: numpy.ndarray,
                        documents: numpy.ndarray, state: dict[str, numpy.ndarray],
                        batch_queries: int) -> tuple[numpy.ndarray, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    output_path = root / "interactions.npy"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity
                and output_path.is_file()
                and sha256(output_path) == manifest["output"]["sha256"],
                "R4 teacher-selection interaction cache differs")
        return numpy.load(output_path, mmap_mode="r"), manifest
    value = k32_interactions(
        queries, shortlists, documents, state, batch_queries, output_path)
    value.flush()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_r4_teacher_selected_interaction_cache",
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


class PrefixThreeView:
    """Expose compact K32 interactions as fine-runner prefix index two."""

    def __init__(self, values: numpy.ndarray):
        self.values = values

    def __getitem__(self, key: Any) -> numpy.ndarray:
        values = numpy.asarray(self.values[key], dtype=numpy.float32)
        result = numpy.zeros((*values.shape[:-1], 3, values.shape[-1]),
                             dtype=numpy.float32)
        result[..., 2, :] = values
        return result


def interaction_normalizers(interactions: numpy.ndarray) -> dict[str, numpy.ndarray]:
    mean, deviation = fine.normalization(interactions, (0, 1))
    interaction_mean = numpy.zeros((3, 8), dtype=numpy.float32)
    interaction_deviation = numpy.ones((3, 8), dtype=numpy.float32)
    interaction_mean[2] = mean
    interaction_deviation[2] = deviation
    return {
        "r4_interaction_mean": interaction_mean,
        "r4_interaction_deviation": interaction_deviation,
        "r4_aggregate_mean": numpy.zeros(3, dtype=numpy.float32),
        "r4_aggregate_deviation": numpy.ones(3, dtype=numpy.float32),
    }


def train_all(contract: dict[str, Any], representative: dict[str, Any],
              selections: list[dict[str, Any]], split: dict[str, Any],
              external_ids: list[str], external_vectors: numpy.ndarray,
              data: dict[str, Any], args: argparse.Namespace
              ) -> list[dict[str, Any]]:
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = [by_id[value] for value in split["training_query_ids"]]
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][training_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    require(pool_vectors.shape == (8141, 384),
            "R4 teacher-selection training pool differs")
    parent_contract = fine.planner.load_contract(
        THIS / "neuroute-r4-fine-grained-interactions.example.json")
    models = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        state = selection_state(args, selections, seed)
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
        interactions, interaction_manifest = cached_interactions(
            args.interaction_cache_root / f"seed-{seed}", identity,
            pool_vectors, shortlists, data["documents"], state,
            int(contract["training"]["interaction_batch_queries"]))
        normalizers = interaction_normalizers(interactions)
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        model_seed = seed ^ 0x13579BD ^ 8141
        path = args.model_root / f"model-teacher-selected-k32-{seed}.npz"
        if path.is_file():
            arrays, saved_mean, saved_deviation, metadata = base.read_model(path)
            require(metadata.get("family")
                    == "neuroute_r4_teacher_selected_model"
                    and metadata.get("seed") == seed
                    and metadata.get("contract_sha256") == sha256(args.contract)
                    and metadata.get("interaction_cache_sha256")
                    == interaction_manifest["output"]["sha256"]
                    and numpy.array_equal(saved_mean, scalar_mean)
                    and numpy.array_equal(saved_deviation, scalar_deviation),
                    "R4 teacher-selection resumable model differs")
        else:
            dummy_aggregate = numpy.zeros((len(pool_vectors), 1, 1),
                                          dtype=numpy.float32)
            arrays, training = fine.train_model(
                contract["model"]["frozen_architecture"], pool_vectors,
                scalar_features, targets, PrefixThreeView(interactions),
                dummy_aggregate, scalar_mean, scalar_deviation, normalizers,
                model_seed, parent_contract)
            training["teacher_trained_representative_selection"] = True
            training["configuration_or_internal_selection_queries"] = 0
            selection_row = next(row for row in selections if row["seed"] == seed)
            selected_artifact = next(row for row in selection_row["artifacts"]
                                     if row["role"]
                                     == "teacher_selected_document_positions_k32")
            metadata = {
                "schema_version": 1,
                "family": "neuroute_r4_teacher_selected_model",
                "seed": seed,
                "model_seed": model_seed,
                "architecture": contract["model"]["frozen_architecture"],
                "training_query_count": len(pool_vectors),
                "training_query_ids_sha256": scale.hash_ids(
                    numpy.asarray(pool_ids, dtype=object)),
                "contract_sha256": sha256(args.contract),
                "cache_manifest_sha256": contract["cache_manifest_sha256"][
                    str(seed)],
                "selected_positions_sha256": selected_artifact["sha256"],
                "interaction_cache_sha256": interaction_manifest["output"][
                    "sha256"],
                "training": training,
            }
            fine.save_model(
                path, arrays, scalar_mean, scalar_deviation, metadata)
        models.append({
            "seed": seed,
            "treatment": "teacher_selected_k32_learned_top8",
            "file": path.name,
            "sha256": sha256(path),
            "metadata": metadata,
        })
        del state, shortlists, scalar_features, targets, interactions
        gc.collect()
    return models


def evaluate_partition(name: str, contract: dict[str, Any],
                       materialization: dict[str, Any],
                       selections: list[dict[str, Any]], data: dict[str, Any],
                       positions: list[int], models: list[dict[str, Any]],
                       args: argparse.Namespace) -> list[dict[str, Any]]:
    oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = selection_state(args, selections, seed)
        require(numpy.array_equal(occupied, state["occupied"]),
                "R4 teacher-selection evaluation occupied state differs")
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        interactions = k32_interactions(
            queries, shortlists, data["documents"], state,
            int(contract["training"]["interaction_batch_queries"]))
        expanded = PrefixThreeView(interactions)[:]
        aggregate = numpy.zeros((len(queries), len(shortlists[0]), 3),
                                dtype=numpy.float32)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        training_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            training_features)
        model = next(row for row in models if row["seed"] == seed)
        arrays, saved_mean, saved_deviation, metadata = base.read_model(
            args.model_root / model["file"])
        require(metadata == model["metadata"]
                and numpy.array_equal(saved_mean, scalar_mean)
                and numpy.array_equal(saved_deviation, scalar_deviation),
                "R4 teacher-selection evaluation model differs")
        scores = fine.numpy_scores(
            contract["model"]["frozen_architecture"], queries,
            scalar_features, expanded, aggregate, arrays,
            saved_mean, saved_deviation)
        orders = [prototype.ordered(scores[row], shortlists[row])
                  for row in range(len(shortlists))]
        value = fine.treatment_rows(
            "teacher_selected_k32_learned_top8", orders, shortlists,
            addresses, index, data, positions, oracle, discounts, contract)
        rows.append({"partition": name, "dataset": "de-1m",
                     "seed": seed, **value})
        del addresses, index, occupied, prototypes, effective, state
        del queries, shortlists, scalar_features, interactions, expanded
        del aggregate, training_features, arrays, scores, orders
        gc.collect()
    return rows


def headline(row: dict[str, Any], fraction: float) -> dict[str, Any]:
    return fine.headline(row, fraction)


def parent_row(parent: dict[str, Any], partition: str, seed: int,
               treatment: str) -> dict[str, Any]:
    return next(row for row in parent[f"{partition}_rows"]
                if row["seed"] == seed and row["treatment"] == treatment)


def r3c_row(parent: dict[str, Any], seed: int) -> dict[str, Any]:
    return next(row for row in parent["frozen_r3c_parent_rows"]
                if row["seed"] == seed
                and row["treatment"] == "r3c_residual_shape")


def partition_comparison(rows: list[dict[str, Any]], parent: dict[str, Any],
                         partition: str, contract: dict[str, Any]
                         ) -> list[dict[str, Any]]:
    fraction = float(contract["evaluation"]["headline_candidate_fraction"])
    result = []
    for seed in contract["route"]["seeds"]:
        teacher = headline(next(row for row in rows if row["seed"] == seed), fraction)
        deterministic = headline(parent_row(
            parent, partition, seed, contract["model"]["frozen_architecture"]),
            fraction)
        privileged = headline(parent_row(
            parent, partition, seed, "privileged_gain_density"), fraction)
        result.append({
            "seed": seed,
            "teacher_selected_actionable_gain": teacher[
                "actionable_gain_coverage"],
            "deterministic_k32_actionable_gain": deterministic[
                "actionable_gain_coverage"],
            "privileged_actionable_gain": privileged["actionable_gain_coverage"],
            "teacher_selected_exact_ndcg_at_10": teacher["exact_ndcg_at_10"],
            "deterministic_k32_exact_ndcg_at_10": deterministic[
                "exact_ndcg_at_10"],
            "teacher_selected_candidate_fraction": teacher["candidate_fraction"],
            "deterministic_k32_candidate_fraction": deterministic[
                "candidate_fraction"],
        })
    return result


def decision(internal: list[dict[str, Any]], parent: dict[str, Any],
             contract: dict[str, Any]) -> dict[str, Any]:
    comparisons = partition_comparison(internal, parent, "internal", contract)
    for row in comparisons:
        gap = (row["privileged_actionable_gain"]
               - row["deterministic_k32_actionable_gain"])
        row["deterministic_to_privileged_gap_closed"] = (
            (row["teacher_selected_actionable_gain"]
             - row["deterministic_k32_actionable_gain"])
            / max(gap, 1.0e-30))
        row["actionable_delta_vs_deterministic_k32"] = (
            row["teacher_selected_actionable_gain"]
            - row["deterministic_k32_actionable_gain"])
        row["ndcg_delta_vs_deterministic_k32"] = (
            row["teacher_selected_exact_ndcg_at_10"]
            - row["deterministic_k32_exact_ndcg_at_10"])
    direct = all(
        row["teacher_selected_actionable_gain"]
        >= contract["decision"]["minimum_actionable_gain"]
        and row["teacher_selected_candidate_fraction"]
        <= contract["decision"]["maximum_candidate_fraction"]
        and row["ndcg_delta_vs_deterministic_k32"] >= 0.0
        for row in comparisons)
    progress = all(
        row["deterministic_to_privileged_gap_closed"]
        >= contract["decision"][
            "minimum_deterministic_to_privileged_gap_closed"]
        and row["ndcg_delta_vs_deterministic_k32"] >= 0.0
        for row in comparisons)
    return {
        "internal_comparisons": comparisons,
        "direct_gate_passed": direct,
        "selection_progress_gate_passed": progress,
        "teacher_selected_representatives_help": direct or progress,
        "representatives_frozen_before_configuration": True,
        "models_frozen_before_configuration": True,
        "internal_opened_after_configuration_replay": True,
        "configuration_or_internal_selection_queries": 0,
        "runtime_query_dependent_selection": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (parent, representative, materialization, split,
     external_ids, external_vectors) = validate_activation(contract, args)
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
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][training_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    selections = materialize_selections(
        contract, representative, materialization, pool_ids, pool_vectors,
        data, args)
    models = train_all(
        contract, representative, selections, split, external_ids,
        external_vectors, data, args)
    configuration = evaluate_partition(
        "configuration", contract, materialization, selections, data,
        configuration_positions, models, args)
    configuration_comparison = partition_comparison(
        configuration, parent, "configuration", contract)
    internal = evaluate_partition(
        "internal", contract, materialization, selections, data,
        internal_positions, models, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_teacher_selected_representatives_result",
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
        "configuration_rows": configuration,
        "configuration_comparison": configuration_comparison,
        "internal_rows": internal,
        "parent_controls": {
            "deterministic_k32_configuration_rows": [
                row for row in parent["configuration_rows"]
                if row["treatment"] == contract["model"]["frozen_architecture"]],
            "deterministic_k32_internal_rows": [
                row for row in parent["internal_rows"]
                if row["treatment"] == contract["model"]["frozen_architecture"]],
            "privileged_configuration_rows": [
                row for row in parent["configuration_rows"]
                if row["treatment"] == "privileged_gain_density"],
            "privileged_internal_rows": [
                row for row in parent["internal_rows"]
                if row["treatment"] == "privileged_gain_density"],
            "frozen_r3c_internal_rows": [
                r3c_row(parent, seed) for seed in contract["route"]["seeds"]],
        },
        "decision": decision(internal, parent, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    documents = r4.normalized_rows(numpy.asarray([
        [1.0, 0.0], [0.8, 0.2], [0.0, 1.0], [-1.0, 0.0], [-0.8, 0.2]],
        dtype=numpy.float32))
    addresses = numpy.asarray([1, 1, 1, 2, 2], dtype=numpy.uint32)
    index = scale.build_index(addresses, 2)
    occupied = numpy.asarray([1, 2], dtype=numpy.uint32)
    baseline = numpy.asarray([
        [0, 3], [1, 4], [2, -1], *([[-1, -1]] * 29)], dtype=numpy.int32)
    effective = numpy.asarray([3, 2], dtype=numpy.uint8)
    queries = numpy.asarray([[0.0, 1.0], [-1.0, 0.0]], dtype=numpy.float32)
    shortlists = numpy.asarray([[1, 2], [2, 1]], dtype=numpy.uint32)
    targets = numpy.asarray([[1.0, 0.0], [1.0, 0.0]], dtype=numpy.float64)
    selected, support, audit = select_teacher_representatives(
        documents, addresses, index, occupied, baseline, effective,
        queries, shortlists, targets, 2)
    interactions = k32_interactions(
        queries[:1], shortlists[:1], documents,
        {"occupied": occupied, "positions": selected,
         "effective": effective}, 1)
    require(selected[0, 0] == 2 and selected[0, 1] == 3
            and support[0, 0] > 0.0
            and audit["positive_query_address_pair_count"] == 2
            and interactions.shape == (1, 2, 8),
            "R4 teacher-selection runner self-test differs")
    print("NeuRoute R4 teacher-selected representative runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-teacher-selected-representatives.example.json")
    for name in [
            "fine-result", "fine-evidence", "r4-result", "r4-evidence",
            "r4-materialization-root", "multilingual-query-root",
            "width-materialization-root", "german-split-result",
            "de-1m-e5-root", "de-1m-input-root", "parent-cache-root",
            "selection-root", "interaction-cache-root", "model-root", "output"]:
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
            parser.error("all R4 teacher-selection paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r4-teacher-selected-representatives: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
