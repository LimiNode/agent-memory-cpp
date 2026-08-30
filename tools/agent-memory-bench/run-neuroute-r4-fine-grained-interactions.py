#!/usr/bin/env python3
"""Train and evaluate the frozen R4 actual-document interaction ladder."""

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


planner = load("neuroute_r4_fine_grained_interactions_planner",
               "plan-neuroute-r4-fine-grained-interactions.py")
r4 = load("neuroute_r4_fine_grained_interactions_parent",
          "run-neuroute-r4-document-representatives.py")
frontier = load("neuroute_r4_fine_grained_frontier",
                "run-neuroute-feasible-candidate-frontier.py")
matched = r4.parent.matched
base = matched.base
prototype = matched.prototype
multi = matched.multi
scale = matched.scale
task = matched.task
ambiguity = matched.ambiguity
sequential = base.sequential


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return r4.sha256(path)


def canonical(value: Any) -> bytes:
    return r4.canonical(value)


def array_sha256(value: numpy.ndarray) -> str:
    return r4.array_sha256(value)


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r4-fine-grained-interactions.py",
        "run-neuroute-r4-fine-grained-interactions.py",
        "run-neuroute-r4-document-representatives.py",
        "run-neuroute-feasible-candidate-frontier.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any],
                                   list[str], numpy.ndarray]:
    actual = {
        "r4_representative_result_sha256": sha256(args.r4_result),
        "r4_representative_evidence_sha256": sha256(args.r4_evidence),
        "feasible_frontier_result_sha256": sha256(args.feasible_result),
        "feasible_frontier_evidence_sha256": sha256(args.feasible_evidence),
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
            f"R4 interaction activation bytes differ: {actual!r}")
    representative = json.loads(args.r4_result.read_text(encoding="utf-8"))
    representative_evidence = json.loads(args.r4_evidence.read_text(
        encoding="utf-8"))
    feasible = json.loads(args.feasible_result.read_text(encoding="utf-8"))
    feasible_evidence = json.loads(args.feasible_evidence.read_text(
        encoding="utf-8"))
    require(representative.get("family")
            == "neuroute_r4_document_representatives_result"
            and representative.get("decision", {}).get(
                "fine_grained_interaction_ladder_licensed") is True
            and representative_evidence.get("passed") is True
            and representative_evidence.get("result_byte_replay_passed") is True,
            "R4 interaction representative parent differs")
    require(feasible.get("family") == "neuroute_feasible_candidate_frontier_result"
            and feasible_evidence.get("passed") is True,
            "R4 interaction feasible parent differs")
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
            "R4 interaction query partitions differ")
    return (representative, feasible, materialization, split,
            external_ids, external_vectors)


def representative_artifact(args: argparse.Namespace, result: dict[str, Any],
                            seed: int, role: str) -> Path:
    row = next(value for value in result["seeds"] if value["seed"] == seed)
    artifact = next(value for value in row["artifacts"] if value["role"] == role)
    path = args.r4_materialization_root / f"seed-{seed}" / artifact["path"]
    require(path.is_file() and sha256(path) == artifact["sha256"]
            and path.stat().st_size == artifact["bytes"],
            f"R4 interaction representative artifact differs: {seed}/{role}")
    return path


def representative_state(args: argparse.Namespace, result: dict[str, Any],
                         seed: int) -> dict[str, numpy.ndarray]:
    state = {
        "occupied": numpy.load(representative_artifact(
            args, result, seed, "occupied_addresses"), mmap_mode="r"),
        "positions": numpy.load(representative_artifact(
            args, result, seed, "actual_document_positions_k32"), mmap_mode="r"),
        "effective": numpy.load(representative_artifact(
            args, result, seed, "actual_document_effective_count"), mmap_mode="r"),
    }
    require(state["positions"].shape == (32, len(state["occupied"]))
            and state["effective"].shape == (len(state["occupied"]),),
            "R4 interaction representative shape differs")
    return state


