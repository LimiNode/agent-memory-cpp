#!/usr/bin/env python3
"""Measure train-only data-dependent product routing over frozen ITQ/E5 data."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import faiss
import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "data_dependent_product_locator_v1"
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)
MEDOID_SAMPLE_COUNT = 1024
MEDOID_ITERATIONS = 4
KMEANS_ITERATIONS = 25
KMEANS_SEED = 20260826


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load("data_dependent_product_locator_evaluator", "evaluate-native-ann-shortlists.py")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "product locator contract identity differs")
    require(value.get("faiss_version") == faiss.__version__ == "1.13.2", "product locator Faiss version differs")
    require(value.get("training_source") == "frozen_train_itq256_codes_derived_only_from_pinned_artifact_and_frozen_train_e5_vectors_only", "product locator training source differs")
    require(value.get("implicit_cell_budgets") == [4096, 16384, 65536] and value.get("target_candidate_fractions") == [.05, .10, .25], "product locator matrix differs")
    require(value.get("amends_measurement_contract_sha256") == "0ce223f825adc5b55ef5662ca628b0ddd5d5fcf8b40ea9aa2345414f26809aadb", "product locator measurement-contract amendment differs")
    require(value.get("routing") == "best_first_sum_local_hamming_then_cell_key_lexicographic_v1" and value.get("routing_by_treatment") == {"local_binary_medoids": "best_first_sum_local_hamming_then_cell_key_lexicographic_v1", "permuted_binary_medoids": "best_first_sum_local_hamming_then_cell_key_lexicographic_v1", "float_e5_product": "best_first_sum_local_squared_l2_e5_then_cell_key_lexicographic_v1"}, "product locator routing differs")
    require(value.get("training") == {"binary_hamming_kmedoids": {"sample": "deterministic_evenly_spaced_train_positions_v1", "sample_count": MEDOID_SAMPLE_COUNT, "iterations": MEDOID_ITERATIONS, "initialization": "farthest_first_hamming_with_lowest_position_ties_v1"}, "float_e5_kmeans": {"clusters_per_block": 4, "iterations": KMEANS_ITERATIONS, "restarts": 1, "seed": KMEANS_SEED, "spherical": False}}, "product locator training parameters differ")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "product locator cascade differs")
    return value


def packed_codes(path: Path, count: int) -> numpy.ndarray:
    values = numpy.fromfile(path, dtype="<u8")
    require(values.size == count * 4, "product locator packed code payload differs")
    return values.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def blocks(dimension: int, count: int) -> list[numpy.ndarray]:
    require(count in (6, 7, 8), "product locator block count differs")
    result = [part.astype(numpy.int16, copy=False) for part in numpy.array_split(numpy.arange(dimension), count)]
    require(sum(part.size for part in result) == dimension and max(part.size for part in result) - min(part.size for part in result) <= 1, "product locator blocks are not balanced")
    return result


def train_binary_codes(train: numpy.ndarray, artifact: Path, expected_sha: str, expected_train_sha: str, expected_train_ids_sha: str) -> tuple[numpy.ndarray, str]:
    require(artifact.is_file() and sha256(artifact) == expected_sha, "product locator ITQ artifact bytes differ")
    with numpy.load(artifact, allow_pickle=False) as archive:
        identity = json.loads(str(archive["identity_json"].item()))
        weights = numpy.asarray(archive["weights"], dtype=numpy.float32)
        thresholds = numpy.asarray(archive["thresholds"], dtype=numpy.float32)
    require(identity.get("family") == "mih_storage_itq_artifact_v2" and identity.get("calibration_train_vectors_sha256") == expected_train_sha and identity.get("calibration_train_ids_sha256") == expected_train_ids_sha and weights.shape == (256, train.shape[1]) and thresholds.shape == (256,), "product locator ITQ artifact differs")
    return (train @ weights.T + thresholds >= 0.0), sha256(artifact)


def deterministic_train_sample(count: int) -> numpy.ndarray:
    require(count >= 4, "product locator train sample is too small")
    if count <= MEDOID_SAMPLE_COUNT:
        return numpy.arange(count, dtype=numpy.int64)
    return numpy.linspace(0, count - 1, MEDOID_SAMPLE_COUNT, dtype=numpy.int64)


def hamming(values: numpy.ndarray, center: numpy.ndarray) -> numpy.ndarray:
    return numpy.count_nonzero(values != center, axis=1).astype(numpy.int16, copy=False)


def binary_medoids(train_bits: numpy.ndarray) -> numpy.ndarray:
    """Fit deterministic genuine Hamming medoids from a frozen train-only sample."""
    sample = train_bits[deterministic_train_sample(train_bits.shape[0])]
    chosen = [0]
    nearest = hamming(sample, sample[0])
    for _ in range(1, 4):
        maximum = int(nearest.max())
        chosen.append(int(numpy.flatnonzero(nearest == maximum)[0]))
        nearest = numpy.minimum(nearest, hamming(sample, sample[chosen[-1]]))
    medoids = sample[numpy.asarray(chosen, dtype=numpy.int64)].copy()
    for _ in range(MEDOID_ITERATIONS):
        distances = numpy.stack([hamming(sample, current) for current in medoids], axis=1)
        assigned = distances.argmin(axis=1)
        updated = medoids.copy()
        for current in range(4):
            group = sample[assigned == current]
            if not group.size:
                continue
            costs = numpy.empty(group.shape[0], dtype=numpy.int64)
            for position, candidate in enumerate(group):
                costs[position] = hamming(group, candidate).sum(dtype=numpy.int64)
            updated[current] = group[int(numpy.argmin(costs))]
        if numpy.array_equal(updated, medoids):
            break
        medoids = updated
    return medoids


def entropy_permutation(bits: numpy.ndarray) -> numpy.ndarray:
    probability = bits.mean(axis=0, dtype=numpy.float64)
    entropy = numpy.zeros(probability.shape, dtype=numpy.float64)
    nonzero = (probability > 0.0) & (probability < 1.0)
    entropy[nonzero] = -probability[nonzero] * numpy.log2(probability[nonzero]) - (1.0 - probability[nonzero]) * numpy.log2(1.0 - probability[nonzero])
    positions = numpy.arange(bits.shape[1], dtype=numpy.int16)
    return positions[numpy.lexsort((positions, -entropy))]


def binary_assign(bits: numpy.ndarray, positions: list[numpy.ndarray], medoids: list[numpy.ndarray]) -> numpy.ndarray:
    output = numpy.empty((bits.shape[0], len(positions)), dtype=numpy.uint8)
    for index, (part, centers) in enumerate(zip(positions, medoids, strict=True)):
        distances = numpy.stack([hamming(bits[:, part], center) for center in centers], axis=1)
        output[:, index] = distances.argmin(axis=1).astype(numpy.uint8)
    return output


def float_centers(train: numpy.ndarray, positions: list[numpy.ndarray], seed_offset: int) -> list[numpy.ndarray]:
    result: list[numpy.ndarray] = []
    for index, part in enumerate(positions):
        trainer = faiss.Kmeans(part.size, 4, niter=KMEANS_ITERATIONS, nredo=1, seed=KMEANS_SEED + seed_offset * 31 + index, spherical=False, verbose=False, gpu=False)
        trainer.train(numpy.ascontiguousarray(train[:, part], dtype=numpy.float32))
        result.append(numpy.asarray(trainer.centroids, dtype=numpy.float32).reshape(4, part.size).copy())
    return result


def float_assign(vectors: numpy.ndarray, positions: list[numpy.ndarray], centers: list[numpy.ndarray]) -> numpy.ndarray:
    output = numpy.empty((vectors.shape[0], len(positions)), dtype=numpy.uint8)
    for index, (part, local) in enumerate(zip(positions, centers, strict=True)):
        distances = ((vectors[:, part, None] - local.T[None, :, :]) ** 2).sum(axis=1)
        output[:, index] = distances.argmin(axis=1).astype(numpy.uint8)
    return output


def cell_keys(cells: numpy.ndarray) -> numpy.ndarray:
    output = numpy.zeros(cells.shape[0], dtype=numpy.int64)
    for column in range(cells.shape[1]):
        output = output * 4 + cells[:, column]
    return output


def cell_index(cells: numpy.ndarray) -> dict[int, numpy.ndarray]:
    keys = cell_keys(cells)
    positions = numpy.arange(keys.size, dtype=numpy.int64)
    order = numpy.lexsort((positions, keys))
    sorted_keys = keys[order]
    begin = numpy.r_[0, numpy.flatnonzero(numpy.diff(sorted_keys)) + 1]
    end = numpy.r_[begin[1:], order.size]
    return {int(sorted_keys[left]): order[left:right] for left, right in zip(begin, end, strict=True)}


def local_binary_costs(query: numpy.ndarray, positions: list[numpy.ndarray], medoids: list[numpy.ndarray]) -> list[numpy.ndarray]:
    return [numpy.asarray([int(numpy.count_nonzero(query[part] != center)) for center in local], dtype=numpy.float64) for part, local in zip(positions, medoids, strict=True)]


def local_float_costs(query: numpy.ndarray, positions: list[numpy.ndarray], centers: list[numpy.ndarray]) -> list[numpy.ndarray]:
    return [((local - query[part]) ** 2).sum(axis=1, dtype=numpy.float64) for part, local in zip(positions, centers, strict=True)]


def routing_rule(treatment_id: str) -> str:
    return "best_first_sum_local_squared_l2_e5_then_cell_key_lexicographic_v1" if treatment_id == "float_e5_product" else "best_first_sum_local_hamming_then_cell_key_lexicographic_v1"


def local_costs(treatment_id: str, query: numpy.ndarray, query_bits: numpy.ndarray, positions: list[numpy.ndarray], codebooks: list[numpy.ndarray]) -> list[numpy.ndarray]:
    return local_float_costs(query, positions, codebooks) if treatment_id == "float_e5_product" else local_binary_costs(query_bits, positions, codebooks)


def best_first_cells(local_costs: list[numpy.ndarray]):
    """Yield every product cell by cost, then lexical base-four cell key."""
    orders = [numpy.lexsort((numpy.arange(4), costs)) for costs in local_costs]
    start = tuple(0 for _ in orders)
    def entry(ranks: tuple[int, ...]) -> tuple[float, int, tuple[int, ...]]:
        values = tuple(int(orders[index][rank]) for index, rank in enumerate(ranks))
        key = 0
        cost = 0.0
        for index, value in enumerate(values):
            key = key * 4 + value
            cost += float(local_costs[index][value])
        return cost, key, ranks
    heap = [entry(start)]
    seen = {start}
    while heap:
        cost, key, ranks = heapq.heappop(heap)
        yield cost, key
        for axis in range(len(ranks)):
            if ranks[axis] == 3:
                continue
            child = list(ranks); child[axis] += 1; current = tuple(child)
            if current not in seen:
                seen.add(current); heapq.heappush(heap, entry(current))


def hamming_positions(document_codes: numpy.ndarray, query_code: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    distances = POPCOUNT[numpy.bitwise_xor(document_codes[candidates], query_code)].sum(axis=1, dtype=numpy.uint16)
    return candidates[numpy.lexsort((candidates, distances))[:768]]


def adc_positions(document_bits: numpy.ndarray, query_projection: numpy.ndarray, adc_centroids: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    table = (query_projection[:, None] - adc_centroids) ** 2
    distances = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distances))[:256]]


def write_quality(data: dict[str, Any], shortlist: Path, contribution: Path, quality: Path, oracle: Path) -> dict[str, Any]:
    _, rows = evaluator.load_export(shortlist, len(data["query_ids"]), len(data["document_ids"]), 768, 256)
    exact_top, full_ndcg = evaluator.load_or_create_oracle_cache(data, oracle, 10)
    report, contributions = evaluator.evaluate(data, rows, 768, 256, 10, exact_top, full_ndcg)
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    contribution.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(contribution, **contributions, query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    sources = {"evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")}
    payload = {"schema_version": 1, "family": "native_ann_shortlist_quality_v1", "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "shortlist_export_sha256": sha256(shortlist), "shortlist_export_backend": "data_dependent_product_locator", "oracle_cache_sha256": sha256(oracle), "hamming_limit": 768, "adc_limit": 256, "oracle_k": 10, "per_query_contributions_path": str(contribution), "per_query_contributions_sha256": sha256(contribution), "per_query_contribution_identity": identity, "evaluator_source_files_sha256": sources, "evaluator_source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), **report}
    quality.parent.mkdir(parents=True, exist_ok=True)
    quality.write_bytes(canonical(payload))
    return payload


def artifact_metadata(scale: str, treatment: str, budget: int, input_manifest: str, evaluation_manifest: str, train_hash: str, itq_hash: str, embedding_dimension: int) -> dict[str, Any]:
    routing_dimension = embedding_dimension if treatment == "float_e5_product" else 256
    return {"schema_version": 1, "family": "data_dependent_product_locator_artifact_v1", "scale": scale, "treatment": treatment, "implicit_cell_budget": budget, "local_codebook_size": 4, "block_count": int(round(math.log(budget, 4))), "input_manifest_sha256": input_manifest, "evaluation_manifest_sha256": evaluation_manifest, "train_vectors_sha256": train_hash, "itq_artifact_sha256": itq_hash, "embedding_dimension": embedding_dimension, "routing_dimension": routing_dimension, "binary_training": "deterministic_train_sample_hamming_kmedoids_v1" if treatment != "float_e5_product" else None, "float_training": "faiss_kmeans_train_only_v1" if treatment == "float_e5_product" else None}


def write_artifact(path: Path, metadata: dict[str, Any], positions: list[numpy.ndarray], codebooks: list[numpy.ndarray], document_cells: numpy.ndarray, permutation: numpy.ndarray | None) -> None:
    arrays: dict[str, Any] = {"metadata_json": numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), "document_cells": document_cells}
    if permutation is not None:
        arrays["permutation"] = permutation
    for index, (part, book) in enumerate(zip(positions, codebooks, strict=True)):
        arrays[f"block_{index}"] = part
        arrays[f"codebook_{index}"] = book
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(path, **arrays)


def load_artifact(path: Path, metadata: dict[str, Any], document_count: int) -> tuple[list[numpy.ndarray], list[numpy.ndarray], numpy.ndarray, numpy.ndarray | None]:
    with numpy.load(path, allow_pickle=False) as archive:
        require(json.loads(str(archive["metadata_json"].item())) == metadata, "product locator serialized artifact provenance differs")
        positions = [numpy.asarray(archive[f"block_{index}"], dtype=numpy.int16).copy() for index in range(metadata["block_count"])]
        books = [numpy.asarray(archive[f"codebook_{index}"]).copy() for index in range(metadata["block_count"])]
        cells = numpy.asarray(archive["document_cells"], dtype=numpy.uint8).copy()
        permutation = numpy.asarray(archive["permutation"], dtype=numpy.int16).copy() if "permutation" in archive.files else None
    require(cells.shape == (document_count, metadata["block_count"]) and numpy.all(cells < 4), "product locator serialized document cells differ")
    require(sum(part.size for part in positions) == metadata["routing_dimension"] and all(book.shape[0] == 4 for book in books), "product locator serialized codebook shape differs")
    return positions, books, cells, permutation


def create_artifact(output: Path, metadata: dict[str, Any], treatment: str, train_e5: numpy.ndarray, train_bits: numpy.ndarray, documents: numpy.ndarray, document_bits: numpy.ndarray) -> tuple[list[numpy.ndarray], list[numpy.ndarray], numpy.ndarray, numpy.ndarray | None, Path]:
    path = output / "artifacts" / f"{treatment}-cells{metadata['implicit_cell_budget']}.npz"
    if path.is_file():
        positions, books, cells, permutation = load_artifact(path, metadata, documents.shape[0])
        return positions, books, cells, permutation, path
    count = metadata["block_count"]
    if treatment == "float_e5_product":
        positions = blocks(train_e5.shape[1], count)
        books = float_centers(train_e5, positions, count)
        cells = float_assign(documents, positions, books)
        permutation = None
    else:
        permutation = entropy_permutation(train_bits) if treatment == "permuted_binary_medoids" else numpy.arange(256, dtype=numpy.int16)
        positions = blocks(256, count)
        positions = [permutation[part] for part in positions]
        books = [binary_medoids(train_bits[:, part]) for part in positions]
        cells = binary_assign(document_bits, positions, books)
    write_artifact(path, metadata, positions, books, cells, permutation)
    positions, books, cells, permutation = load_artifact(path, metadata, documents.shape[0])
    return positions, books, cells, permutation, path


def complete(config_path: Path, config: dict[str, Any], shortlist: Path, quality: Path, contribution: Path) -> bool:
    if not all(path.is_file() for path in (config_path, shortlist, quality, contribution)):
        return False
    try:
        require(json.loads(config_path.read_text(encoding="utf-8")) == config, "product locator saved config differs")
        current = json.loads(quality.read_text(encoding="utf-8"))
        return current.get("shortlist_export_sha256") == sha256(shortlist) and current.get("per_query_contributions_sha256") == sha256(contribution) and current.get("shortlist_export_backend") == "data_dependent_product_locator"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def prior_measurements(output_root: Path, contract: dict[str, Any], itq_artifact: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Recover complete rows without changing their measured status or metrics."""
    path = output_root / "summary.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        accepted_contracts = {sha256(Path(contract["_path"])), contract["amends_measurement_contract_sha256"]}
        require(payload.get("schema_version") == 1 and payload.get("family") == FAMILY and payload.get("contract_sha256") in accepted_contracts and payload.get("itq_artifact_sha256") == sha256(itq_artifact) and isinstance(payload.get("rows"), list), "product locator prior summary differs")
        rows = {(str(row["scale"]), str(row["id"])): row for row in payload["rows"]}
        require(len(rows) == len(payload["rows"]), "product locator prior summary contains duplicate rows")
        return rows
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {}


