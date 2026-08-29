#!/usr/bin/env python3
"""Train nonlinear listwise rerankers inside frozen K8 address shortlists."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.util
import json
import platform
import sys
import zipfile
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


planner = load("neuroute_nonlinear_listwise_reranker_planner",
               "plan-neuroute-nonlinear-listwise-reranker.py")
parent = load("neuroute_nonlinear_listwise_reranker_parent",
              "run-neuroute-prototype-gain-density-reranker.py")
old_nonlinear = load("neuroute_nonlinear_listwise_query_bundle",
                     "run-neuroute-nonlinear-scheduler.py")
multi = parent.multi
sequential = parent.sequential
scale = parent.scale
task = parent.task

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
        "materialize-neuroute-multilingual-training-queries.py",
        "plan-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-prototype-gain-density-reranker.py",
        "run-neuroute-address-multi-prototype.py",
        "run-neuroute-nonlinear-scheduler.py",
    )
    return {name: sha256(THIS / name) for name in names}


def activation_hashes(args: argparse.Namespace) -> dict[str, str]:
    return {
        "prototype_gain_density_result_sha256": sha256(
            args.prototype_gain_density_result),
        "prototype_gain_density_evidence_sha256": sha256(
            args.prototype_gain_density_evidence),
        "multilingual_query_manifest_sha256": sha256(
            args.multilingual_query_root / "manifest.json"),
        "width_materialization_sha256": sha256(
            args.width_materialization_root / "manifest.json"),
        "german_split_result_sha256": sha256(args.german_split_result),
    }


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
        list[str], numpy.ndarray]:
    actual = activation_hashes(args)
    require(actual == contract["activation"],
            f"nonlinear listwise activation bytes differ: {actual!r}")
    parent_result = json.loads(args.prototype_gain_density_result.read_text(
        encoding="utf-8"))
    parent_evidence = json.loads(args.prototype_gain_density_evidence.read_text(
        encoding="utf-8"))
    require(parent_result.get("family")
            == "neuroute_prototype_gain_density_reranker_result"
            and parent_result.get("decision", {}).get(
                "richer_model_or_training_followup_licensed") is True
            and parent_result.get("decision", {}).get(
                "production_selection_licensed") is False,
            "nonlinear listwise parent decision differs")
    require(parent_evidence.get("family")
            == "neuroute_prototype_gain_density_reranker_evidence"
            and parent_evidence.get("passed") is True
            and parent_evidence.get("result_sha256")
            == actual["prototype_gain_density_result_sha256"]
            and parent_evidence.get("result_byte_replay_passed") is True
            and parent_evidence.get(
                "authoritative_qrels_to_quality_replay_passed") is True,
            "nonlinear listwise parent evidence differs")
    materialization = json.loads((args.width_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    split = split_result["split"]
    partition_names = ("training_query_ids", "configuration_selection_query_ids",
                       "internal_evaluation_query_ids")
    expected = (contract["query_partitions"]["base_training"]["queries"],
                contract["query_partitions"]["configuration"]["queries"],
                contract["query_partitions"]["internal_evaluation"]["queries"])
    require(all(len(split[name]) == count
                for name, count in zip(partition_names, expected))
            and all(set(split[left]).isdisjoint(split[right])
                    for index, left in enumerate(partition_names)
                    for right in partition_names[index + 1:]),
            "nonlinear listwise German partitions differ")
    query_manifest, external_ids, external_vectors = old_nonlinear.validate_query_bundle(
        args.multilingual_query_root,
        actual["multilingual_query_manifest_sha256"])
    require(len(external_ids)
            == contract["query_partitions"]["additional_training"]["queries"],
            "nonlinear listwise external query count differs")
    return (parent_result, parent_evidence, materialization, split,
            external_ids, external_vectors)


def exact_top_k_batched(documents: numpy.ndarray, document_ids: numpy.ndarray,
                        queries: numpy.ndarray, top_k: int,
                        batch_size: int) -> numpy.ndarray:
    """Build deterministic pseudo-labels without retaining a query-by-corpus matrix."""
    result = numpy.empty((len(queries), top_k), dtype=numpy.int32)
    require(batch_size > 0, "nonlinear listwise exact batch size differs")
    for start in range(0, len(queries), batch_size):
        stop = min(start + batch_size, len(queries))
        scores = numpy.asarray(queries[start:stop], dtype=numpy.float32) @ documents.T
        for local, row in enumerate(scores):
            result[start + local] = scale.select_largest(row, document_ids, top_k)
        del scores
    return result


def shortlist_feature_batches(queries: numpy.ndarray, occupied: numpy.ndarray,
                              prototypes: numpy.ndarray, effective: numpy.ndarray,
                              counts: numpy.ndarray, shortlist: int,
                              document_count: int, batch_size: int) -> Iterator[
                                  tuple[int, int, numpy.ndarray, numpy.ndarray]]:
    """Yield shortlist tensors while bounding the only [batch, occupied] allocation."""
    require(batch_size > 0, "nonlinear listwise feature batch size differs")
    for start in range(0, len(queries), batch_size):
        stop = min(start + batch_size, len(queries))
        current = numpy.asarray(queries[start:stop], dtype=numpy.float32)
        maximum = parent.maximum_scores(current, prototypes, effective)
        addresses, features = parent.query_shortlists(
            current, maximum, occupied, prototypes, effective, counts,
            shortlist, document_count)
        yield start, stop, addresses, features
        del maximum, addresses, features


def density_targets_from_top_k(shortlists: numpy.ndarray, top_positions: numpy.ndarray,
                               document_addresses: numpy.ndarray,
                               counts: numpy.ndarray,
                               discounts: numpy.ndarray) -> numpy.ndarray:
    targets = numpy.zeros(shortlists.shape, dtype=numpy.float64)
    for query_index in range(len(shortlists)):
        gains = sequential.target_gains(
            top_positions[query_index], document_addresses, discounts)
        positions = {int(address): column for column, address
                     in enumerate(shortlists[query_index].tolist())}
        for address, gain in gains.items():
            column = positions.get(int(address))
            if column is not None:
                targets[query_index, column] = gain / max(int(counts[address]), 1)
    return targets


def supervised_target_mask(targets: numpy.ndarray) -> numpy.ndarray:
    return numpy.asarray(targets.sum(axis=1, dtype=numpy.float64) > 0.0,
                         dtype=numpy.bool_)


def cache_identity(contract: dict[str, Any], seed: int, query_ids: list[str],
                   query_vectors: numpy.ndarray, route: dict[str, Any],
                   prototypes: numpy.ndarray, effective: numpy.ndarray,
                   counts: numpy.ndarray, top_positions: numpy.ndarray,
                   scale_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "family": "neuroute_nonlinear_listwise_training_cache_identity",
        "contract_sha256": sha256(Path(contract["_contract_path"])),
        "seed": seed,
        "query_ids_sha256": scale.hash_ids(numpy.asarray(query_ids, dtype=object)),
        "query_vectors_sha256": bytes_sha256(query_vectors),
        "feature_producer_sha256": sha256(Path(__file__)),
        "de_1m_e5_manifest_sha256": scale_config["e5_manifest_sha256"],
        "de_1m_input_manifest_sha256": scale_config["input_manifest_sha256"],
        "document_addresses_sha256": route["document_addresses"]["sha256"],
        "posting_counts_sha256": bytes_sha256(counts),
        "prototypes_sha256": bytes_sha256(prototypes),
        "effective_sha256": bytes_sha256(effective),
        "pseudo_teacher_top10_sha256": bytes_sha256(top_positions),
        "shortlist_size": contract["prototype_shortlist"]["address_shortlist"],
        "feature_count": 22,
        "external_pseudo_supervision": True,
        "external_qrels_used": False,
        "zero_target_policy": contract["training"]["zero_target_policy"],
    }


def open_training_cache(cache_root: Path, identity: dict[str, Any],
                        queries: numpy.ndarray, occupied: numpy.ndarray,
                        prototypes: numpy.ndarray, effective: numpy.ndarray,
                        counts: numpy.ndarray, document_count: int,
                        top_positions: numpy.ndarray, document_addresses: numpy.ndarray,
                        discounts: numpy.ndarray, batch_size: int) -> tuple[
                            numpy.ndarray, numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    identity_hash = hashlib.sha256(canonical(identity)).hexdigest()
    root = cache_root / f"seed-{identity['seed']}-{identity_hash[:16]}"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    paths = {
        "shortlists": root / "shortlists.npy",
        "features": root / "features.npy",
        "targets": root / "targets.npy",
    }
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity,
                "nonlinear listwise cache identity differs")
        require(all(path.is_file() and sha256(path) == manifest["outputs"][name]["sha256"]
                    for name, path in paths.items()),
                "nonlinear listwise cache payload differs")
        return (numpy.load(paths["shortlists"], mmap_mode="r"),
                numpy.load(paths["features"], mmap_mode="r"),
                numpy.load(paths["targets"], mmap_mode="r"), manifest)

    count = len(queries)
    shortlist = int(identity["shortlist_size"])
    shortlists = numpy.lib.format.open_memmap(
        paths["shortlists"], mode="w+", dtype=numpy.uint32,
        shape=(count, shortlist))
    features = numpy.lib.format.open_memmap(
        paths["features"], mode="w+", dtype=numpy.float32,
        shape=(count, shortlist, 22))
    targets = numpy.lib.format.open_memmap(
        paths["targets"], mode="w+", dtype=numpy.float64,
        shape=(count, shortlist))
    for start, stop, current_shortlists, current_features in shortlist_feature_batches(
            queries, occupied, prototypes, effective, counts, shortlist,
            document_count, batch_size):
        shortlists[start:stop] = current_shortlists
        features[start:stop] = current_features
        targets[start:stop] = density_targets_from_top_k(
            current_shortlists, top_positions[start:stop], document_addresses,
            counts, discounts)
    shortlists.flush()
    features.flush()
    targets.flush()
    supervised = supervised_target_mask(targets)
    manifest = {
        "schema_version": 1,
        "family": "neuroute_nonlinear_listwise_training_cache",
        "identity": identity,
        "training_query_count": count,
        "supervised_query_count": int(numpy.count_nonzero(supervised)),
        "zero_target_query_count": int(numpy.count_nonzero(~supervised)),
        "zero_target_policy": identity["zero_target_policy"],
        "outputs": {
            name: {"path": path.name, "sha256": sha256(path),
                   "bytes": path.stat().st_size}
            for name, path in paths.items()
        },
    }
    manifest_path.write_bytes(canonical(manifest))
    return (numpy.load(paths["shortlists"], mmap_mode="r"),
            numpy.load(paths["features"], mmap_mode="r"),
            numpy.load(paths["targets"], mmap_mode="r"), manifest)


def feature_normalization(features: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    flattened = features.reshape(-1, features.shape[-1])
    mean = numpy.asarray(flattened.mean(axis=0, dtype=numpy.float64), dtype=numpy.float32)
    deviation = numpy.asarray(flattened.std(axis=0, dtype=numpy.float64),
                              dtype=numpy.float32)
    deviation[deviation < 1.0e-8] = 1.0
    return mean, deviation


def fit_pairwise_models_streaming(features: numpy.ndarray, targets: numpy.ndarray,
                                  alphas: list[float], negatives_per_positive: int
                                  ) -> tuple[dict[float, numpy.ndarray],
                                             numpy.ndarray, numpy.ndarray,
                                             dict[str, Any]]:
    """Fit the ridge control without full float64 feature copies."""
    flattened = features.reshape(-1, features.shape[-1])
    mean = numpy.asarray(flattened.mean(axis=0, dtype=numpy.float64),
                         dtype=numpy.float64)
    deviation = numpy.asarray(flattened.std(axis=0, dtype=numpy.float64),
                              dtype=numpy.float64)
    deviation[deviation < 1.0e-8] = 1.0
    dimension = features.shape[-1]
    gram = numpy.zeros((dimension, dimension), dtype=numpy.float64)
    vector = numpy.zeros(dimension, dtype=numpy.float64)
    pair_count = 0
    positive_count = 0
    zero_target_queries = 0
    for query_index in range(len(features)):
        query_targets = numpy.asarray(targets[query_index], dtype=numpy.float64)
        positives = numpy.flatnonzero(query_targets > 0.0)
        negatives = numpy.flatnonzero(query_targets == 0.0)[:negatives_per_positive]
        if not positives.size:
            zero_target_queries += 1
            continue
        if not negatives.size:
            continue
        normalized = ((numpy.asarray(features[query_index], dtype=numpy.float64)
                       - mean) / deviation)
        total_density = float(query_targets[positives].sum(dtype=numpy.float64))
        for positive in positives.tolist():
            differences = normalized[positive] - normalized[negatives]
            weight = query_targets[positive] / max(total_density, 1.0e-30)
            gram += weight * (differences.T @ differences)
            vector += weight * differences.sum(axis=0)
            pair_count += len(negatives)
            positive_count += 1
    require(pair_count > 0, "nonlinear listwise ridge training pairs are empty")
    identity = numpy.eye(dimension, dtype=numpy.float64)
    models = {alpha: numpy.linalg.solve(gram + alpha * identity, vector)
              for alpha in alphas}
    metadata = {
        "feature_count": dimension,
        "positive_address_instances": positive_count,
        "pair_count": pair_count,
        "zero_target_query_count": zero_target_queries,
        "supervised_query_count": len(features) - zero_target_queries,
        "mean_sha256": bytes_sha256(mean),
        "deviation_sha256": bytes_sha256(deviation),
        "gram_sha256": bytes_sha256(gram),
        "target_vector_sha256": bytes_sha256(vector),
        "streaming_sufficient_statistics": True,
    }
    return models, mean, deviation, metadata


def initialized_arrays(variant: str, query_dimensions: int, feature_dimensions: int,
                       hidden: int, seed: int) -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(seed)

    def weight(rows: int, columns: int) -> numpy.ndarray:
        bound = numpy.sqrt(6.0 / float(rows + columns))
        return rng.uniform(-bound, bound, size=(rows, columns)).astype(numpy.float32)

    scorer_inputs = hidden * (3 if variant == "pointwise_listnet" else 5)
    return {
        "query_weight": weight(query_dimensions, hidden),
        "query_bias": numpy.zeros(hidden, dtype=numpy.float32),
        "local_weight": weight(feature_dimensions, hidden),
        "local_bias": numpy.zeros(hidden, dtype=numpy.float32),
        "score_weight1": weight(scorer_inputs, hidden),
        "score_bias1": numpy.zeros(hidden, dtype=numpy.float32),
        "score_weight2": weight(hidden, 1),
        "score_bias2": numpy.zeros(1, dtype=numpy.float32),
    }


def normalized_features(features: numpy.ndarray, mean: numpy.ndarray,
                        deviation: numpy.ndarray) -> numpy.ndarray:
    return numpy.asarray((features.astype(numpy.float32) - mean) / deviation,
                         dtype=numpy.float32)


def numpy_model_scores(variant: str, queries: numpy.ndarray, features: numpy.ndarray,
                       arrays: dict[str, numpy.ndarray], mean: numpy.ndarray,
                       deviation: numpy.ndarray) -> numpy.ndarray:
    local = numpy.tanh(normalized_features(features, mean, deviation)
                       @ arrays["local_weight"] + arrays["local_bias"])
    query = numpy.tanh(numpy.asarray(queries, dtype=numpy.float32)
                       @ arrays["query_weight"] + arrays["query_bias"])
    expanded = numpy.broadcast_to(query[:, None, :], local.shape)
    parts = [local, expanded, local * expanded]
    if variant == "context_deepsets_listnet":
        mean_context = local.mean(axis=1, keepdims=True, dtype=numpy.float32)
        maximum_context = local.max(axis=1, keepdims=True)
        parts.extend((numpy.broadcast_to(mean_context, local.shape),
                      numpy.broadcast_to(maximum_context, local.shape)))
    joined = numpy.concatenate(parts, axis=2)
    hidden = numpy.tanh(joined @ arrays["score_weight1"] + arrays["score_bias1"])
    return numpy.asarray((hidden @ arrays["score_weight2"]
                          + arrays["score_bias2"])[..., 0], dtype=numpy.float64)


def train_neural_model(variant: str, queries: numpy.ndarray, features: numpy.ndarray,
                       targets: numpy.ndarray, seed: int,
                       contract: dict[str, Any]) -> tuple[
                           dict[str, numpy.ndarray], numpy.ndarray, numpy.ndarray,
                           dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith(str(training["torch_version_prefix"])),
            f"nonlinear listwise torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(seed & 0x7FFFFFFF)
    mean, deviation = feature_normalization(features)
    local_hidden = int(training["local_hidden_dimensions"])
    query_hidden = int(training["query_hidden_dimensions"])
    context_hidden = int(training["context_hidden_dimensions"])
    require(local_hidden == query_hidden == context_hidden,
            "nonlinear listwise hidden dimensions must match")
    initialized = initialized_arrays(
        variant, queries.shape[1], features.shape[2],
        local_hidden, seed ^ 0x617A2B3C)
    parameters = {name: torch.nn.Parameter(torch.from_numpy(value.copy()))
                  for name, value in initialized.items()}
    optimizer = torch.optim.AdamW(
        list(parameters.values()), lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]))
    query_tensor = torch.from_numpy(numpy.asarray(queries, dtype=numpy.float32))
    target_tensor = torch.from_numpy(numpy.asarray(targets, dtype=numpy.float32))
    supervised_queries = supervised_target_mask(targets)
    zero_target_query_count = int(numpy.count_nonzero(~supervised_queries))
    require(zero_target_query_count < len(queries),
            "nonlinear listwise training pool has no supervised queries")
    batch_size = int(training["batch_queries"])
    losses = []
    for epoch in range(int(training["epochs"])):
        rng = numpy.random.default_rng(seed ^ ((epoch + 1) * 0x9E3779B1))
        order = rng.permutation(len(queries))
        total_loss = 0.0
        total_rows = 0
        for start in range(0, len(order), batch_size):
            selected = order[start:start + batch_size]
            positions = torch.from_numpy(selected.astype(numpy.int64))
            query = query_tensor[positions]
            local_numpy = normalized_features(features[selected], mean, deviation)
            local_input = torch.from_numpy(local_numpy)
            target = target_tensor[positions]
            target_total = target.sum(dim=1, keepdim=True)
            supervised = target_total[:, 0] > 0.0
            if not bool(torch.any(supervised)):
                continue
            query = query[supervised]
            local_input = local_input[supervised]
            target = target[supervised]
            target_total = target_total[supervised]
            target = target / target_total
            optimizer.zero_grad(set_to_none=True)
            local = torch.tanh(local_input @ parameters["local_weight"]
                               + parameters["local_bias"])
            query_hidden = torch.tanh(query @ parameters["query_weight"]
                                      + parameters["query_bias"])
            expanded = query_hidden[:, None, :].expand_as(local)
            parts = [local, expanded, local * expanded]
            if variant == "context_deepsets_listnet":
                mean_context = local.mean(dim=1, keepdim=True).expand_as(local)
                maximum_context = local.max(dim=1, keepdim=True).values.expand_as(local)
                parts.extend((mean_context, maximum_context))
            joined = torch.cat(parts, dim=2)
            hidden = torch.tanh(joined @ parameters["score_weight1"]
                                + parameters["score_bias1"])
            scores = (hidden @ parameters["score_weight2"]
                      + parameters["score_bias2"])[..., 0]
            scores = scores * float(training["score_scale"])
            loss = -(target * functional.log_softmax(scores, dim=1)).sum(dim=1).mean()
            loss.backward()
            optimizer.step()
            rows = int(supervised.sum())
            total_loss += float(loss.detach()) * rows
            total_rows += rows
        losses.append(total_loss / max(total_rows, 1))
    arrays = {name: value.detach().numpy().astype(numpy.float32)
              for name, value in parameters.items()}
    return arrays, mean, deviation, {
        "epoch_losses": losses,
        "final_loss": losses[-1],
        "torch_version": torch.__version__,
        "zero_target_query_count": zero_target_query_count,
        "supervised_query_count": len(queries) - zero_target_query_count,
        "zero_target_policy": contract["training"]["zero_target_policy"],
        "external_pseudo_supervision": True,
        "external_qrels_used": False,
    }


def save_model(path: Path, arrays: dict[str, numpy.ndarray], mean: numpy.ndarray,
               deviation: numpy.ndarray, metadata: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payloads = {
        **arrays,
        "feature_mean": numpy.asarray(mean),
        "feature_deviation": numpy.asarray(deviation),
        "metadata_json": numpy.asarray(json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, task.array_npy_bytes(payloads[name]))
    replay_arrays, replay_mean, replay_deviation, replay_metadata = read_model(path)
    require(replay_metadata == metadata
            and numpy.array_equal(replay_mean, mean)
            and numpy.array_equal(replay_deviation, deviation)
            and all(numpy.array_equal(replay_arrays[name], arrays[name])
                    for name in arrays),
            "nonlinear listwise model serialization differs")
    return sha256(path)


def read_model(path: Path) -> tuple[dict[str, numpy.ndarray], numpy.ndarray,
                                    numpy.ndarray, dict[str, Any]]:
    with numpy.load(path, allow_pickle=False) as stored:
        require("metadata_json" in stored.files
                and "feature_mean" in stored.files
                and "feature_deviation" in stored.files,
                "nonlinear listwise model members differ")
        excluded = {"metadata_json", "feature_mean", "feature_deviation"}
        arrays = {name: stored[name] for name in stored.files if name not in excluded}
        metadata = json.loads(str(stored["metadata_json"].item()))
        return (arrays, stored["feature_mean"], stored["feature_deviation"], metadata)


def select_configuration(rows: list[dict[str, Any]], variant: str,
                         headline_budget: int) -> dict[str, Any]:
    current = [row for row in rows if row["variant"] == variant]
    require(bool(current), f"nonlinear listwise calibration is empty: {variant}")
    def headline(row: dict[str, Any]) -> dict[str, Any]:
        return next(value for value in row["budgets"]
                    if value["address_budget"] == headline_budget)
    return min(current, key=lambda row: (
        -headline(row)["actionable_gain_coverage"],
        headline(row)["candidate_fraction"],
        row["training_query_count"], row.get("ridge_alpha") or 0.0))


def calibration_row(variant: str, seed: int, training_count: int,
                    shortlists: numpy.ndarray, targets: numpy.ndarray,
                    scores: numpy.ndarray, counts: numpy.ndarray,
                    document_count: int, target_totals: numpy.ndarray,
                    budget: int, **extra: Any) -> dict[str, Any]:
    gains = []
    candidates = []
    for query_index in range(len(shortlists)):
        order = parent.ordered(scores[query_index], shortlists[query_index])
        selected = order[:budget]
        positions = {int(value): column for column, value
                     in enumerate(shortlists[query_index].tolist())}
        gains.append(sum(targets[query_index, positions[int(value)]]
                         for value in selected.tolist())
                     / max(float(target_totals[query_index]), 1.0e-30))
        candidates.append(int(counts[selected].sum(dtype=numpy.int64)) / document_count)
    return {
        "seed": seed,
        "variant": variant,
        "training_query_count": training_count,
        "static_gain_density_coverage": float(numpy.mean(gains, dtype=numpy.float64)),
        "candidate_fraction": float(numpy.mean(candidates, dtype=numpy.float64)),
        **extra,
    }


def summarize_orders(treatment: str, orders: list[numpy.ndarray],
                     shortlists: numpy.ndarray, addresses: numpy.ndarray,
                     index: dict[str, Any], data: dict[str, Any],
                     positions: list[int], oracle: dict[int, numpy.ndarray],
                     discounts: numpy.ndarray,
                     contract: dict[str, Any]) -> dict[str, Any]:
    queries = []
    for local, position in enumerate(positions):
        gains = sequential.target_gains(oracle[position], addresses, discounts)
        positives = set(gains)
        order = orders[local]
        queries.append({
            "query_id": str(data["query_ids"][position]),
            "shortlist_target_address_recall": (
                len(set(shortlists[local].tolist()) & positives) / max(len(positives), 1)),
            "shortlist_average_precision": multi.centroid.average_precision(
                order, positives),
            "budgets": parent.fixed_budget_rows(
                order, gains, index, data, position, oracle[position], discounts,
                contract),
        })
    budgets = []
    for address_budget in contract["diagnostic"]["address_budgets"]:
        values = [next(row for row in query["budgets"]
                       if row["address_budget"] == address_budget)
                  for query in queries]
        budgets.append({
            "address_budget": address_budget,
            "candidate_fraction": float(numpy.mean([
                row["candidate_fraction"] for row in values], dtype=numpy.float64)),
            "static_gain_coverage": float(numpy.mean([
                row["static_gain_coverage"] for row in values], dtype=numpy.float64)),
            "actionable_gain_coverage": float(numpy.mean([
                row["actionable_gain_coverage"] for row in values], dtype=numpy.float64)),
            "exact_ndcg_at_10": float(numpy.mean([
                row["exact_ndcg_at_10"] for row in values], dtype=numpy.float64)),
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


def evaluation_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Adapt the new frozen contract to the parent cascade helper surface."""
    return {
        **contract,
        "diagnostic": {
            "address_budgets": contract["evaluation"]["address_budgets"],
            "selection_address_budget": contract["evaluation"][
                "headline_address_budget"],
            "candidate_mass_target": contract["evaluation"][
                "candidate_mass_target"],
        },
    }