def address_lookup(occupied: numpy.ndarray) -> numpy.ndarray:
    result = numpy.full(65536, -1, dtype=numpy.int32)
    result[numpy.asarray(occupied, dtype=numpy.uint32)] = numpy.arange(
        len(occupied), dtype=numpy.int32)
    return result


def interaction_identity(contract: dict[str, Any], seed: int,
                         queries: numpy.ndarray, shortlists: numpy.ndarray,
                         state: dict[str, numpy.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "neuroute_r4_actual_document_interaction_cache_identity",
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
        "seed": seed,
        "query_vectors_sha256": array_sha256(queries),
        "shortlists_sha256": array_sha256(shortlists),
        "occupied_sha256": array_sha256(state["occupied"]),
        "representative_positions_sha256": array_sha256(state["positions"]),
        "representative_effective_sha256": array_sha256(state["effective"]),
        "prefixes": [8, 16, 32],
        "retained_sorted_scores": 8,
    }


def interaction_arrays(queries: numpy.ndarray, shortlists: numpy.ndarray,
                       documents: numpy.ndarray, state: dict[str, numpy.ndarray],
                       batch_queries: int, output: Path | None = None
                       ) -> numpy.ndarray:
    shape = (len(queries), shortlists.shape[1], 3, 8)
    if output is None:
        result: numpy.ndarray = numpy.empty(shape, dtype=numpy.float32)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        result = numpy.lib.format.open_memmap(
            output, mode="w+", dtype=numpy.float32, shape=shape)
    lookup = address_lookup(state["occupied"])
    positions_by_slot = numpy.asarray(state["positions"], dtype=numpy.int32)
    for start in range(0, len(queries), batch_queries):
        stop = min(len(queries), start + batch_queries)
        for query_index in range(start, stop):
            rows = lookup[numpy.asarray(shortlists[query_index], dtype=numpy.uint32)]
            require(numpy.all(rows >= 0), "R4 interaction shortlist is unoccupied")
            positions = positions_by_slot[:, rows].T
            valid = positions >= 0
            safe = numpy.where(valid, positions, 0)
            vectors = numpy.asarray(documents[safe], dtype=numpy.float32)
            scores = numpy.einsum(
                "kpd,d->kp", vectors,
                numpy.asarray(queries[query_index], dtype=numpy.float32),
                dtype=numpy.float32, optimize=True)
            scores[~valid] = -numpy.inf
            for prefix_index, prefix in enumerate((8, 16, 32)):
                ordered = numpy.sort(scores[:, :prefix], axis=1)[:, ::-1][:, :8]
                result[query_index, :, prefix_index, :] = numpy.where(
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
                "R4 interaction cache differs")
        return numpy.load(output_path, mmap_mode="r"), manifest
    value = interaction_arrays(
        queries, shortlists, documents, state, batch_queries, output_path)
    value.flush()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_r4_actual_document_interaction_cache",
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


def normalization(values: numpy.ndarray, axes: tuple[int, ...]
                  ) -> tuple[numpy.ndarray, numpy.ndarray]:
    mean = values.mean(axis=axes, dtype=numpy.float64).astype(numpy.float32)
    deviation = values.std(axis=axes, dtype=numpy.float64).astype(numpy.float32)
    deviation[deviation < 1.0e-6] = 1.0
    return mean, deviation


def aggregate_features(interactions: numpy.ndarray,
                       shortlists: numpy.ndarray,
                       state: dict[str, numpy.ndarray], temperature: float
                       ) -> numpy.ndarray:
    scores = numpy.asarray(interactions[:, :, 2, :], dtype=numpy.float32)
    lookup = address_lookup(state["occupied"])
    rows = lookup[numpy.asarray(shortlists, dtype=numpy.uint32)]
    effective = numpy.minimum(
        numpy.asarray(state["effective"][rows], dtype=numpy.int32), 8)
    top1 = scores[:, :, 0]
    top2 = numpy.where(effective >= 2,
                       (scores[:, :, 0] + scores[:, :, 1]) * 0.5, top1)
    maximum = top1
    total = numpy.zeros_like(top1)
    for slot in range(8):
        active = effective > slot
        total += numpy.where(active,
                             numpy.exp((scores[:, :, slot] - maximum)
                                       * temperature), 0.0).astype(numpy.float32)
    logmeanexp = maximum + numpy.log(
        total / numpy.maximum(effective, 1).astype(numpy.float32)) / temperature
    return numpy.stack((top1, top2, logmeanexp), axis=2).astype(numpy.float32)


def fixed_normalizers(interactions: numpy.ndarray,
                      aggregate: numpy.ndarray) -> dict[str, numpy.ndarray]:
    interaction_mean, interaction_deviation = normalization(
        interactions, (0, 1))
    aggregate_mean, aggregate_deviation = normalization(aggregate, (0, 1))
    return {
        "r4_interaction_mean": interaction_mean,
        "r4_interaction_deviation": interaction_deviation,
        "r4_aggregate_mean": aggregate_mean,
        "r4_aggregate_deviation": aggregate_deviation,
    }


def initialized_arrays(variant: str, contract: dict[str, Any], seed: int
                       ) -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(seed)

    def weight(rows: int, columns: int) -> numpy.ndarray:
        bound = numpy.sqrt(6.0 / float(rows + columns))
        return rng.uniform(-bound, bound, size=(rows, columns)).astype(numpy.float32)

    local = int(contract["models"]["local_input_dimensions"][variant])
    hidden = int(contract["models"]["score_hidden_dimensions"][variant])
    return {
        "query_weight": weight(384, 32),
        "query_bias": numpy.zeros(32, dtype=numpy.float32),
        "local_weight": weight(local, 32),
        "local_bias": numpy.zeros(32, dtype=numpy.float32),
        "score_weight1": weight(160, hidden),
        "score_bias1": numpy.zeros(hidden, dtype=numpy.float32),
        "score_weight2": weight(hidden, 1),
        "score_bias2": numpy.zeros(1, dtype=numpy.float32),
    }


def parameter_count(arrays: dict[str, numpy.ndarray]) -> int:
    return sum(int(value.size) for value in arrays.values())


def local_numpy(variant: str, scalar: numpy.ndarray,
                interactions: numpy.ndarray, aggregate: numpy.ndarray,
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                arrays: dict[str, numpy.ndarray]) -> numpy.ndarray:
    scalar_value = numpy.asarray(
        (numpy.asarray(scalar, dtype=numpy.float32) - scalar_mean)
        / scalar_deviation, dtype=numpy.float32)
    if variant == "r0_scalar":
        return scalar_value
    if variant.endswith("learned_top8"):
        prefix = {"actual_k8_learned_top8": 0,
                  "actual_k16_learned_top8": 1,
                  "actual_k32_learned_top8": 2}[variant]
        value = ((numpy.asarray(interactions[:, :, prefix, :], dtype=numpy.float32)
                  - arrays["r4_interaction_mean"][prefix])
                 / arrays["r4_interaction_deviation"][prefix])
    else:
        column = {"actual_k32_max": 0, "actual_k32_top2_mean": 1,
                  "actual_k32_logmeanexp": 2}[variant]
        value = ((numpy.asarray(aggregate[:, :, column], dtype=numpy.float32)
                  - arrays["r4_aggregate_mean"][column])
                 / arrays["r4_aggregate_deviation"][column])
        value = value[:, :, None]
    return numpy.concatenate((scalar_value, value), axis=2).astype(numpy.float32)


def score_torch(query: Any, local_input: Any, parameters: dict[str, Any],
                score_scale: float) -> Any:
    return matched.score_torch(query, local_input, parameters, score_scale)


def train_model(variant: str, queries: numpy.ndarray,
                scalar_features: numpy.ndarray, targets: numpy.ndarray,
                interactions: numpy.ndarray, aggregate: numpy.ndarray,
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                normalizers: dict[str, numpy.ndarray], model_seed: int,
                contract: dict[str, Any]) -> tuple[
                    dict[str, numpy.ndarray], dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith(str(training["torch_version_prefix"])),
            f"R4 interaction torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(model_seed & 0x7FFFFFFF)
    initialized = initialized_arrays(variant, contract, model_seed ^ 0x6815D3A7)
    expected = planner.parameter_counts(contract)[variant]
    require(parameter_count(initialized) == expected,
            f"R4 interaction parameter count differs: {variant}")
    parameters = {name: torch.nn.Parameter(torch.from_numpy(value.copy()))
                  for name, value in initialized.items()}
    optimizer = torch.optim.AdamW(
        list(parameters.values()), lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]))
    query_tensor = torch.from_numpy(numpy.asarray(queries, dtype=numpy.float32))
    target_tensor = torch.from_numpy(numpy.asarray(targets, dtype=numpy.float32))
    supervised = numpy.asarray(targets.sum(axis=1, dtype=numpy.float64) > 0.0)
    losses = []
    for epoch in range(int(training["epochs"])):
        rng = numpy.random.default_rng(model_seed ^ ((epoch + 1) * 0x9E3779B1))
        order = rng.permutation(len(queries))
        total_loss = 0.0
        total_rows = 0
        for start in range(0, len(order), int(training["batch_queries"])):
            selected = order[start:start + int(training["batch_queries"])]
            selected = selected[supervised[selected]]
            if not len(selected):
                continue
            positions = torch.from_numpy(selected.astype(numpy.int64))
            query = query_tensor[positions]
            target = target_tensor[positions]
            target = target / target.sum(dim=1, keepdim=True)
            local = local_numpy(
                variant, numpy.asarray(scalar_features[selected], dtype=numpy.float32),
                numpy.asarray(interactions[selected], dtype=numpy.float32),
                numpy.asarray(aggregate[selected], dtype=numpy.float32),
                scalar_mean, scalar_deviation, normalizers)
            optimizer.zero_grad(set_to_none=True)
            scores = score_torch(query, torch.from_numpy(local), parameters,
                                 float(training["score_scale"]))
            loss = -(target * functional.log_softmax(scores, dim=1)).sum(
                dim=1).mean()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * len(selected)
            total_rows += len(selected)
        losses.append(total_loss / max(total_rows, 1))
    arrays = {name: value.detach().numpy().astype(numpy.float32)
              for name, value in parameters.items()}
    arrays.update(normalizers)
    return arrays, {
        "epoch_losses": losses,
        "final_loss": losses[-1],
        "parameter_count": expected,
        "supervised_query_count": int(numpy.count_nonzero(supervised)),
        "zero_target_query_count": int(numpy.count_nonzero(~supervised)),
        "torch_version": torch.__version__,
        "full_384d_actual_document_cosines": variant != "r0_scalar",
        "teacher_trained_representative_selection": False,
    }


