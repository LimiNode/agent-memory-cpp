#!/usr/bin/env python3
"""Train and evaluate nonlinear direct-address schedulers."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.util
import json
import math
import sys
import zipfile
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


planner = load("neuroute_nonlinear_scheduler_planner",
               "plan-neuroute-nonlinear-scheduler.py")
decomposition = load("neuroute_nonlinear_scheduler_parent",
                     "run-neuroute-scheduler-decomposition.py")
listwise = decomposition.listwise
task = listwise.task
scale = listwise.scale

MODEL_ARRAY_NAMES = (
    "query_w1",
    "query_b1",
    "query_w2",
    "query_b2",
    "address_embeddings",
)


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "materialize-neuroute-multilingual-training-queries.py",
        "plan-neuroute-nonlinear-scheduler.py",
        "run-neuroute-nonlinear-scheduler.py",
        "run-neuroute-scheduler-decomposition.py",
        "run-neuroute-listwise-probe-scheduler.py",
        "run-neuroute-task-aware-probe-scheduler.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_query_bundle(root: Path, expected_hash: str) -> tuple[
        dict[str, Any], list[str], numpy.ndarray]:
    manifest_path = root / "manifest.json"
    require(manifest_path.is_file() and sha256(manifest_path) == expected_hash,
            "nonlinear scheduler query manifest differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_multilingual_training_queries_v1",
            "nonlinear scheduler query family differs")
    require(manifest["materializer_source_files_sha256"] == {
        "materialize-neuroute-multilingual-training-queries.py": sha256(
            THIS / "materialize-neuroute-multilingual-training-queries.py"),
        "materialize-prepared-e5.py": sha256(THIS / "materialize-prepared-e5.py"),
    }, "nonlinear scheduler query producer bytes differ")
    outputs = manifest["outputs"]
    ids_path = root / outputs["query_ids"]["path"]
    vectors_path = root / outputs["query_vectors"]["path"]
    require(ids_path.is_file() and vectors_path.is_file()
            and sha256(ids_path) == outputs["query_ids"]["sha256"]
            and sha256(vectors_path) == outputs["query_vectors"]["sha256"]
            and outputs["query_ids"]["count"] == 7988
            and outputs["query_vectors"]["count"] == 7988
            and outputs["query_vectors"]["dimension"] == 384,
            "nonlinear scheduler query payload differs")
    ids = [json.loads(line)["id"] for line in ids_path.read_text(
        encoding="utf-8").splitlines()]
    vectors = numpy.fromfile(vectors_path, dtype="<f4").reshape(7988, 384)
    require(len(ids) == 7988 and len(set(ids)) == 7988
            and numpy.isfinite(vectors).all(),
            "nonlinear scheduler query records differ")
    return manifest, ids, vectors


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
        dict[str, Any], list[str], numpy.ndarray]:
    actual = {
        "decomposition_result_sha256": sha256(args.decomposition_result),
        "decomposition_evidence_sha256": sha256(args.decomposition_evidence),
        "multilingual_query_manifest_sha256": sha256(args.multilingual_query_root /
                                                       "manifest.json"),
    }
    require(actual == contract["activation"],
            f"nonlinear scheduler activation differs: {actual!r}")
    parent = json.loads(args.decomposition_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.decomposition_evidence.read_text(encoding="utf-8"))
    require(parent.get("family") == "neuroute_scheduler_decomposition_result"
            and evidence.get("family") == "neuroute_scheduler_decomposition_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["decomposition_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True,
            "nonlinear scheduler decomposition evidence differs")
    parent_contract = decomposition.planner.load_contract(
        THIS / "neuroute-scheduler-decomposition.example.json")
    listwise_parent, width_result, materialization, split = decomposition.validate_activation(
        parent_contract, args)
    query_manifest, external_ids, external_vectors = validate_query_bundle(
        args.multilingual_query_root, actual["multilingual_query_manifest_sha256"])
    return (listwise_parent, width_result, materialization, split,
            query_manifest, external_ids, external_vectors)


def exact_top_k(documents: numpy.ndarray, document_ids: numpy.ndarray,
                queries: numpy.ndarray, top_k: int) -> numpy.ndarray:
    result = numpy.empty((queries.shape[0], top_k), dtype=numpy.int32)
    for start in range(0, queries.shape[0], 128):
        stop = min(start + 128, queries.shape[0])
        scores = queries[start:stop].astype(numpy.float32) @ documents.T
        for local, row in enumerate(scores):
            result[start + local] = scale.select_largest(row, document_ids, top_k)
    return result


def bit_initialization(seed: int, dimensions: int) -> numpy.ndarray:
    addresses = numpy.arange(1 << 16, dtype=numpy.uint32)
    signs = task.address_signs(addresses, 16)
    rng = numpy.random.default_rng(seed ^ 0x5EEDC0DE)
    projection = rng.choice(numpy.asarray([-1.0, 1.0], dtype=numpy.float32),
                            size=(16, dimensions)).astype(numpy.float32)
    values = numpy.tanh(signs @ projection / numpy.float32(4.0)).astype(numpy.float32)
    norms = numpy.linalg.norm(values, axis=1, keepdims=True)
    return values / numpy.maximum(norms, numpy.float32(1.0e-12))


def centroid_initialization(data: dict[str, Any], addresses: numpy.ndarray,
                            index: dict[str, Any], arrays: dict[str, numpy.ndarray],
                            base: numpy.ndarray) -> numpy.ndarray:
    result = base.copy()
    occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
    centroids = numpy.empty((occupied.size, data["documents"].shape[1]), dtype=numpy.float32)
    for row, address in enumerate(occupied.tolist()):
        start, stop = int(index["offsets"][address]), int(index["offsets"][address + 1])
        centroids[row] = data["documents"][index["order"][start:stop]].mean(axis=0)
    norms = numpy.linalg.norm(centroids, axis=1, keepdims=True)
    centroids /= numpy.maximum(norms, numpy.float32(1.0e-12))
    hidden = task.infer_hidden(centroids, arrays)
    hidden /= numpy.maximum(numpy.linalg.norm(hidden, axis=1, keepdims=True),
                            numpy.float32(1.0e-12))
    result[occupied] = hidden
    return result


def sampled_candidates(top100: numpy.ndarray, addresses: numpy.ndarray,
                       occupied: numpy.ndarray, query_ids: list[str], seed: int,
                       negative_count: int) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    maximum_positive = top100.shape[1]
    candidates = numpy.zeros((top100.shape[0], maximum_positive + negative_count),
                             dtype=numpy.int64)
    targets = numpy.zeros_like(candidates, dtype=numpy.float32)
    mask = numpy.zeros_like(candidates, dtype=numpy.bool_)
    discounts = 1.0 / numpy.log2(numpy.arange(maximum_positive, dtype=numpy.float64) + 2.0)
    for query, positions in enumerate(top100):
        gains: dict[int, float] = {}
        for address, gain in zip(addresses[positions].tolist(), discounts.tolist()):
            gains[int(address)] = gains.get(int(address), 0.0) + gain
        positive = sorted(gains, key=lambda address: (-gains[address], address))
        total = sum(gains.values())
        for column, address in enumerate(positive):
            candidates[query, column] = address
            targets[query, column] = gains[address] / total
            mask[query, column] = True
        digest = hashlib.sha256(f"neuroute-negative-v1\0{seed}\0{query_ids[query]}".encode(
            "utf-8")).digest()
        rng = numpy.random.default_rng(int.from_bytes(digest[:8], "little"))
        positive_set = set(positive)
        negative = []
        for address in occupied[rng.permutation(occupied.size)].tolist():
            if int(address) not in positive_set:
                negative.append(int(address))
                if len(negative) == negative_count:
                    break
        require(len(negative) == negative_count,
                "nonlinear scheduler negative pool is incomplete")
        start = maximum_positive
        candidates[query, start:start + negative_count] = negative
        mask[query, start:start + negative_count] = True
    return candidates, targets, mask


def train_model(hidden: numpy.ndarray, candidates: numpy.ndarray, targets: numpy.ndarray,
                mask: numpy.ndarray, initial_addresses: numpy.ndarray, seed: int,
                contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith("2.8.0"),
            f"nonlinear scheduler torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(seed & 0x7FFFFFFF)
    rng = numpy.random.default_rng(seed ^ 0x13579BDF)
    w1 = numpy.zeros((64, training["query_hidden_dimensions"]), dtype=numpy.float32)
    w1[:, :64] = numpy.eye(64, dtype=numpy.float32)
    w1[:, 64:] = rng.normal(0.0, 0.02, size=w1[:, 64:].shape).astype(numpy.float32)
    w2 = numpy.zeros((training["query_hidden_dimensions"],
                      training["address_embedding_dimensions"]), dtype=numpy.float32)
    w2[:64] = numpy.eye(64, dtype=numpy.float32)
    w2[64:] = rng.normal(0.0, 0.02, size=w2[64:].shape).astype(numpy.float32)
    address_layer = torch.nn.Embedding(1 << 16,
                                       training["address_embedding_dimensions"], sparse=True)
    with torch.no_grad():
        address_layer.weight.copy_(torch.from_numpy(initial_addresses))
    query_w1 = torch.nn.Parameter(torch.from_numpy(w1))
    query_b1 = torch.nn.Parameter(torch.zeros(training["query_hidden_dimensions"]))
    query_w2 = torch.nn.Parameter(torch.from_numpy(w2))
    query_b2 = torch.nn.Parameter(torch.zeros(training["address_embedding_dimensions"]))
    query_optimizer = torch.optim.AdamW(
        [query_w1, query_b1, query_w2, query_b2], lr=training["learning_rate"],
        weight_decay=training["weight_decay"])
    address_optimizer = torch.optim.SparseAdam(
        address_layer.parameters(), lr=training["learning_rate"])
    hidden_tensor = torch.from_numpy(numpy.asarray(hidden, dtype=numpy.float32))
    candidates_tensor = torch.from_numpy(candidates)
    targets_tensor = torch.from_numpy(targets)
    mask_tensor = torch.from_numpy(mask)
    batch_size = int(training["batch_size"])
    losses = []
    for epoch in range(int(training["epochs"])):
        epoch_rng = numpy.random.default_rng(seed ^ (epoch * 0x9E3779B1))
        order = epoch_rng.permutation(hidden.shape[0])
        total_loss = 0.0
        total_rows = 0
        for start in range(0, order.size, batch_size):
            positions = torch.from_numpy(order[start:start + batch_size].astype(numpy.int64))
            query = hidden_tensor[positions]
            selected = candidates_tensor[positions]
            target = targets_tensor[positions]
            selected_mask = mask_tensor[positions]
            query_optimizer.zero_grad(set_to_none=True)
            address_optimizer.zero_grad(set_to_none=True)
            query_embedding = torch.tanh(query @ query_w1 + query_b1) @ query_w2 + query_b2
            query_embedding = functional.normalize(query_embedding, dim=1)
            address_embedding = functional.normalize(address_layer(selected), dim=2)
            scores = torch.einsum("bd,bcd->bc", query_embedding, address_embedding)
            scores = scores * float(training["score_scale"])
            scores = scores.masked_fill(~selected_mask, -1.0e9)
            loss = -(target * functional.log_softmax(scores, dim=1)).sum(dim=1).mean()
            loss.backward()
            query_optimizer.step()
            address_optimizer.step()
            rows = int(positions.numel())
            total_loss += float(loss.detach()) * rows
            total_rows += rows
        losses.append(total_loss / total_rows)
    arrays = {
        "query_w1": query_w1.detach().numpy().astype(numpy.float32),
        "query_b1": query_b1.detach().numpy().astype(numpy.float32),
        "query_w2": query_w2.detach().numpy().astype(numpy.float32),
        "query_b2": query_b2.detach().numpy().astype(numpy.float32),
        "address_embeddings": address_layer.weight.detach().numpy().astype(numpy.float32),
    }
    return arrays, {"epoch_losses": losses, "final_loss": losses[-1],
                    "torch_version": torch.__version__}


def score_model(hidden: numpy.ndarray, occupied: numpy.ndarray,
                arrays: dict[str, numpy.ndarray], maximum: int) -> list[numpy.ndarray]:
    query = numpy.tanh(hidden @ arrays["query_w1"] + arrays["query_b1"])
    query = query @ arrays["query_w2"] + arrays["query_b2"]
    query /= numpy.maximum(numpy.linalg.norm(query, axis=1, keepdims=True),
                           numpy.float32(1.0e-12))
    addresses = arrays["address_embeddings"][occupied]
    addresses /= numpy.maximum(numpy.linalg.norm(addresses, axis=1, keepdims=True),
                               numpy.float32(1.0e-12))
    return decomposition.rank_scores(query @ addresses.T, occupied, maximum)


def read_model(path: Path) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    with numpy.load(path, allow_pickle=False) as stored:
        require(sorted(stored.files) == sorted((*MODEL_ARRAY_NAMES, "metadata_json")),
                "nonlinear scheduler model members differ")
        arrays = {name: stored[name] for name in MODEL_ARRAY_NAMES}
        metadata = json.loads(str(stored["metadata_json"].item()))
    return arrays, metadata


def save_model(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> str:
    require(sorted(arrays) == sorted(MODEL_ARRAY_NAMES),
            "nonlinear scheduler model arrays differ")
    path.parent.mkdir(parents=True, exist_ok=True)
    payloads = {**arrays, "metadata_json": numpy.asarray(json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True))}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, task.array_npy_bytes(payloads[name]))
    replay, replay_metadata = read_model(path)
    require(replay_metadata == metadata
            and all(numpy.array_equal(replay[name], arrays[name]) for name in arrays),
            "nonlinear scheduler model serialization differs")
    return sha256(path)


def choose_size(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passing = [row for row in rows if row["calibration_gate_passed"]]
    if passing:
        return min(passing, key=lambda row: (row["mean_candidate_fraction"],
                                             -row["mean_raw_e5_top10_survival"],
                                             row["training_query_count"]))
    return min(rows, key=lambda row: (-row["mean_raw_e5_top10_survival"],
                                      row["mean_candidate_fraction"],
                                      row["training_query_count"]))


def train_and_calibrate(contract: dict[str, Any], width_contract: dict[str, Any],
                        entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
                        materialization: dict[str, Any], split: dict[str, Any],
                        external_ids: list[str], external_vectors: numpy.ndarray,
                        args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                                           dict[int, dict[str, Any]]]:
    config = next(row for row in width_contract["scales"] if row["id"] == "de-25k")
    data = scale.load_scale(config, args.de_25k_e5_root, args.de_25k_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = [by_id[value] for value in split["training_query_ids"]]
    calibration_positions = [by_id[value] for value in split["configuration_selection_query_ids"]]
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((data["queries"][training_positions], external_vectors), axis=0)
    require(len(pool_ids) == 8141 and pool_vectors.shape == (8141, 384),
            "nonlinear scheduler combined pool differs")
    top100 = exact_top_k(data["documents"], data["document_ids"], pool_vectors,
                         contract["training"]["teacher_exact_e5_top_k"])
    calibration_oracle, _ = scale.exact_oracle(data, calibration_positions, 10)
    manifest_dataset = next(row for row in materialization["datasets"] if row["id"] == "de-25k")
    calibration_rows: list[dict[str, Any]] = []
    model_entries: list[dict[str, Any]] = []
    baseline_selected: dict[int, dict[str, Any]] = {}
    selected_candidates: dict[tuple[int, str], list[dict[str, Any]]] = {}
    maximum = max(contract["calibration"]["probe_budgets"])
    for entry in entries:
        seed = int(entry["seed"])
        arrays = models[(16, seed)]
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-25k" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(route_root, route["document_addresses"]),
                                  dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
        threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
        calibration_hidden = task.infer_hidden(data["queries"][calibration_positions], arrays)
        calibration_logits = (calibration_hidden @ arrays["weight3"].T
                              + arrays["bias3"] - threshold)
        baseline_orders = task.orders(calibration_logits, "occupied_logit", index, 16,
                                      maximum, 0.0)
        baseline_rows = [{"seed": seed, "variant": "occupied_logit",
                          "training_query_count": 0, "mass_penalty": 0.0, **row}
                         for row in task.candidate_summary(
                             index, baseline_orders, contract["calibration"]["probe_budgets"],
                             calibration_oracle, calibration_positions,
                             len(data["document_ids"]), contract)]
        calibration_rows.extend(baseline_rows)
        baseline_selected[seed] = task.select_calibration(baseline_rows, contract)
        pool_hidden = task.infer_hidden(pool_vectors, arrays)
        base_initial = bit_initialization(seed, contract["training"][
            "address_embedding_dimensions"])
        initializations = {
            "direct_id": base_initial,
            "centroid_initialized_id": centroid_initialization(
                data, addresses, index, arrays, base_initial),
        }
        candidates, targets, candidate_mask = sampled_candidates(
            top100, addresses, occupied, pool_ids, seed,
            contract["training"]["negative_addresses_per_query"])
        for variant in contract["training"]["variants"]:
            selected_candidates[(seed, variant)] = []
            for training_count in contract["training"]["nested_query_counts"]:
                model_seed = seed ^ (0x2468ACE if variant == "direct_id" else 0x13579BD)
                learned, training_metrics = train_model(
                    pool_hidden[:training_count], candidates[:training_count],
                    targets[:training_count], candidate_mask[:training_count],
                    initializations[variant], model_seed, contract)
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_nonlinear_direct_address_model",
                    "contract_sha256": sha256(args.contract),
                    "seed": seed,
                    "variant": variant,
                    "training_query_count": training_count,
                    "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                        pool_ids[:training_count], dtype=object)),
                    "document_addresses_sha256": route["document_addresses"]["sha256"],
                    "multilingual_query_manifest_sha256": contract["activation"][
                        "multilingual_query_manifest_sha256"],
                }
                path = args.model_root / f"model-{variant}-{training_count}-{seed}.npz"
                digest = save_model(path, learned, metadata)
                requested = score_model(calibration_hidden, occupied, learned, maximum)
                measured = task.candidate_summary(
                    index, requested, contract["calibration"]["probe_budgets"],
                    calibration_oracle, calibration_positions, len(data["document_ids"]),
                    contract)
                rows = [{"seed": seed, "variant": variant,
                         "training_query_count": training_count, "mass_penalty": 0.0,
                         **row} for row in measured]
                calibration_rows.extend(rows)
                choice = task.select_calibration(rows, contract)
                candidate = {**choice, "seed": seed, "variant": variant,
                             "training_query_count": training_count,
                             "model_file": path.name, "model_sha256": digest}
                selected_candidates[(seed, variant)].append(candidate)
                model_entries.append({
                    "seed": seed, "variant": variant,
                    "training_query_count": training_count,
                    "file": path.name, "sha256": digest,
                    "metadata": metadata,
                    "training": training_metrics,
                    "address_embedding_bytes": int(learned["address_embeddings"].nbytes),
                })
                del learned
                gc.collect()
        del addresses, index, occupied, calibration_hidden, calibration_logits
        del pool_hidden, base_initial, initializations, candidates, targets, candidate_mask
        gc.collect()
    selected = [choose_size(selected_candidates[(seed, variant)])
                for seed in contract["route"]["seeds"]
                for variant in contract["training"]["variants"]]
    del data, top100, pool_vectors
    gc.collect()
    return calibration_rows, model_entries, {"baseline": baseline_selected,
                                              "models": selected}


def load_selected_models(selected: list[dict[str, Any]], root: Path) -> dict[
        tuple[int, str], dict[str, numpy.ndarray]]:
    result = {}
    for row in selected:
        path = root / row["model_file"]
        require(path.is_file() and sha256(path) == row["model_sha256"],
                "nonlinear scheduler selected model bytes differ")
        arrays, metadata = read_model(path)
        require(metadata["seed"] == row["seed"] and metadata["variant"] == row["variant"]
                and metadata["training_query_count"] == row["training_query_count"],
                "nonlinear scheduler selected model metadata differs")
        result[(int(row["seed"]), row["variant"])] = arrays
    return result


def evaluate(contract: dict[str, Any], width_contract: dict[str, Any],
             entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
             materialization: dict[str, Any], split: dict[str, Any],
             selection: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    learned = load_selected_models(selection["models"], args.model_root)
    selected_map = {(int(row["seed"]), row["variant"]): row
                    for row in selection["models"]}
    datasets = []
    for config in width_contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        ids = split["internal_evaluation_query_ids"]
        positions = [by_id[value] for value in ids]
        oracle, full_ndcg = scale.exact_oracle(data, positions, 10)
        manifest_dataset = next(row for row in materialization["datasets"]
                                if row["id"] == config["id"])
        rows = []
        for entry in entries:
            seed = int(entry["seed"])
            arrays = models[(16, seed)]
            route = task.route_entry(manifest_dataset, 16, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(task.read_descriptor(
                route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, 16)
            occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
            hidden = task.infer_hidden(data["queries"][positions], arrays)
            threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
            logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
            baseline_choice = selection["baseline"][seed]
            baseline_orders = task.orders(logits, "occupied_logit", index, 16,
                                          int(baseline_choice["probes"]), 0.0)
            evaluated = task.evaluate_requested(data, positions, baseline_orders, index,
                                                 oracle, full_ndcg, contract)
            listwise.add_oracle_regret(evaluated, data, positions, oracle, addresses, index,
                                       contract)
            rows.append({"seed": seed, "treatment": "occupied_logit", "probes": int(
                baseline_choice["probes"]), "training_query_count": 0,
                "calibration_gate_passed": baseline_choice["calibration_gate_passed"],
                "document_addresses_sha256": route["document_addresses"]["sha256"],
                **evaluated})
            for variant in contract["training"]["variants"]:
                choice = selected_map[(seed, variant)]
                requested = score_model(hidden, occupied, learned[(seed, variant)],
                                        int(choice["probes"]))
                evaluated = task.evaluate_requested(data, positions, requested, index,
                                                     oracle, full_ndcg, contract)
                listwise.add_oracle_regret(evaluated, data, positions, oracle, addresses,
                                           index, contract)
                rows.append({"seed": seed, "treatment": variant,
                             "probes": int(choice["probes"]),
                             "training_query_count": int(choice["training_query_count"]),
                             "model_sha256": choice["model_sha256"],
                             "calibration_gate_passed": choice["calibration_gate_passed"],
                             "document_addresses_sha256": route["document_addresses"]["sha256"],
                             **evaluated})
            del addresses, index, occupied, hidden, logits
            gc.collect()
        datasets.append({"id": config["id"], "document_count": len(data["document_ids"]),
                         "query_count": len(positions),
                         "evaluation_query_ids_sha256": scale.hash_ids(numpy.asarray(
                             ids, dtype=object)), "rows": rows})
        del data, oracle, full_ndcg
        gc.collect()
    return datasets


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    checks = []
    for dataset in datasets:
        for row in dataset["rows"]:
            metrics = row["metrics"]
            checks.append({
                "dataset": dataset["id"], "seed": row["seed"],
                "treatment": row["treatment"], "probes": row["probes"],
                "training_query_count": row["training_query_count"],
                "candidate_fraction": metrics["candidate_fraction"],
                "oracle_regret_mean": row["oracle_regret"]["candidate_fraction_regret"]["mean"],
                "quality_pass": (metrics["candidate_fraction"] <= rule[
                    "maximum_candidate_fraction"]
                    and metrics["adc64_e5_oracle_survival"] >= rule[
                        "minimum_adc64_e5_oracle_survival"]
                    and metrics["exact64_ndcg_retention_vs_full_e5"] >= rule[
                        "minimum_exact64_ndcg_retention_vs_full_e5"]),
            })
    quality = {treatment: all(row["quality_pass"] for row in checks
                              if row["treatment"] == treatment)
               for treatment in ["occupied_logit", *contract["training"]["variants"]]}
    de_1m = [row for row in checks if row["dataset"] == "de-1m"]
    baseline = {row["seed"]: row for row in de_1m if row["treatment"] == "occupied_logit"}
    improvements = []
    success = {"occupied_logit": False}
    for treatment in contract["training"]["variants"]:
        rows = []
        for current in (row for row in de_1m if row["treatment"] == treatment):
            parent = baseline[current["seed"]]
            rows.append({
                "seed": current["seed"],
                "candidate_reduction_vs_baseline": 1.0 - current["candidate_fraction"] / max(
                    parent["candidate_fraction"], 1.0e-30),
                "oracle_regret_reduction_vs_baseline": 1.0 - current["oracle_regret_mean"] / max(
                    parent["oracle_regret_mean"], 1.0e-30),
            })
        improvement_pass = all(
            row["candidate_reduction_vs_baseline"] >= rule[
                "minimum_de_1m_candidate_reduction_vs_baseline"]
            or row["oracle_regret_reduction_vs_baseline"] >= rule[
                "minimum_de_1m_oracle_regret_reduction_vs_baseline"] for row in rows)
        success[treatment] = quality[treatment] and improvement_pass
        improvements.append({"treatment": treatment, "seeds": rows,
                             "improvement_gate_passed": improvement_pass})
    return {"quality_pass": quality, "de_1m_improvements": improvements,
            "treatment_success": success,
            "sequential_followup_licensed": any(success.values()),
            "production_selection_licensed": False, "checks": checks}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (_, width_result, materialization, split, query_manifest,
     external_ids, external_vectors) = validate_activation(contract, args)
    width_contract = listwise.width.planner.load_contract(
        THIS / "neuroute-width-scale-budget.example.json")
    entries = [row for row in task.model_entries(width_result, task.planner.load_contract(
        THIS / "neuroute-task-aware-probe-scheduler.example.json")) if int(row["width"]) == 16]
    models = task.load_models(entries, args.width_model_root)
    calibration, model_entries, selection = train_and_calibrate(
        contract, width_contract, entries, models, materialization, split,
        external_ids, external_vectors, args)
    datasets = evaluate(contract, width_contract, entries, models, materialization, split,
                        selection, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_nonlinear_scheduler_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {"numpy_version": numpy.__version__,
                      "query_bundle_execution": query_manifest["execution"]},
        "matrix": planner.plan(contract),
        "models": model_entries,
        "calibration": calibration,
        "selection": selection,
        "datasets": datasets,
        "decision": decision(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    initialized = bit_initialization(17, 64)
    require(initialized.shape == (65536, 64)
            and numpy.allclose(numpy.linalg.norm(initialized, axis=1), 1.0,
                               rtol=1.0e-5, atol=1.0e-5),
            "nonlinear scheduler initialization self-test differs")
    top100 = numpy.asarray([[0, 1]], dtype=numpy.int32)
    addresses = numpy.asarray([3, 3], dtype=numpy.uint32)
    candidates, targets, mask = sampled_candidates(
        top100, addresses, numpy.asarray([1, 2, 3, 4], dtype=numpy.uint32),
        ["q"], 19, 2)
    require(candidates[0, 0] == 3 and targets[0, 0] == 1.0
            and int(mask.sum()) == 3,
            "nonlinear scheduler sampled teacher self-test differs")
    planner.load_contract(THIS / "neuroute-nonlinear-scheduler.example.json")
    print("NeuRoute nonlinear scheduler self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-nonlinear-scheduler.example.json")
    parser.add_argument("--decomposition-result", type=Path)
    parser.add_argument("--decomposition-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--listwise-result", type=Path)
    parser.add_argument("--listwise-evidence", type=Path)
    parser.add_argument("--listwise-head-root", type=Path)
    parser.add_argument("--task-result", type=Path)
    parser.add_argument("--task-evidence", type=Path)
    parser.add_argument("--task-authoritative-evidence", type=Path)
    parser.add_argument("--width-result", type=Path)
    parser.add_argument("--width-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--width-model-root", type=Path)
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
            parser.error("all nonlinear scheduler paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-nonlinear-scheduler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
