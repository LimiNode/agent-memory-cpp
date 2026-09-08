#!/usr/bin/env python3
"""Run the leakage-safe es-25k direct semantic-address calibration study."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("direct_address_planner", "plan-direct-learned-semantic-address.py")
splitter = load("direct_address_splitter", "materialize-direct-semantic-address-splits.py")
quality = load("direct_address_quality", "evaluate-projection-quantization.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def percentile(values: list[float], fraction: float) -> float:
    require(values and 0.0 <= fraction <= 1.0, "direct semantic address percentile differs")
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction))


def code_values(logits: numpy.ndarray, width: int) -> numpy.ndarray:
    require(logits.ndim == 2 and 0 < width <= min(31, logits.shape[1]), "direct semantic address code width differs")
    powers = numpy.left_shift(numpy.uint32(1), numpy.arange(width, dtype=numpy.uint32))
    return ((logits[:, :width] >= 0.0).astype(numpy.uint32) * powers).sum(axis=1, dtype=numpy.uint32)


def confidence_addresses(logits: numpy.ndarray, width: int, count: int) -> list[int]:
    """Enumerates the cheapest bounded subset flips under absolute-logit cost."""
    require(logits.shape == (16,) and 0 < width <= 16 and 0 < count <= 64, "direct semantic address query logits differ")
    base = int(code_values(logits[None, :], width)[0])
    margins = numpy.abs(logits[:width])
    masks: list[tuple[float, int]] = [(0.0, 0)]
    for flip_count in range(1, min(3, width) + 1):
        for bits in itertools.combinations(range(width), flip_count):
            mask = sum(1 << bit for bit in bits)
            masks.append((float(sum(float(margins[bit]) for bit in bits)), mask))
    masks.sort(key=lambda value: (value[0], value[1]))
    return [base ^ mask for _, mask in masks[:count]]


def document_addresses(logits: numpy.ndarray, width: int, replication: int) -> list[int]:
    require(0 < replication <= 4, "direct semantic address document replication differs")
    base = int(code_values(logits[None, :], width)[0])
    result = [base]
    for bit in numpy.argsort(numpy.abs(logits[:width]), kind="stable")[:replication - 1]:
        result.append(base ^ (1 << int(bit)))
    return result


def gelu_tanh(value: numpy.ndarray) -> numpy.ndarray:
    return 0.5 * value * (1.0 + numpy.tanh(math.sqrt(2.0 / math.pi) * (value + 0.044715 * value ** 3)))


def infer_mlp(queries: numpy.ndarray, artifact: dict[str, numpy.ndarray]) -> numpy.ndarray:
    normalized = (queries - artifact["query_mean"]) / artifact["query_scale"]
    hidden = gelu_tanh(normalized @ artifact["weight1"].T + artifact["bias1"])
    return (hidden @ artifact["weight2"].T + artifact["bias2"]).astype(numpy.float32)


def train_mlp(queries: numpy.ndarray, targets: numpy.ndarray, config: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for the real direct semantic address run") from error
    require(queries.shape == (324, 384) and targets.shape == (324, 16), "direct semantic address training tensors differ")
    torch.manual_seed(config["seed"])
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(config["torch_threads"])
    query_mean = queries.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    query_scale = queries.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    query_scale[query_scale < 1.0e-6] = 1.0
    features = torch.from_numpy(((queries - query_mean) / query_scale).astype(numpy.float32))
    labels = torch.from_numpy(targets.astype(numpy.float32))
    model = torch.nn.Sequential(
        torch.nn.Linear(384, 128),
        torch.nn.GELU(approximate="tanh"),
        torch.nn.Linear(128, 16),
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    generator = torch.Generator().manual_seed(config["seed"] + 1)
    losses: list[float] = []
    started = time.perf_counter()
    for _ in range(config["epochs"]):
        order = torch.randperm(features.shape[0], generator=generator)
        total = 0.0
        for start in range(0, features.shape[0], config["batch_size"]):
            selected = order[start:start + config["batch_size"]]
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(model(features[selected]), labels[selected])
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * selected.numel()
        losses.append(total / features.shape[0])
    elapsed = time.perf_counter() - started
    first, second = model[0], model[2]
    artifact = {
        "query_mean": query_mean,
        "query_scale": query_scale,
        "weight1": first.weight.detach().numpy().astype(numpy.float32),
        "bias1": first.bias.detach().numpy().astype(numpy.float32),
        "weight2": second.weight.detach().numpy().astype(numpy.float32),
        "bias2": second.bias.detach().numpy().astype(numpy.float32),
    }
    replay = infer_mlp(queries, artifact)
    with torch.no_grad():
        expected = model(features).numpy()
    require(numpy.allclose(replay, expected, rtol=2.0e-5, atol=2.0e-5), "direct semantic address MLP serialization replay differs")
    metadata = {
        "family": config["family"],
        "seed": config["seed"],
        "epochs": config["epochs"],
        "batch_size": config["batch_size"],
        "learning_rate": config["learning_rate"],
        "weight_decay": config["weight_decay"],
        "torch_threads": config["torch_threads"],
        "checkpoint": config["checkpoint"],
        "target": config["target"],
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "training_seconds": elapsed,
        "torch_version": torch.__version__,
    }
    return artifact, metadata


def load_inputs(e5_root: Path, input_root: Path) -> dict[str, Any]:
    data = quality.load_root(e5_root)
    manifest_path = input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1 and manifest.get("family") == "mih_storage_benchmark_input_v1", "direct semantic address cascade manifest differs")
    pairs = (
        ("document_codes_file", "document_codes_sha256"),
        ("query_codes_file", "query_codes_sha256"),
        ("query_itq_projections_file", "query_itq_projections_sha256"),
        ("binary_adc_centroids_file", "binary_adc_centroids_sha256"),
        ("document_vectors_file", "document_vectors_sha256"),
        ("query_vectors_file", "query_vectors_sha256"),
    )
    for file_key, hash_key in pairs:
        path = input_root / manifest[file_key]
        require(path.is_file() and sha256(path) == manifest[hash_key], f"frozen cascade payload differs: {file_key}")
    require(manifest["document_vectors_sha256"] == data["output_hashes"]["evaluation_document_vectors"]
            and manifest["query_vectors_sha256"] == data["output_hashes"]["evaluation_query_vectors"]
            and manifest["document_count"] == len(data["document_ids"]) == 25000
            and manifest["query_count"] == len(data["query_ids"]) == 648,
            "direct semantic address frozen roots are not byte-aligned")
    document_codes = numpy.fromfile(input_root / manifest["document_codes_file"], dtype="<u8").reshape(25000, 4).view(numpy.uint8).reshape(25000, 32)
    query_codes = numpy.fromfile(input_root / manifest["query_codes_file"], dtype="<u8").reshape(648, 4).view(numpy.uint8).reshape(648, 32)
    query_projection = numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
    adc_centroids = numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    return {
        **data,
        "documents": numpy.asarray(data["documents"], dtype=numpy.float32),
        "queries": numpy.asarray(data["queries"], dtype=numpy.float32),
        "input_manifest": manifest,
        "input_manifest_sha256": sha256(manifest_path),
        "document_codes": document_codes,
        "document_bits": numpy.unpackbits(document_codes, axis=1, bitorder="little")[:, :256],
        "query_codes": query_codes,
        "query_projection": query_projection,
        "adc_centroids": adc_centroids,
    }


def exact_oracle(data: dict[str, Any], oracle_k: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    positions = numpy.empty((len(data["query_ids"]), oracle_k), dtype=numpy.int32)
    ndcg = numpy.empty(len(data["query_ids"]), dtype=numpy.float64)
    for query_position, query_id in enumerate(data["query_ids"]):
        scores = data["documents"] @ data["queries"][query_position]
        order = numpy.lexsort((data["document_ids"], -scores))
        positions[query_position] = order[:oracle_k]
        ndcg[query_position] = quality.dcg_at_10(data["document_ids"][order], data["qrels"][query_id])
    return positions, ndcg


def document_head(documents: numpy.ndarray) -> tuple[numpy.ndarray, dict[str, numpy.ndarray]]:
    mean = documents.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    centered_sample = documents[::4].astype(numpy.float64) - mean
    _, _, right = numpy.linalg.svd(centered_sample, full_matrices=False)
    projection = right[:16].T.astype(numpy.float32)
    raw = (documents - mean) @ projection
    threshold = numpy.median(raw, axis=0).astype(numpy.float32)
    logits = (raw - threshold).astype(numpy.float32)
    return logits, {"document_mean": mean, "document_projection": projection, "document_threshold": threshold}


def build_index(document_logits: numpy.ndarray, documents: numpy.ndarray, width: int, replication: int) -> dict[str, Any]:
    lists: dict[int, list[int]] = {}
    for position, row in enumerate(document_logits):
        for address in document_addresses(row, width, replication):
            lists.setdefault(address, []).append(position)
    postings = {address: numpy.asarray(values, dtype=numpy.int32) for address, values in lists.items()}
    centroids: dict[int, numpy.ndarray] = {}
    for address, values in postings.items():
        centroid = documents[values].mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
        norm = float(numpy.linalg.norm(centroid))
        centroids[address] = centroid / norm if norm else centroid
    key_bytes = 4 if width <= 16 else 8
    return {
        "postings": postings,
        "centroids": centroids,
        "occupied_addresses": numpy.asarray(sorted(postings), dtype=numpy.uint32),
        "posting_ids": sum(len(values) for values in postings.values()),
        "payload_bytes": sum(len(values) for values in postings.values()) * 4 + len(postings) * (key_bytes + 8),
    }


def ordered_addresses(treatment: str, query_logits: numpy.ndarray, query: numpy.ndarray,
                      index: dict[str, Any], width: int, probes: int) -> list[int]:
    if treatment in ("symmetric_document_head_control", "learned_direct_address_postings"):
        return confidence_addresses(query_logits, width, probes)
    if treatment == "learned_address_then_float_bucket_centroid_refinement":
        pool = confidence_addresses(query_logits, width, min(64, max(16, probes * 4)))
        occupied = [address for address in pool if address in index["centroids"]]
        occupied.sort(key=lambda address: (-float(index["centroids"][address] @ query), address))
        return occupied[:probes]
    require(treatment == "exact_float_bucket_centroid_scan_same_postings", "direct semantic address treatment differs")
    addresses = index["occupied_addresses"]
    scores = numpy.asarray([index["centroids"][int(address)] @ query for address in addresses], dtype=numpy.float32)
    return [int(addresses[position]) for position in numpy.lexsort((addresses, -scores))[:probes]]


def candidate_union(addresses: list[int], postings: dict[int, numpy.ndarray], document_count: int,
                    mass_target: float) -> tuple[numpy.ndarray, list[int]]:
    limit = max(1, int(math.floor(document_count * mass_target)))
    selected: list[numpy.ndarray] = []
    accepted: list[int] = []
    marked = numpy.zeros(document_count, dtype=numpy.bool_)
    count = 0
    for address in addresses:
        values = postings.get(address)
        if values is None:
            accepted.append(address)
            continue
        fresh = values[~marked[values]]
        if count + fresh.size > limit:
            continue
        marked[fresh] = True
        selected.append(fresh)
        accepted.append(address)
        count += int(fresh.size)
    candidates = numpy.concatenate(selected) if selected else numpy.empty(0, dtype=numpy.int32)
    candidates.sort()
    require(candidates.size <= limit and numpy.unique(candidates).size == candidates.size, "direct semantic address candidate ceiling differs")
    return candidates, accepted


def cascade(data: dict[str, Any], query_position: int, candidates: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    if not candidates.size:
        empty = numpy.empty(0, dtype=numpy.int32)
        return empty, empty, empty
    xor = numpy.bitwise_xor(data["document_codes"][candidates], data["query_codes"][query_position])
    distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    hamming = candidates[numpy.lexsort((data["document_ids"][candidates], distances))[:768]]
    table = (data["query_projection"][query_position, :, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], data["document_bits"][hamming]].sum(axis=1)
    adc = hamming[numpy.lexsort((data["document_ids"][hamming], adc_distances))[:256]]
    scores = data["documents"][adc] @ data["queries"][query_position]
    ranked = adc[numpy.lexsort((data["document_ids"][adc], -scores))[:10]]
    return hamming, adc, ranked


def evaluate(data: dict[str, Any], query_positions: list[int], query_logits: numpy.ndarray,
             index: dict[str, Any], oracle: numpy.ndarray, full_ndcg: numpy.ndarray,
             treatment: str, width: int, probes: int, mass_target: float,
             measure_timing: bool, retain_audit: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts: list[float] = []
    raw_survivals: list[float] = []
    hamming_survivals: list[float] = []
    adc_survivals: list[float] = []
    ndcgs: list[float] = []
    times: list[float] = []
    audits: list[dict[str, Any]] = []
    for query_position in query_positions:
        started = time.perf_counter()
        requested = ordered_addresses(treatment, query_logits[query_position], data["queries"][query_position], index, width, probes)
        candidates, accepted = candidate_union(requested, index["postings"], len(data["document_ids"]), mass_target)
        hamming, adc, ranked = cascade(data, query_position, candidates)
        if measure_timing:
            times.append((time.perf_counter() - started) * 1000.0)
        raw_survival = float(numpy.isin(oracle[query_position], candidates).sum()) / oracle.shape[1]
        hamming_survival = float(numpy.isin(oracle[query_position], hamming).sum()) / oracle.shape[1]
        adc_survival = float(numpy.isin(oracle[query_position], adc).sum()) / oracle.shape[1]
        ndcg = quality.dcg_at_10(data["document_ids"][ranked], data["qrels"][data["query_ids"][query_position]])
        counts.append(float(candidates.size))
        raw_survivals.append(raw_survival)
        hamming_survivals.append(hamming_survival)
        adc_survivals.append(adc_survival)
        ndcgs.append(ndcg)
        if retain_audit:
            audits.append({
                "query_position": query_position,
                "query_id": data["query_ids"][query_position],
                "requested_addresses": requested,
                "accepted_addresses": accepted,
                "candidate_positions": candidates.tolist(),
                "hamming_positions": hamming.tolist(),
                "adc_positions": adc.tolist(),
                "reranked_positions": ranked.tolist(),
                "e5_oracle_raw_union_survival": raw_survival,
                "e5_oracle_hamming_survival": hamming_survival,
                "e5_oracle_survival_after_adc": adc_survival,
                "reranked_ndcg_at_10": ndcg,
                "full_e5_ndcg_at_10": float(full_ndcg[query_position]),
            })
    metrics = {
        "query_count": len(query_positions),
        "candidate_fraction": float(numpy.mean(counts, dtype=numpy.float64)) / len(data["document_ids"]),
        "candidate_count_p95": percentile(counts, 0.95),
        "e5_oracle_raw_union_survival": float(numpy.mean(raw_survivals, dtype=numpy.float64)),
        "e5_oracle_hamming_survival": float(numpy.mean(hamming_survivals, dtype=numpy.float64)),
        "e5_oracle_survival_after_adc": float(numpy.mean(adc_survivals, dtype=numpy.float64)),
        "reranked_ndcg_at_10": float(numpy.mean(ndcgs, dtype=numpy.float64)),
        "full_e5_ndcg_at_10": float(numpy.mean(full_ndcg[query_positions], dtype=numpy.float64)),
        "routing_cascade_p50_ms": percentile(times, 0.50) if times else None,
        "routing_cascade_p95_ms": percentile(times, 0.95) if times else None,
        "posting_ids": index["posting_ids"],
        "posting_payload_bytes": index["payload_bytes"],
        "occupied_address_count": len(index["postings"]),
    }
    return metrics, audits


def selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["e5_oracle_survival_after_adc"],
        row["reranked_ndcg_at_10"],
        -row["candidate_fraction"],
        -row["routing_cascade_p50_ms"],
        -row["semantic_prefix_bits"],
        -row["query_probes"],
        -row["document_replication"],
    )


def save_artifact(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    with numpy.load(path, allow_pickle=False) as stored:
        require(json.loads(str(stored["metadata_json"].item())) == metadata, "direct semantic address artifact metadata differs")
        for name, value in arrays.items():
            require(numpy.array_equal(stored[name], value), f"direct semantic address artifact array differs: {name}")


def run(contract_path: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = planner.load_contract(contract_path)
    data = load_inputs(e5_root, input_root)
    oracle, full_ndcg = exact_oracle(data, contract["cascade"]["oracle_k"])
    split_ids = splitter.materialize(data["query_ids"], contract)
    id_to_query = {value: index for index, value in enumerate(data["query_ids"])}
    partitions = {
        name: [id_to_query[value] for value in split_ids[f"{name}_query_ids"]]
        for name in ("training", "configuration_selection", "internal_evaluation")
    }
    document_logits, document_artifact = document_head(data["documents"])
    symmetric_logits = ((data["queries"] - document_artifact["document_mean"]) @ document_artifact["document_projection"] - document_artifact["document_threshold"]).astype(numpy.float32)
    target_probabilities = numpy.asarray([
        (document_logits[oracle[position]] >= 0.0).mean(axis=0)
        for position in partitions["training"]
    ], dtype=numpy.float32)
    mlp_artifact, training = train_mlp(data["queries"][partitions["training"]], target_probabilities, contract["router_training"])
    learned_logits = infer_mlp(data["queries"], mlp_artifact)
    artifact_arrays = {**document_artifact, **mlp_artifact}
    artifact_metadata = {
        "schema_version": 1,
        "family": "direct_learned_semantic_address_model_v1",
        "contract_sha256": sha256(contract_path),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "training_query_ids": split_ids["training_query_ids"],
        "training": training,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / "model.npz"
    save_artifact(artifact_path, artifact_arrays, artifact_metadata)

    indexes: dict[tuple[int, int], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    treatments = {
        "symmetric_document_head_control": symmetric_logits,
        "learned_direct_address_postings": learned_logits,
    }
    for planned in planner.plan(contract)["rows"]:
        width = planned["semantic_prefix_bits"]
        replication = planned["document_replication"]
        index = indexes.setdefault((width, replication), build_index(document_logits, data["documents"], width, replication))
        for treatment, logits in treatments.items():
            metrics, _ = evaluate(
                data,
                partitions["configuration_selection"],
                logits,
                index,
                oracle,
                full_ndcg,
                treatment,
                width,
                planned["query_probes"],
                planned["candidate_mass_target"],
                True,
                False,
            )
            rows.append({"treatment": treatment, **planned, **metrics})

    selected_by_budget: list[dict[str, Any]] = []
    for mass_target in contract["candidate_mass_targets"]:
        eligible = [
            row for row in rows
            if row["treatment"] == contract["selection"]["headline_treatment"]
            and row["candidate_mass_target"] == mass_target
            and row["candidate_fraction"] <= mass_target
        ]
        require(eligible, f"no learned direct semantic address row satisfies candidate budget {mass_target}")
        selected_by_budget.append(max(eligible, key=selection_key))
    selected = next(row for row in selected_by_budget if row["candidate_mass_target"] == contract["selection"]["candidate_mass_target"])
    width = selected["semantic_prefix_bits"]
    replication = selected["document_replication"]
    probes = selected["query_probes"]
    mass_target = selected["candidate_mass_target"]
    index = indexes[(width, replication)]

    matched_treatments = {
        "symmetric_document_head_control": symmetric_logits,
        "learned_direct_address_postings": learned_logits,
        "learned_address_then_float_bucket_centroid_refinement": learned_logits,
        "exact_float_bucket_centroid_scan_same_postings": learned_logits,
    }
    selection_controls: list[dict[str, Any]] = []
    internal_controls: list[dict[str, Any]] = []
    for treatment, logits in matched_treatments.items():
        selection_metrics, selection_audit = evaluate(data, partitions["configuration_selection"], logits, index, oracle, full_ndcg, treatment, width, probes, mass_target, True, True)
        internal_metrics, internal_audit = evaluate(data, partitions["internal_evaluation"], logits, index, oracle, full_ndcg, treatment, width, probes, mass_target, True, True)
        selection_controls.append({"treatment": treatment, **selection_metrics})
        internal_controls.append({"treatment": treatment, **internal_metrics})
        (output_root / f"selection-audit-{treatment}.json").write_bytes(canonical({"schema_version": 1, "treatment": treatment, "rows": selection_audit}))
        (output_root / f"internal-audit-{treatment}.json").write_bytes(canonical({"schema_version": 1, "treatment": treatment, "rows": internal_audit}))

    router_times: list[float] = []
    for position in partitions["configuration_selection"]:
        for _ in range(20):
            started = time.perf_counter()
            infer_mlp(data["queries"][position:position + 1], mlp_artifact)
            router_times.append((time.perf_counter() - started) * 1.0e6)
    result = {
        "schema_version": 1,
        "family": "direct_learned_semantic_address_result_v1",
        "contract_sha256": sha256(contract_path),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "model_sha256": sha256(artifact_path),
        "splits": split_ids,
        "training": training,
        "selection_rows": rows,
        "selected_headline_by_budget": selected_by_budget,
        "selected_headline": selected,
        "matched_selection_controls": selection_controls,
        "internal_evaluation_controls": internal_controls,
        "query_router_inference": {
            "implementation": "numpy_float32_single_query_mlp_excluding_e5_v1",
            "samples": len(router_times),
            "p50_us": percentile(router_times, 0.50),
            "p95_us": percentile(router_times, 0.95),
        },
        "timing_scope": "directional_single_local_run_warm_python_numpy_excludes_e5_query_encoding_v1",
        "runtime": {
            "python": platform.python_version(),
            "numpy": numpy.__version__,
            "platform": platform.platform(),
        },
    }
    (output_root / "result.json").write_bytes(canonical(result))


def self_test() -> None:
    logits = numpy.asarray([2.0, -0.1, 3.0] + [1.0] * 13, dtype=numpy.float32)
    require(code_values(logits[None, :], 3).tolist() == [5], "direct semantic address code primitive differs")
    addresses = confidence_addresses(logits, 3, 4)
    require(addresses[0] == 5 and len(addresses) == len(set(addresses)) == 4, "direct semantic address confidence probing differs")
    postings = {1: numpy.asarray([0, 1]), 2: numpy.asarray([1, 2]), 3: numpy.asarray([3, 4])}
    candidates, accepted = candidate_union([1, 2, 3], postings, 10, 0.4)
    require(candidates.tolist() == [0, 1, 2] and accepted == [1, 2], "direct semantic address hard candidate ceiling differs")
    print("direct semantic address runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "direct-learned-semantic-address.example.json")
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.e5_root, args.input_root, args.output_root)):
            parser.error("--e5-root, --input-root, and --output-root are required")
        run(args.contract, args.e5_root, args.input_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-direct-learned-semantic-address: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