def numpy_scores(variant: str, queries: numpy.ndarray,
                 scalar_features: numpy.ndarray, interactions: numpy.ndarray,
                 aggregate: numpy.ndarray, arrays: dict[str, numpy.ndarray],
                 scalar_mean: numpy.ndarray,
                 scalar_deviation: numpy.ndarray) -> numpy.ndarray:
    local_input = local_numpy(
        variant, scalar_features, interactions, aggregate,
        scalar_mean, scalar_deviation, arrays)
    local = numpy.tanh(local_input @ arrays["local_weight"]
                       + arrays["local_bias"])
    query_hidden = numpy.tanh(numpy.asarray(queries, dtype=numpy.float32)
                              @ arrays["query_weight"] + arrays["query_bias"])
    expanded = numpy.broadcast_to(query_hidden[:, None, :], local.shape)
    mean_context = local.mean(axis=1, keepdims=True, dtype=numpy.float32)
    maximum_context = local.max(axis=1, keepdims=True)
    joined = numpy.concatenate((
        local, expanded, local * expanded,
        numpy.broadcast_to(mean_context, local.shape),
        numpy.broadcast_to(maximum_context, local.shape)), axis=2)
    hidden = numpy.tanh(joined @ arrays["score_weight1"]
                        + arrays["score_bias1"])
    return numpy.asarray((hidden @ arrays["score_weight2"]
                          + arrays["score_bias2"])[..., 0], dtype=numpy.float64)