def reusable_row(row: Any, expected: dict[str, Any]) -> bool:
    metric_names = ("actual_candidate_fraction", "candidate_count_p95", "cell_probes_p50", "cell_probes_p95", "nonempty_cells_p50", "nonempty_cells_p95", "routing_p50_ms_per_query", "routing_p95_ms_per_query", "e5_oracle_survival_after_adc", "reranked_ndcg_at_10")
    return isinstance(row, dict) and row.get("status") == "measured" and all(row.get(name) == value for name, value in expected.items()) and all(isinstance(row.get(name), (int, float)) and not isinstance(row.get(name), bool) and row[name] >= 0.0 for name in metric_names)


def route(local_costs: list[numpy.ndarray], index: dict[int, numpy.ndarray], target: int) -> tuple[numpy.ndarray, list[int], int, int, float]:
    started = time.perf_counter()
    selected: list[numpy.ndarray] = []
    visited: list[int] = []
    probes = 0
    count = 0
    for _, key in best_first_cells(local_costs):
        probes += 1
        documents = index.get(key)
        if documents is None:
            continue
        visited.append(key); selected.append(documents); count += documents.size
        if count >= target:
            break
    candidates = numpy.sort(numpy.concatenate(selected, dtype=numpy.int64))
    require(candidates.size >= target, "product locator exhaustive cell traversal did not reach target")
    return candidates, visited, probes, len(visited), (time.perf_counter() - started) * 1000.0


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    contract["_path"] = str(args.contract)
    prior = prior_measurements(args.output_root, contract, args.itq_artifact)
    summary: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, document_count = scale["id"], scale["documents"]
        root, output = args.scale_root / scale_id, args.output_root / scale_id
        input_root, evaluation_root = root / "input", root / "e5"
        input_manifest_path, evaluation_manifest_path = input_root / "manifest.json", evaluation_root / "manifest.json"
        require(sha256(input_manifest_path) == scale["input_manifest_sha256"] and sha256(evaluation_manifest_path) == scale["evaluation_manifest_sha256"], f"product locator frozen manifests differ: {scale_id}")
        input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        data = evaluator.shared.load_root(evaluation_root)
        require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == document_count and len(data["query_ids"]) == 648, f"product locator evaluation payload differs: {scale_id}")
        train_path = evaluation_root / "train-vectors.f32"
        train = numpy.fromfile(train_path, dtype="<f4").reshape(-1, data["dimension"])
        require(sha256(train_path) == input_manifest["calibration_train_vectors_sha256"], f"product locator train vectors differ: {scale_id}")
        require(evaluator.shared.ordered_ids_sha256(data["train_ids"]) == input_manifest["calibration_train_ids_sha256"], f"product locator train ids differ: {scale_id}")
        train_bits, itq_hash = train_binary_codes(train, args.itq_artifact, input_manifest["itq_artifact_sha256"], sha256(train_path), input_manifest["calibration_train_ids_sha256"])
        document_codes = packed_codes(input_root / input_manifest["document_codes_file"], document_count)
        query_codes = packed_codes(input_root / input_manifest["query_codes_file"], 648)
        document_bits = numpy.unpackbits(document_codes, bitorder="little", axis=1)
        query_bits = numpy.unpackbits(query_codes, bitorder="little", axis=1)
        query_projections = numpy.fromfile(input_root / input_manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
        adc_centroids = numpy.fromfile(input_root / input_manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        documents, queries = numpy.asarray(data["documents"], dtype=numpy.float32), numpy.asarray(data["queries"], dtype=numpy.float32)
        for treatment in contract["treatments"]:
            treatment_id = treatment["id"]
            for budget in contract["implicit_cell_budgets"]:
                metadata = artifact_metadata(scale_id, treatment_id, budget, sha256(input_manifest_path), sha256(evaluation_manifest_path), sha256(train_path), itq_hash, data["dimension"])
                local_positions, codebooks, cells, _, artifact_path = create_artifact(output, metadata, treatment_id, train, train_bits, documents, document_bits)
                index = cell_index(cells)
                for fraction in contract["target_candidate_fractions"]:
                    target = int(math.ceil(fraction * document_count))
                    identifier = f"{treatment_id}-cells{budget}-target{target}"
                    config = {"schema_version": 1, "family": FAMILY, "scale": scale_id, "treatment": treatment, "implicit_cell_budget": budget, "block_count": metadata["block_count"], "target_candidate_fraction": fraction, "target_candidate_count": target, "input_manifest_sha256": sha256(input_manifest_path), "evaluation_manifest_sha256": sha256(evaluation_manifest_path), "train_vectors_sha256": sha256(train_path), "itq_artifact_sha256": itq_hash, "artifact_sha256": sha256(artifact_path), "cascade": contract["cascade"], "cell_traversal": contract["routing"], "candidate_union_rule": "document_position_ascending_v1"}
                    config_path, shortlist_path = output / "configs" / f"{identifier}.json", output / "shortlists" / f"{identifier}.json"
                    quality_path, contribution_path = output / "quality" / f"{identifier}.json", output / "contributions" / f"{identifier}.npz"
                    audit_path = output / "routing-audits" / f"{identifier}.json"
                    measurement_path = output / "measurements" / f"{identifier}.json"
                    if complete(config_path, config, shortlist_path, quality_path, contribution_path) and audit_path.is_file():
                        quality = json.loads(quality_path.read_text(encoding="utf-8"))
                        expected = {"scale": scale_id, "id": identifier, "treatment": treatment_id, "implicit_cell_budget": budget, "target_candidate_fraction": fraction, "target_candidate_count": target, "config_sha256": sha256(config_path), "artifact_sha256": sha256(artifact_path), "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "contribution_sha256": sha256(contribution_path), "routing_audit_sha256": sha256(audit_path), "e5_oracle_survival_after_adc": quality["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": quality["reranked_ndcg_at_10"]}
                        stored = None
                        if measurement_path.is_file():
                            try:
                                stored = json.loads(measurement_path.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError):
                                stored = None
                        row = stored if reusable_row(stored, expected) else prior.get((scale_id, identifier))
                        if reusable_row(row, expected):
                            measurement_path.parent.mkdir(parents=True, exist_ok=True)
                            measurement_path.write_bytes(canonical(row))
                            summary.append(row)
                            continue
                    config_path.parent.mkdir(parents=True, exist_ok=True); config_path.write_bytes(canonical(config))
                    rows: list[dict[str, Any]] = []; counts: list[float] = []; probes: list[float] = []; nonempty: list[float] = []; times: list[float] = []
                    audit_rows: list[dict[str, Any]] = []
                    for query_position in range(648):
                        local = local_costs(treatment_id, queries[query_position], query_bits[query_position], local_positions, codebooks)
                        candidates, visited, current_probes, current_nonempty, elapsed = route(local, index, target)
                        require(candidates.size >= 768, "product locator candidates below Hamming@768")
                        hamming = hamming_positions(document_codes, query_codes[query_position], candidates)
                        adc = adc_positions(document_bits, query_projections[query_position], adc_centroids, hamming)
                        rows.append({"query_position": query_position, "selected_cell_keys": visited, "hamming_shortlist_positions": hamming.tolist(), "binary_adc_positions": adc.tolist()})
                        audit_rows.append({"query_position": query_position, "selected_cell_keys": visited, "candidate_count": int(candidates.size), "target_candidate_count": target, "cell_probes": current_probes, "nonempty_cells": current_nonempty})
                        counts.append(float(candidates.size)); probes.append(float(current_probes)); nonempty.append(float(current_nonempty)); times.append(elapsed)
                    audit_path.parent.mkdir(parents=True, exist_ok=True); audit_path.write_bytes(canonical({"schema_version": 1, "family": FAMILY, "config_sha256": sha256(config_path), "artifact_sha256": sha256(artifact_path), "rows": audit_rows}))
                    shortlist_path.parent.mkdir(parents=True, exist_ok=True); shortlist_path.write_bytes(canonical({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "data_dependent_product_locator", "input_manifest_sha256": sha256(input_manifest_path), "hamming_limit": 768, "config_sha256": sha256(config_path), "artifact_sha256": sha256(artifact_path), "rows": rows}))
                    measured = write_quality(data, shortlist_path, contribution_path, quality_path, output / "oracle.npz")
                    row = {"scale": scale_id, "id": identifier, "treatment": treatment_id, "implicit_cell_budget": budget, "target_candidate_fraction": fraction, "target_candidate_count": target, "status": "measured", "actual_candidate_fraction": float(numpy.mean(counts)) / document_count, "candidate_count_p95": percentile(counts, .95), "cell_probes_p50": percentile(probes, .50), "cell_probes_p95": percentile(probes, .95), "nonempty_cells_p50": percentile(nonempty, .50), "nonempty_cells_p95": percentile(nonempty, .95), "routing_p50_ms_per_query": percentile(times, .50), "routing_p95_ms_per_query": percentile(times, .95), "config_sha256": sha256(config_path), "artifact_sha256": sha256(artifact_path), "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "contribution_sha256": sha256(contribution_path), "routing_audit_sha256": sha256(audit_path), "e5_oracle_survival_after_adc": measured["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": measured["reranked_ndcg_at_10"]}
                    measurement_path.parent.mkdir(parents=True, exist_ok=True)
                    measurement_path.write_bytes(canonical(row))
                    summary.append(row)
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "itq_artifact_sha256": sha256(args.itq_artifact), "rows": summary}))


def self_test() -> None:
    contract = load_contract(THIS / "data-dependent-product-locator.example.json")
    require(sum(len(contract["treatments"]) * len(contract["implicit_cell_budgets"]) * len(contract["target_candidate_fractions"]) for _ in contract["scales"]) == 54, "product locator matrix differs")
    costs = [numpy.asarray([0., 0., 1., 1.]), numpy.asarray([0., 1., 1., 2.])]
    sequence = [key for _, key in best_first_cells(costs)]
    require(sequence == [0, 4, 1, 2, 5, 6, 8, 12, 3, 7, 9, 10, 13, 14, 11, 15], "product locator lexical cell tie rule differs")
    source = numpy.asarray([[0, 0, 0, 0], [0, 0, 1, 1], [1, 1, 1, 1], [1, 1, 0, 0]], dtype=numpy.uint8)
    medoids = binary_medoids(source)
    require(medoids.shape == (4, 4) and all(any(numpy.array_equal(item, row) for row in source) for item in medoids), "product locator medoids are not real train codes")
    vectors = numpy.asarray([[0., 0., 1., 1.], [0., 1., 1., 0.], [1., 0., 0., 1.], [1., 1., 0., 0.]], dtype=numpy.float32)
    local_blocks = [numpy.asarray([0, 1], dtype=numpy.int16), numpy.asarray([2, 3], dtype=numpy.int16)]
    centers = [numpy.asarray([[0., 0.], [0., 1.], [1., 0.], [1., 1.]], dtype=numpy.float32), numpy.asarray([[0., 0.], [0., 1.], [1., 0.], [1., 1.]], dtype=numpy.float32)]
    assigned = float_assign(vectors, local_blocks, centers)
    index = cell_index(assigned)
    routed, visited, probe_count, nonempty, _ = route(local_float_costs(vectors[0], local_blocks, centers), index, 2)
    require(routed.size >= 2 and visited and probe_count >= nonempty >= 1, "product locator float routing differs")
    require(routing_rule("local_binary_medoids") == contract["routing_by_treatment"]["local_binary_medoids"] and routing_rule("float_e5_product") == contract["routing_by_treatment"]["float_e5_product"], "product locator routing amendment differs")
    expected = {"scale": "test", "id": "test", "treatment": "float_e5_product"}
    measured = {**expected, "status": "measured", "actual_candidate_fraction": .05, "candidate_count_p95": 1.0, "cell_probes_p50": 1.0, "cell_probes_p95": 1.0, "nonempty_cells_p50": 1.0, "nonempty_cells_p95": 1.0, "routing_p50_ms_per_query": 1.0, "routing_p95_ms_per_query": 1.0, "e5_oracle_survival_after_adc": .5, "reranked_ndcg_at_10": .5}
    require(reusable_row(measured, expected) and not reusable_row({**measured, "status": "reused_complete"}, expected), "product locator resume row differs")
    print("data-dependent product locator runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "data-dependent-product-locator.example.json")
    parser.add_argument("--scale-root", type=Path)
    parser.add_argument("--itq-artifact", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.scale_root is None or args.itq_artifact is None or args.output_root is None:
            parser.error("--scale-root, --itq-artifact, and --output-root are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, evaluator.EvaluationError) as error:
        print(f"run-data-dependent-product-locator: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