def target_totals(oracle: dict[int, numpy.ndarray], positions: list[int],
                  addresses: numpy.ndarray, counts: numpy.ndarray,
                  discounts: numpy.ndarray) -> numpy.ndarray:
    return parent.global_density_totals(oracle, positions, addresses, counts, discounts)


def prepare_query_features(queries: numpy.ndarray, occupied: numpy.ndarray,
                           prototypes: numpy.ndarray, effective: numpy.ndarray,
                           counts: numpy.ndarray, document_count: int,
                           shortlist: int, batch_size: int) -> tuple[
                               numpy.ndarray, numpy.ndarray]:
    shortlists = numpy.empty((len(queries), shortlist), dtype=numpy.uint32)
    features = numpy.empty((len(queries), shortlist, 22), dtype=numpy.float32)
    for start, stop, current_shortlists, current_features in shortlist_feature_batches(
            queries, occupied, prototypes, effective, counts, shortlist,
            document_count, batch_size):
        shortlists[start:stop] = current_shortlists
        features[start:stop] = current_features
    return shortlists, features


def budget(row: dict[str, Any], value: int) -> dict[str, Any]:
    return next(item for item in row["budgets"] if item["address_budget"] == value)


def decision(internal_rows: list[dict[str, Any]], selections: list[dict[str, Any]],
             contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    comparisons = []
    success = {}
    for variant in contract["models"]["variants"]:
        variant_rows = []
        for seed in contract["route"]["seeds"]:
            prototype = next(row for row in internal_rows
                             if row["seed"] == seed
                             and row["treatment"] == "prototype_order")
            learned = next(row for row in internal_rows
                           if row["seed"] == seed and row["treatment"] == variant)
            teacher = next(row for row in internal_rows
                           if row["seed"] == seed
                           and row["treatment"] == "privileged_teacher")
            headline = int(rule["headline_address_budget"])
            prototype_budget = budget(prototype, headline)
            learned_budget = budget(learned, headline)
            teacher_budget = budget(teacher, headline)
            gap = teacher_budget["actionable_gain_coverage"] - prototype_budget[
                "actionable_gain_coverage"]
            closure = ((learned_budget["actionable_gain_coverage"]
                        - prototype_budget["actionable_gain_coverage"])
                       / max(gap, 1.0e-30))
            current = {
                "variant": variant,
                "seed": seed,
                "prototype_actionable_gain_at_256": prototype_budget[
                    "actionable_gain_coverage"],
                "learned_actionable_gain_at_256": learned_budget[
                    "actionable_gain_coverage"],
                "teacher_actionable_gain_at_256": teacher_budget[
                    "actionable_gain_coverage"],
                "teacher_gap_closure": closure,
                "prototype_candidate_fraction_at_256": prototype_budget[
                    "candidate_fraction"],
                "learned_candidate_fraction_at_256": learned_budget[
                    "candidate_fraction"],
                "candidate_fraction_ratio": learned_budget["candidate_fraction"]
                    / max(prototype_budget["candidate_fraction"], 1.0e-30),
            }
            comparisons.append(current)
            variant_rows.append(current)
        direct = all(
            row["learned_actionable_gain_at_256"] >= rule["minimum_actionable_gain"]
            and row["learned_candidate_fraction_at_256"] <= rule[
                "maximum_candidate_fraction"]
            for row in variant_rows)
        progress = all(
            row["teacher_gap_closure"] >= rule[
                "minimum_prototype_to_teacher_gap_closed"]
            and row["candidate_fraction_ratio"] <= rule[
                "maximum_candidate_fraction_ratio_vs_prototype_order"]
            for row in variant_rows)
        success[variant] = {"direct_gate_passed": direct,
                            "progress_gate_passed": progress}
    neural_names = ("pointwise_listnet", "context_deepsets_listnet")
    neural_success = any(success[name]["direct_gate_passed"] for name in neural_names)
    return {
        "configuration_selected": selections,
        "de_1m_internal_comparisons": comparisons,
        "treatment_success": success,
        "nonlinear_direct_router_sufficient": neural_success,
        "direct_and_progress_gates_are_alternatives": rule[
            "direct_and_progress_gates_are_alternatives"],
        "teacher_objective_followup_licensed": (
            not neural_success and any(success[name]["progress_gate_passed"]
                                       for name in neural_names)),
        "teacher_objective_ablation_predeclared": rule[
            "teacher_objective_ablation_predeclared"],
        "native_confirmation_licensed": False,
        "internal_evaluation_opened_after_configuration_selection": True,
        "external_training_topics_used_as_pseudo_supervision_only": True,
        "external_training_qrels_used": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], materialization: dict[str, Any],
             split: dict[str, Any], external_ids: list[str],
             external_vectors: numpy.ndarray,
             args: argparse.Namespace) -> tuple[list[dict[str, Any]],
                                                  list[dict[str, Any]],
                                                  list[dict[str, Any]],
                                                  dict[str, Any]]:
    parent_contract = parent.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")
    de_1m_config = next(row for row in parent_contract["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(de_1m_config, args.de_1m_e5_root, args.de_1m_input_root)
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
    require(len(pool_ids) == max(contract["training"]["nested_query_counts"])
            and pool_vectors.shape == (len(pool_ids), 384),
            "nonlinear listwise combined training pool differs")
    top_positions = exact_top_k_batched(
        data["documents"], data["document_ids"], pool_vectors,
        contract["teacher"]["top_k"],
        contract["training"]["exact_query_batch_size"])
    configuration_oracle, _ = scale.exact_oracle(
        data, configuration_positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    protocol = evaluation_contract(contract)
    configuration_rows: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    cache_manifests: list[dict[str, Any]] = []
    shortlist_size = int(contract["prototype_shortlist"]["address_shortlist"])

    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index,
            contract["prototype_shortlist"]["requested_prototypes_per_address"])
        identity = cache_identity(
            contract, seed, pool_ids, pool_vectors, route, prototypes, effective,
            index["counts"], top_positions, de_1m_config)
        training_shortlists, training_features, training_targets, cache_manifest = (
            open_training_cache(
                args.cache_root, identity, pool_vectors, occupied, prototypes, effective,
                index["counts"], len(data["document_ids"]), top_positions, addresses,
                discounts, contract["training"]["feature_query_batch_size"]))
        cache_manifests.append({"seed": seed, "manifest": cache_manifest})
        configuration_queries = numpy.asarray(
            data["queries"][configuration_positions], dtype=numpy.float32)
        configuration_shortlists, configuration_features = prepare_query_features(
            configuration_queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), shortlist_size,
            contract["training"]["feature_query_batch_size"])
        configuration_targets = parent.density_targets(
            configuration_shortlists, configuration_oracle,
            configuration_positions, addresses, index["counts"], discounts)
        seed_rows: list[dict[str, Any]] = []
        for training_count in contract["training"]["nested_query_counts"]:
            ridge_fits, ridge_mean, ridge_deviation, ridge_metadata = (
                fit_pairwise_models_streaming(
                    training_features[:training_count],
                    training_targets[:training_count],
                    contract["models"]["ridge_alphas"],
                    contract["models"]["ridge_hard_negatives_per_positive"]))
            ridge_candidates = []
            for alpha, weights in ridge_fits.items():
                scores = parent.learned_scores(
                    configuration_features, weights, ridge_mean, ridge_deviation)
                orders = [parent.ordered(scores[row], configuration_shortlists[row])
                          for row in range(len(configuration_shortlists))]
                summary = summarize_orders(
                    "ridge_control", orders, configuration_shortlists, addresses,
                    index, data, configuration_positions, configuration_oracle,
                    discounts, protocol)
                summary.pop("queries")
                ridge_candidates.append({"ridge_alpha": float(alpha),
                                         "weights": weights, **summary})
            headline = int(contract["evaluation"]["headline_address_budget"])
            chosen_ridge = min(ridge_candidates, key=lambda row: (
                -budget(row, headline)["actionable_gain_coverage"],
                budget(row, headline)["candidate_fraction"], row["ridge_alpha"]))
            ridge_metadata_full = {
                "schema_version": 1,
                "family": "neuroute_nonlinear_listwise_ridge_model",
                "seed": seed, "variant": "ridge_control",
                "training_query_count": training_count,
                "ridge_alpha": chosen_ridge["ridge_alpha"],
                "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                    pool_ids[:training_count], dtype=object)),
                "cache_identity_sha256": hashlib.sha256(canonical(identity)).hexdigest(),
                "contract_sha256": sha256(args.contract),
                "document_addresses_sha256": identity[
                    "document_addresses_sha256"],
                "prototypes_sha256": identity["prototypes_sha256"],
                "external_pseudo_supervision": True,
                "external_qrels_used": False,
                "training": ridge_metadata,
            }
            ridge_path = args.model_root / f"model-ridge-{training_count}-{seed}.npz"
            ridge_digest = save_model(
                ridge_path, {"weights": chosen_ridge.pop("weights")},
                ridge_mean, ridge_deviation, ridge_metadata_full)
            ridge_row = {
                "dataset": "de-1m", "seed": seed, "variant": "ridge_control",
                "training_query_count": training_count,
                "ridge_alpha": chosen_ridge["ridge_alpha"],
                "model_file": ridge_path.name, "model_sha256": ridge_digest,
                **{key: value for key, value in chosen_ridge.items()
                   if key not in {"treatment", "ridge_alpha"}},
            }
            seed_rows.append(ridge_row)
            configuration_rows.append(ridge_row)
            models.append({"seed": seed, "variant": "ridge_control",
                           "training_query_count": training_count,
                           "ridge_alpha": chosen_ridge["ridge_alpha"],
                           "file": ridge_path.name, "sha256": ridge_digest,
                           "metadata": ridge_metadata_full})

            for variant_index, variant in enumerate(
                    ("pointwise_listnet", "context_deepsets_listnet")):
                model_seed = seed ^ ((variant_index + 1) * 0x2468ACE) ^ training_count
                arrays, mean, deviation, metrics = train_neural_model(
                    variant, pool_vectors[:training_count],
                    training_features[:training_count],
                    training_targets[:training_count], model_seed, contract)
                scores = numpy_model_scores(
                    variant, configuration_queries, configuration_features,
                    arrays, mean, deviation)
                orders = [parent.ordered(scores[row], configuration_shortlists[row])
                          for row in range(len(configuration_shortlists))]
                summary = summarize_orders(
                    variant, orders, configuration_shortlists, addresses, index,
                    data, configuration_positions, configuration_oracle,
                    discounts, protocol)
                summary.pop("queries")
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_nonlinear_listwise_model",
                    "seed": seed, "variant": variant,
                    "training_query_count": training_count,
                    "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                        pool_ids[:training_count], dtype=object)),
                    "cache_identity_sha256": hashlib.sha256(canonical(identity)).hexdigest(),
                    "contract_sha256": sha256(args.contract),
                    "document_addresses_sha256": identity[
                        "document_addresses_sha256"],
                    "prototypes_sha256": identity["prototypes_sha256"],
                    "model_seed": model_seed,
                    "external_pseudo_supervision": True,
                    "external_qrels_used": False,
                    "training": metrics,
                }
                path = args.model_root / f"model-{variant}-{training_count}-{seed}.npz"
                digest = save_model(path, arrays, mean, deviation, metadata)
                row = {"dataset": "de-1m", "seed": seed, "variant": variant,
                       "training_query_count": training_count,
                       "model_file": path.name, "model_sha256": digest,
                       **{key: value for key, value in summary.items()
                          if key != "treatment"}}
                seed_rows.append(row)
                configuration_rows.append(row)
                models.append({"seed": seed, "variant": variant,
                               "training_query_count": training_count,
                               "file": path.name, "sha256": digest,
                               "metadata": metadata})
                del arrays, mean, deviation, scores
                gc.collect()
            del ridge_fits, ridge_mean, ridge_deviation, ridge_candidates
            gc.collect()
        selections.extend(select_configuration(
            seed_rows, variant, contract["evaluation"]["headline_address_budget"])
            for variant in contract["models"]["variants"])
        del training_shortlists, training_features, training_targets
        del configuration_shortlists, configuration_features, configuration_targets
        del addresses, index, occupied, prototypes, effective, members
        gc.collect()

    # Selection is now fully frozen; only now may internal query vectors be read.
    internal_oracle, _ = scale.exact_oracle(
        data, internal_positions, contract["cascade"]["oracle_k"])
    internal_queries = numpy.asarray(data["queries"][internal_positions],
                                     dtype=numpy.float32)
    internal_rows: list[dict[str, Any]] = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index,
            contract["prototype_shortlist"]["requested_prototypes_per_address"])
        internal_shortlists, internal_features = prepare_query_features(
            internal_queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), shortlist_size,
            contract["training"]["feature_query_batch_size"])
        internal_targets = parent.density_targets(
            internal_shortlists, internal_oracle, internal_positions,
            addresses, index["counts"], discounts)
        orders_by_treatment: dict[str, list[numpy.ndarray]] = {
            "prototype_order": [row.copy() for row in internal_shortlists],
            "privileged_teacher": [
                parent.ordered(internal_targets[row], internal_shortlists[row],
                               index["counts"])
                for row in range(len(internal_shortlists))],
        }
        for variant in contract["models"]["variants"]:
            selected = next(row for row in selections
                            if row["seed"] == seed and row["variant"] == variant)
            artifact = next(row for row in models
                            if row["seed"] == seed and row["variant"] == variant
                            and row["training_query_count"]
                            == selected["training_query_count"])
            arrays, mean, deviation, metadata = read_model(
                args.model_root / artifact["file"])
            require(metadata["seed"] == seed and metadata["variant"] == variant,
                    "nonlinear listwise selected model metadata differs")
            if variant == "ridge_control":
                scores = parent.learned_scores(
                    internal_features, arrays["weights"], mean, deviation)
            else:
                scores = numpy_model_scores(
                    variant, internal_queries, internal_features,
                    arrays, mean, deviation)
            orders_by_treatment[variant] = [
                parent.ordered(scores[row], internal_shortlists[row])
                for row in range(len(internal_shortlists))]
        for treatment in ["prototype_order", *contract["models"]["variants"],
                          "privileged_teacher"]:
            summary = summarize_orders(
                treatment, orders_by_treatment[treatment], internal_shortlists,
                addresses, index, data, internal_positions, internal_oracle,
                discounts, protocol)
            selected = next((row for row in selections
                             if row["seed"] == seed
                             and row["variant"] == treatment), None)
            internal_rows.append({"dataset": "de-1m", "seed": seed,
                                  "evaluated_shortlist_size": shortlist_size,
                                  "selected_configuration": selected, **summary})
        del addresses, index, occupied, prototypes, effective, members
        del internal_shortlists, internal_features, internal_targets
        gc.collect()

    cache_summary = {
        "combined_training_query_count": len(pool_ids),
        "combined_training_query_ids_sha256": scale.hash_ids(
            numpy.asarray(pool_ids, dtype=object)),
        "pseudo_teacher_top10_sha256": bytes_sha256(top_positions),
        "pseudo_teacher_corpus": "de-1m",
        "external_pseudo_supervision": True,
        "external_qrels_used": False,
        "seed_caches": [
            {"seed": row["seed"],
             "manifest_sha256": hashlib.sha256(canonical(row["manifest"])).hexdigest()}
            for row in cache_manifests],
    }
    result_decision = decision(internal_rows, selections, contract)
    del data, pool_vectors, top_positions, internal_queries
    gc.collect()
    return configuration_rows, models, internal_rows, {
        "selection": selections, "training_cache": cache_summary,
        "decision": result_decision}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    contract["_contract_path"] = str(args.contract)
    (_, _, materialization, split, external_ids,
     external_vectors) = validate_activation(contract, args)
    configuration_rows, models, internal_rows, summary = evaluate(
        contract, materialization, split, external_ids, external_vectors, args)
    contract.pop("_contract_path", None)
    result = {
        "schema_version": 1,
        "family": "neuroute_nonlinear_listwise_reranker_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "torch_version": importlib.import_module("torch").__version__,
            "torch_threads": contract["training"]["torch_threads"],
            "device": contract["training"]["device"],
        },
        "matrix": planner.plan(contract),
        "training_cache": summary["training_cache"],
        "models": models,
        "configuration_rows": configuration_rows,
        "configuration_selected": summary["selection"],
        "internal_rows": internal_rows,
        "decision": summary["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    queries = numpy.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=numpy.float32)
    prototypes = numpy.asarray([
        [[1.0, 0.0], [0.0, 1.0], [.70710677, .70710677]],
        [[0.0, 1.0], [1.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
    ], dtype=numpy.float32)
    effective = numpy.asarray([2, 2, 1], dtype=numpy.uint8)
    occupied = numpy.asarray([1, 2, 3], dtype=numpy.uint32)
    counts = numpy.asarray([0, 2, 3, 1], dtype=numpy.int64)
    batches = list(shortlist_feature_batches(
        queries, occupied, prototypes, effective, counts, 3, 6, 1))
    require(len(batches) == 2
            and all((stop - start) == 1 for start, stop, _, _ in batches)
            and all(addresses.shape == (1, 3) and features.shape == (1, 3, 22)
                    for _, _, addresses, features in batches),
            "nonlinear listwise bounded shortlist self-test differs")
    top = numpy.asarray([[0, 1]], dtype=numpy.int32)
    document_addresses = numpy.asarray([1, 2], dtype=numpy.uint32)
    targets = density_targets_from_top_k(
        batches[0][2], top, document_addresses, counts,
        numpy.asarray([1.0, 0.5], dtype=numpy.float64))
    require(targets.shape == (1, 3) and numpy.count_nonzero(targets) == 2,
            "nonlinear listwise pseudo-target self-test differs")
    mask = supervised_target_mask(numpy.vstack((targets, numpy.zeros_like(targets))))
    require(mask.tolist() == [True, False],
            "nonlinear listwise zero-target policy self-test differs")
    ridge_features = numpy.asarray([
        [[2.0, 0.0], [0.0, 1.0], [0.0, 2.0]],
        [[1.0, 0.0], [0.0, 1.0], [0.0, 2.0]],
    ], dtype=numpy.float32)
    ridge_targets = numpy.asarray([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                                  dtype=numpy.float64)
    ridge_models, _, _, ridge_metadata = fit_pairwise_models_streaming(
        ridge_features, ridge_targets, [0.1], 2)
    require(ridge_metadata["zero_target_query_count"] == 1
            and ridge_metadata["supervised_query_count"] == 1
            and ridge_metadata["pair_count"] == 2
            and 0.1 in ridge_models,
            "nonlinear listwise streaming ridge self-test differs")
    synthetic_features = numpy.zeros((2, 3, 22), dtype=numpy.float32)
    synthetic_features[0, :, 0] = [2.0, 1.0, 0.0]
    synthetic_features[1, :, 0] = [0.0, 1.0, 2.0]
    mean, deviation = feature_normalization(synthetic_features)
    for variant in ("pointwise_listnet", "context_deepsets_listnet"):
        arrays = initialized_arrays(variant, 2, 22, 4, 17)
        scores = numpy_model_scores(
            variant, queries, synthetic_features, arrays, mean, deviation)
        require(scores.shape == (2, 3) and numpy.isfinite(scores).all(),
                f"nonlinear listwise {variant} inference self-test differs")
    rows = [
        {"variant": "x", "static_gain_density_coverage": .7,
         "candidate_fraction": .2, "training_query_count": 512},
        {"variant": "x", "static_gain_density_coverage": .8,
         "candidate_fraction": .3, "training_query_count": 153},
    ]
    rows[0]["budgets"] = [{"address_budget": 256,
                            "actionable_gain_coverage": .7,
                            "candidate_fraction": .2}]
    rows[1]["budgets"] = [{"address_budget": 256,
                            "actionable_gain_coverage": .8,
                            "candidate_fraction": .3}]
    require(select_configuration(rows, "x", 256)["training_query_count"] == 153,
            "nonlinear listwise selection self-test differs")
    planner.load_contract(THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    print("NeuRoute nonlinear listwise reranker self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-nonlinear-listwise-reranker.example.json")
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all nonlinear listwise reranker paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-nonlinear-listwise-reranker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