def save_model(path: Path, arrays: dict[str, numpy.ndarray],
               scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
               metadata: dict[str, Any]) -> str:
    return base.save_model(path, arrays, scalar_mean, scalar_deviation, metadata)


def train_all(contract: dict[str, Any], representative: dict[str, Any],
              materialization: dict[str, Any], split: dict[str, Any],
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
            "R4 interaction training pool differs")
    models = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        state = representative_state(args, representative, seed)
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
        aggregate = aggregate_features(
            interactions, shortlists, state,
            float(contract["representations"]["logmeanexp_temperature"]))
        normalizers = fixed_normalizers(interactions, aggregate)
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        for variant in contract["representations"]["variants"]:
            model_seed = seed ^ 0x13579BD ^ 8141
            path = args.model_root / f"model-{variant}-{seed}.npz"
            if path.is_file():
                arrays, saved_mean, saved_deviation, metadata = base.read_model(path)
                require(metadata.get("family") == "neuroute_r4_interaction_model"
                        and metadata.get("seed") == seed
                        and metadata.get("variant") == variant
                        and metadata.get("contract_sha256") == sha256(args.contract)
                        and metadata.get("interaction_cache_sha256")
                        == interaction_manifest["output"]["sha256"]
                        and parameter_count({name: arrays[name] for name in [
                            "query_weight", "query_bias", "local_weight", "local_bias",
                            "score_weight1", "score_bias1", "score_weight2", "score_bias2"]})
                        == planner.parameter_counts(contract)[variant]
                        and numpy.array_equal(saved_mean, scalar_mean)
                        and numpy.array_equal(saved_deviation, scalar_deviation),
                        "R4 interaction resumable model differs")
            else:
                arrays, training = train_model(
                    variant, pool_vectors, scalar_features, targets,
                    interactions, aggregate, scalar_mean, scalar_deviation,
                    normalizers, model_seed, contract)
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_r4_interaction_model",
                    "seed": seed,
                    "variant": variant,
                    "model_seed": model_seed,
                    "training_query_count": len(pool_vectors),
                    "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                        pool_ids, dtype=object)),
                    "contract_sha256": sha256(args.contract),
                    "cache_manifest_sha256": contract["cache_manifest_sha256"][
                        str(seed)],
                    "interaction_cache_sha256": interaction_manifest["output"][
                        "sha256"],
                    "training": training,
                }
                save_model(path, arrays, scalar_mean, scalar_deviation, metadata)
            models.append({
                "seed": seed, "variant": variant, "file": path.name,
                "sha256": sha256(path), "metadata": metadata,
            })
        del state, shortlists, scalar_features, targets, interactions, aggregate
        gc.collect()
    return models


def evaluation_state(contract: dict[str, Any], representative: dict[str, Any],
                     materialization: dict[str, Any], data: dict[str, Any],
                     seed: int, positions: list[int], args: argparse.Namespace
                     ) -> tuple[dict[str, Any], numpy.ndarray, numpy.ndarray,
                                numpy.ndarray, numpy.ndarray, numpy.ndarray,
                                numpy.ndarray, numpy.ndarray]:
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    route = task.route_entry(manifest_dataset, 16, seed)
    route_root = args.width_materialization_root / "de-1m" / route["id"]
    addresses = numpy.asarray(task.read_descriptor(
        route_root, route["document_addresses"]), dtype=numpy.uint32)
    index = scale.build_index(addresses, 16)
    occupied, prototypes, effective, _ = multi.build_nested_prototypes(
        data["documents"], addresses, index, 8)
    state = representative_state(args, representative, seed)
    require(numpy.array_equal(occupied, state["occupied"]),
            "R4 interaction occupied state differs")
    queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    shortlists, scalar_features = base.prepare_query_features(
        queries, occupied, prototypes, effective, index["counts"],
        len(data["document_ids"]), 1024,
        parent_contract["training"]["feature_query_batch_size"])
    interactions = interaction_arrays(
        queries, shortlists, data["documents"], state,
        int(contract["training"]["interaction_batch_queries"]))
    aggregate = aggregate_features(
        interactions, shortlists, state,
        float(contract["representations"]["logmeanexp_temperature"]))
    return (index, addresses, queries, shortlists, scalar_features,
            interactions, aggregate, prototypes)


def treatment_rows(treatment: str, orders: list[numpy.ndarray],
                   shortlists: numpy.ndarray, addresses: numpy.ndarray,
                   index: dict[str, Any], data: dict[str, Any],
                   positions: list[int], oracle: dict[int, numpy.ndarray],
                   discounts: numpy.ndarray, contract: dict[str, Any]
                   ) -> dict[str, Any]:
    queries = []
    for local, position in enumerate(positions):
        gains = sequential.target_gains(oracle[position], addresses, discounts)
        budgets = [frontier.budget_row(
            orders[local], fraction, index["counts"], index, data, position,
            oracle[position], discounts, gains, contract["cascade"])
            for fraction in contract["evaluation"]["candidate_fraction_budgets"]]
        queries.append({
            "query_id": str(data["query_ids"][position]),
            "shortlist_target_address_recall": (
                len(set(shortlists[local].tolist()) & set(gains))
                / max(len(gains), 1)),
            "budgets": budgets,
        })
    return {
        "treatment": treatment,
        "query_count": len(queries),
        "frontier": frontier.aggregate(
            queries, contract["evaluation"]["candidate_fraction_budgets"]),
        "queries": queries,
    }


def evaluate_partition(name: str, contract: dict[str, Any],
                       representative: dict[str, Any],
                       materialization: dict[str, Any], data: dict[str, Any],
                       positions: list[int], models: list[dict[str, Any]],
                       args: argparse.Namespace) -> list[dict[str, Any]]:
    oracle, _ = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    rows = []
    for seed in contract["route"]["seeds"]:
        (index, addresses, queries, shortlists, scalar_features,
         interactions, aggregate, prototypes) = evaluation_state(
             contract, representative, materialization, data, seed,
             positions, args)
        targets = prototype.density_targets(
            shortlists, oracle, positions, addresses, index["counts"], discounts)
        orders: dict[str, list[numpy.ndarray]] = {
            "prototype_order": [row.copy() for row in shortlists],
            "privileged_gain_density": [prototype.ordered(
                targets[row], shortlists[row], index["counts"])
                for row in range(len(shortlists))],
        }
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            contract["cache_manifest_sha256"][str(seed)])
        training_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            training_features)
        for variant in contract["representations"]["variants"]:
            model = next(value for value in models
                         if value["seed"] == seed and value["variant"] == variant)
            arrays, saved_mean, saved_deviation, metadata = base.read_model(
                args.model_root / model["file"])
            require(metadata == model["metadata"]
                    and numpy.array_equal(saved_mean, scalar_mean)
                    and numpy.array_equal(saved_deviation, scalar_deviation),
                    "R4 interaction evaluation model differs")
            scores = numpy_scores(
                variant, queries, scalar_features, interactions, aggregate,
                arrays, saved_mean, saved_deviation)
            orders[variant] = [prototype.ordered(scores[row], shortlists[row])
                               for row in range(len(shortlists))]
        for treatment in ["prototype_order",
                          *contract["representations"]["variants"],
                          "privileged_gain_density"]:
            value = treatment_rows(
                treatment, orders[treatment], shortlists, addresses, index,
                data, positions, oracle, discounts, contract)
            rows.append({"partition": name, "dataset": "de-1m",
                         "seed": seed, **value})
        del index, addresses, queries, shortlists, scalar_features
        del interactions, aggregate, prototypes, targets, orders, training_features
        gc.collect()
    return rows


def headline(row: dict[str, Any], fraction: float) -> dict[str, Any]:
    return next(value["last_feasible"] for value in row["frontier"]
                if value["candidate_fraction_budget"] == fraction)


def select_configuration(rows: list[dict[str, Any]], contract: dict[str, Any]
                         ) -> dict[str, Any]:
    fraction = float(contract["evaluation"]["headline_candidate_fraction"])
    variants = contract["representations"]["variants"]
    aggregate = []
    for variant in variants:
        current = [headline(row, fraction) for row in rows
                   if row["treatment"] == variant]
        aggregate.append({
            "variant": variant,
            "seed_count": len(current),
            "actionable_gain_coverage": float(numpy.mean([
                row["actionable_gain_coverage"] for row in current],
                dtype=numpy.float64)),
            "exact_ndcg_at_10": float(numpy.mean([
                row["exact_ndcg_at_10"] for row in current],
                dtype=numpy.float64)),
            "candidate_fraction": float(numpy.mean([
                row["candidate_fraction"] for row in current],
                dtype=numpy.float64)),
        })
    selected = min(aggregate, key=lambda row: (
        -row["actionable_gain_coverage"], -row["exact_ndcg_at_10"],
        row["candidate_fraction"], variants.index(row["variant"])))
    return {"selection_partition": "configuration", "rows": aggregate,
            "selected_variant": selected["variant"]}


def parent_row(feasible: dict[str, Any], seed: int, treatment: str
               ) -> dict[str, Any]:
    return next(row for row in feasible["rows"]
                if row["seed"] == seed and row["treatment"] == treatment)


def decision(internal: list[dict[str, Any]], feasible: dict[str, Any],
             selection: dict[str, Any], contract: dict[str, Any]
             ) -> dict[str, Any]:
    fraction = float(contract["evaluation"]["headline_candidate_fraction"])
    comparisons = []
    success = {}
    r0_replay = []
    for seed in contract["route"]["seeds"]:
        current_r0 = headline(next(row for row in internal
                                   if row["seed"] == seed
                                   and row["treatment"] == "r0_scalar"), fraction)
        frozen_r0 = headline(parent_row(feasible, seed, "r0_scalar"), fraction)
        r0_replay.append({
            "seed": seed,
            "actionable_delta": current_r0["actionable_gain_coverage"]
            - frozen_r0["actionable_gain_coverage"],
            "ndcg_delta": current_r0["exact_ndcg_at_10"]
            - frozen_r0["exact_ndcg_at_10"],
        })
    for variant in contract["representations"]["variants"]:
        rows = []
        for seed in contract["route"]["seeds"]:
            learned = headline(next(row for row in internal
                                    if row["seed"] == seed
                                    and row["treatment"] == variant), fraction)
            frozen_r3c = headline(parent_row(
                feasible, seed, "r3c_residual_shape"), fraction)
            privileged = headline(parent_row(
                feasible, seed, "privileged_gain_density"), fraction)
            gap = (privileged["actionable_gain_coverage"]
                   - frozen_r3c["actionable_gain_coverage"])
            closure = ((learned["actionable_gain_coverage"]
                        - frozen_r3c["actionable_gain_coverage"])
                       / max(gap, 1.0e-30))
            current = {
                "seed": seed, "variant": variant,
                "actionable_gain_coverage": learned["actionable_gain_coverage"],
                "exact_ndcg_at_10": learned["exact_ndcg_at_10"],
                "candidate_fraction": learned["candidate_fraction"],
                "r3c_to_privileged_gap_closed": closure,
                "ndcg_delta_vs_frozen_r3c": learned["exact_ndcg_at_10"]
                - frozen_r3c["exact_ndcg_at_10"],
            }
            rows.append(current)
            comparisons.append(current)
        direct = all(row["actionable_gain_coverage"]
                     >= contract["decision"]["minimum_actionable_gain"]
                     and row["candidate_fraction"]
                     <= contract["decision"]["maximum_candidate_fraction"]
                     for row in rows)
        progress = all(row["r3c_to_privileged_gap_closed"]
                       >= contract["decision"][
                           "minimum_r3c_to_privileged_gap_closed"]
                       and row["ndcg_delta_vs_frozen_r3c"] >= 0.0
                       for row in rows)
        success[variant] = {"direct_gate_passed": direct,
                            "progress_gate_passed": progress}
    return {
        "r0_frozen_replay": r0_replay,
        "r0_frozen_replay_passed": all(
            abs(row["actionable_delta"]) <= 1.0e-12
            and abs(row["ndcg_delta"]) <= 1.0e-12 for row in r0_replay),
        "configuration_selected_variant_for_teacher_selection": selection[
            "selected_variant"],
        "internal_comparisons": comparisons,
        "variant_success": success,
        "any_interaction_gate_passed": any(
            row["direct_gate_passed"] or row["progress_gate_passed"]
            for row in success.values()),
        "teacher_trained_representative_study_required": True,
        "teacher_trained_representative_selection_used": False,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (representative, feasible, materialization, split,
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
    models = train_all(
        contract, representative, materialization, split,
        external_ids, external_vectors, data, args)
    configuration = evaluate_partition(
        "configuration", contract, representative, materialization, data,
        configuration_positions, models, args)
    selection = select_configuration(configuration, contract)
    internal = evaluate_partition(
        "internal", contract, representative, materialization, data,
        internal_positions, models, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_fine_grained_interactions_result",
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
        "models": models,
        "configuration_rows": configuration,
        "configuration_selection": selection,
        "internal_rows": internal,
        "frozen_r3c_parent_rows": [row for row in feasible["rows"]
                                    if row["treatment"]
                                    in ("r0_scalar", "r3c_residual_shape",
                                        "privileged_gain_density")],
        "decision": decision(internal, feasible, selection, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r4-fine-grained-interactions.example.json")
    for variant, expected in planner.parameter_counts(contract).items():
        require(parameter_count(initialized_arrays(variant, contract, 11)) == expected,
                f"R4 interaction model self-test differs: {variant}")
    state = {
        "occupied": numpy.asarray([1, 2], dtype=numpy.uint32),
        "positions": numpy.asarray([
            [0, 2], [1, 3], [-1, 4], *([[-1, -1]] * 29)], dtype=numpy.int32),
        "effective": numpy.asarray([2, 3], dtype=numpy.uint8),
    }
    documents = numpy.asarray([[1.0, 0.0], [0.0, 1.0],
                               [-1.0, 0.0], [0.0, -1.0],
                               [0.6, 0.8]], dtype=numpy.float32)
    queries = numpy.asarray([[1.0, 0.0]], dtype=numpy.float32)
    shortlists = numpy.asarray([[1, 2]], dtype=numpy.uint32)
    interactions = interaction_arrays(queries, shortlists, documents, state, 1)
    aggregate = aggregate_features(interactions, shortlists, state, 16.0)
    require(interactions.shape == (1, 2, 3, 8)
            and interactions[0, 0, 0, :2].tolist() == [1.0, 0.0]
            and interactions[0, 1, 2, :3].tolist() == [0.6000000238418579, 0.0, -1.0]
            and numpy.all(numpy.isfinite(aggregate)),
            "R4 interaction array self-test differs")
    print("NeuRoute R4 fine-grained interaction runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-fine-grained-interactions.example.json")
    for name in [
            "r4-result", "r4-evidence", "r4-materialization-root",
            "feasible-result", "feasible-evidence", "multilingual-query-root",
            "width-materialization-root", "german-split-result",
            "de-1m-e5-root", "de-1m-input-root", "parent-cache-root",
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
            parser.error("all R4 interaction paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r4-fine-grained-interactions: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
