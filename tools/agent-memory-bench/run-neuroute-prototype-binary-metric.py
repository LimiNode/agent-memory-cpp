#!/usr/bin/env python3
"""Measure a teacher-ranked shared binary metric over K8 prototypes.

This is an offline geometry ceiling.  It deliberately does not claim a native
router or an MIH implementation: every candidate is scored by exhaustive
XOR+popcount before optional address deduplication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent
POPCOUNT = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_prototype_binary_metric" and
            value.get("widths") == [16, 24, 32, 48, 64],
            "prototype-binary contract differs")
    require(value["teacher_top_k"] > max(value["negative_ranks"]),
            "teacher top-k does not contain all negatives")
    require(value["positive_count"] <= min(value["negative_ranks"]),
            "positive and negative ranks overlap")
    return value


def _top_indices(scores: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(scores))
    if count == len(scores):
        candidates = np.arange(len(scores), dtype=np.int64)
    else:
        candidates = np.argpartition(-scores, count - 1)[:count]
    order = np.lexsort((candidates, -scores[candidates]))
    return candidates[order].astype(np.int64, copy=False)


def teacher_rankings(queries: np.ndarray, prototypes: np.ndarray,
                    top_k: int) -> tuple[np.ndarray, float]:
    """Build the frozen float K8 prototype ranking used as supervision."""
    started = time.perf_counter()
    result = np.empty((len(queries), top_k), dtype=np.int32)
    # A query-at-a-time loop bounds the temporary score vector to ~2 MiB and
    # also works with mmap-backed prototype records.
    for row, query in enumerate(queries):
        result[row] = _top_indices(np.asarray(prototypes @ query,
                                              dtype=np.float32), top_k)
    return result, (time.perf_counter() - started) * 1000.0


def pairwise_training_matrix(queries: np.ndarray, prototypes: np.ndarray,
                             rankings: np.ndarray, train_count: int,
                             positive_count: int,
                             negative_ranks: list[int]) -> tuple[np.ndarray, dict[str, int]]:
    """Create a Hamming-aware query/prototype alignment matrix.

    For each teacher pair (q, p+) and hard negative p-, the quadratic score
    (w*q)(w*(p+ - p-)) is maximised.  The leading eigenvectors provide shared
    hyperplanes; unlike the earlier positive-pair low-variance screen, the
    supervision explicitly contains ranked negatives.
    """
    train_count = min(train_count, len(queries))
    q_rows = np.repeat(queries[:train_count],
                       positive_count * len(negative_ranks), axis=0)
    deltas: list[np.ndarray] = []
    pair_count = 0
    for query_index in range(train_count):
        positives = rankings[query_index, :positive_count]
        for positive in positives:
            for rank in negative_ranks:
                negative = int(rankings[query_index, rank])
                deltas.append(prototypes[int(positive)] - prototypes[negative])
                pair_count += 1
    delta_rows = np.asarray(deltas, dtype=np.float32)
    require(len(delta_rows) == len(q_rows) and len(q_rows) > 0,
            "teacher pair construction is empty")
    # q.T @ delta is the sum of q outer delta.  Symmetrisation makes the
    # eigendecomposition deterministic and keeps the objective explicit.
    matrix = (q_rows.T @ delta_rows + delta_rows.T @ q_rows) / (2.0 * len(q_rows))
    return matrix.astype(np.float64), {"queries": train_count,
        "pairs": pair_count, "positives": positive_count,
        "negative_ranks": len(negative_ranks)}


def directions_from_matrix(matrix: np.ndarray, width: int) -> np.ndarray:
    values, vectors = np.linalg.eigh(matrix)
    order = np.argsort(values)[::-1]
    directions = vectors[:, order[:min(width, vectors.shape[0])]].T
    # Fix each eigenvector sign so serialized codes are stable across LAPACK
    # implementations that choose the opposite representative.
    for row in directions:
        pivot = int(np.argmax(np.abs(row)))
        if row[pivot] < 0.0:
            row *= -1.0
    return directions.astype(np.float32)


def encode(values: np.ndarray, directions: np.ndarray,
           thresholds: np.ndarray) -> np.ndarray:
    bits = np.empty((len(values), len(directions)), dtype=np.bool_)
    for first in range(0, len(values), 32768):
        stop = min(first + 32768, len(values))
        projected = np.asarray(values[first:stop] @ directions.T,
                               dtype=np.float32)
        bits[first:stop] = projected >= thresholds[None, :]
    return np.packbits(bits, axis=1, bitorder="little")


def hamming_to_query(codes: np.ndarray, query_code: np.ndarray) -> np.ndarray:
    return POPCOUNT[np.bitwise_xor(codes, query_code[None, :])].sum(
        axis=1, dtype=np.uint16)


def entropy(codes: np.ndarray, width: int) -> float:
    bits = np.unpackbits(codes, axis=1, bitorder="little")[:, :width]
    probability = np.mean(bits, axis=0)
    terms = np.where((probability > 0.0) & (probability < 1.0),
                     -(probability * np.log2(probability) +
                       (1.0 - probability) * np.log2(1.0 - probability)), 0.0)
    return float(np.mean(terms))


def evaluate_width(queries: np.ndarray, prototypes: np.ndarray,
                   teacher: np.ndarray, width: int, contract: dict[str, Any],
                   train_count: int, frozen_codes: np.ndarray | None,
                   frozen_query_codes: np.ndarray | None
                   ) -> dict[str, Any]:
    matrix, training = pairwise_training_matrix(
        queries, prototypes, teacher, train_count,
        int(contract["positive_count"]),
        [int(v) for v in contract["negative_ranks"]])
    directions = directions_from_matrix(matrix, width)
    train_values = np.concatenate((queries[:train_count],
                                   prototypes[teacher[:train_count, :int(
                                       contract["positive_count"])]].reshape(-1,
                                       prototypes.shape[1])))
    thresholds = np.median(train_values @ directions.T, axis=0).astype(np.float32)
    query_codes = encode(queries, directions, thresholds)
    prototype_codes = encode(prototypes, directions, thresholds)
    rows: dict[str, Any] = {"method": "teacher_ranked_pairwise_alignment",
        "width": width, "code_bytes_per_prototype": (width + 7) // 8,
        "mean_bit_entropy": entropy(prototype_codes, width),
        "training": training, "partitions": {}}
    for name, begin, end in (("configuration", 0, train_count),
                             ("internal", train_count, len(queries))):
        if begin == end:
            continue
        partition_rows: dict[str, Any] = {"queries": end - begin,
            "budgets": {}}
        for budget in contract["address_budgets"]:
            budget = min(int(budget), len(prototypes))
            recalls: list[float] = []
            radii: list[float] = []
            elapsed: list[float] = []
            for query_index in range(begin, end):
                started = time.perf_counter()
                distances = hamming_to_query(prototype_codes,
                                              query_codes[query_index])
                selected = _top_indices(-distances.astype(np.float32), budget)
                elapsed.append((time.perf_counter() - started) * 1000.0)
                teacher_set = set(int(v) for v in teacher[query_index])
                recalls.append(sum(int(v) in teacher_set for v in selected) /
                               len(teacher_set))
                radii.append(float(np.max(distances[selected])))
            partition_rows["budgets"][str(budget)] = {
                "teacher_prototype_recall_at_k": float(np.mean(recalls)),
                "worst_query_recall_at_k": float(np.min(recalls)),
                "mean_hamming_radius": float(np.mean(radii)),
                "p95_hamming_radius": float(np.quantile(radii, .95)),
                "scan_ms": {"median": float(np.median(elapsed)),
                             "p95": float(np.quantile(elapsed, .95))}}
        rows["partitions"][name] = partition_rows
    if (frozen_codes is not None and frozen_query_codes is not None and
            width <= frozen_codes.shape[1] * 8 and
            frozen_query_codes.shape[1] >= (width + 7) // 8):
        frozen_bytes = (width + 7) // 8
        rows["frozen_control"] = {"method": "frozen_prototype_code_prefix",
            "width": width, "code_bytes_per_prototype": frozen_bytes,
            "mean_bit_entropy": entropy(frozen_codes[:, :frozen_bytes], width),
            "partitions": {}}
        control = frozen_codes[:, :frozen_bytes]
        for name, begin, end in (("configuration", 0, train_count),
                                 ("internal", train_count, len(queries))):
            if begin == end:
                continue
            recalls_by_budget: dict[str, float] = {}
            for budget in contract["address_budgets"]:
                budget = min(int(budget), len(prototypes))
                recalls = []
                for query_index in range(begin, end):
                    distances = hamming_to_query(
                        control, frozen_query_codes[query_index,
                                                    :frozen_bytes])
                    selected = _top_indices(-distances.astype(np.float32), budget)
                    teacher_set = set(int(v) for v in teacher[query_index])
                    recalls.append(sum(int(v) in teacher_set for v in selected) /
                                   len(teacher_set))
                recalls_by_budget[str(budget)] = float(np.mean(recalls))
            rows["frozen_control"]["partitions"][name] = {
                "teacher_prototype_recall_at_k": recalls_by_budget}
    return rows


def synthetic() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(282)
    prototypes = rng.normal(size=(2048, 16)).astype(np.float32)
    prototypes /= np.linalg.norm(prototypes, axis=1, keepdims=True)
    queries = prototypes[:32] + .03 * rng.normal(size=(32, 16)).astype(np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    return {"queries": queries, "prototype_vectors": prototypes,
            "prototype_codes": np.packbits(prototypes >= 0, axis=1,
                                             bitorder="little")}


def evaluate(npz: Any, contract: dict[str, Any]) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    require({"queries", "prototype_vectors"}.issubset(files),
            "prototype-binary input arrays are missing")
    queries = np.asarray(npz["queries"], dtype=np.float32)
    prototypes = np.asarray(npz["prototype_vectors"], dtype=np.float32)
    require(queries.ndim == prototypes.ndim == 2 and
            queries.shape[1] == prototypes.shape[1] and len(queries) >=
            int(contract["training"]["minimum_queries"]),
            "prototype-binary shapes differ")
    require(len(prototypes) >= int(contract["teacher_top_k"]),
            "prototype pool is smaller than teacher top-k")
    teacher = np.asarray(npz["teacher_top_prototypes"], dtype=np.int32) \
        if "teacher_top_prototypes" in files else None
    teacher_ms = 0.0
    if teacher is None:
        teacher, teacher_ms = teacher_rankings(queries, prototypes,
                                               int(contract["teacher_top_k"]))
    require(teacher.shape == (len(queries), int(contract["teacher_top_k"])),
            "teacher ranking shape differs")
    require(np.all((teacher >= 0) & (teacher < len(prototypes))),
            "teacher ranking contains an invalid prototype")
    train_count = len(queries) // 2
    frozen = np.asarray(npz["prototype_codes"], dtype=np.uint8) \
        if "prototype_codes" in files else None
    frozen_query = np.asarray(npz["query_codes"], dtype=np.uint8) \
        if "query_codes" in files else None
    if frozen is not None:
        require(frozen.shape[0] == len(prototypes),
                "frozen prototype code shape differs")
    if frozen_query is not None:
        require(frozen_query.shape[0] == len(queries),
                "frozen query code shape differs")
    result: dict[str, Any] = {"schema_version": 1,
        "family": contract["family"], "prototype_count": len(prototypes),
        "query_count": len(queries), "dimension": prototypes.shape[1],
        "teacher_top_k": int(contract["teacher_top_k"]),
        "teacher_build_ms": teacher_ms, "train_query_count": train_count,
        "widths": {}}
    for width in contract["widths"]:
        result["widths"][str(width)] = evaluate_width(
            queries, prototypes, teacher, int(width), contract, train_count,
            frozen, frozen_query)
    result["decision"] = {"native_mih_licensed": False,
        "production_selection_licensed": False,
        "extend_to_128_256": "conditional_on_held_out_gain_and_unsaturated_64",
        "reason": "exhaustive diagnostic ceiling requires full cascade replay"}
    return result


def self_test() -> int:
    contract = load_contract(THIS / "neuroute-prototype-binary-metric.example.json")
    result = evaluate(synthetic(), contract)
    require(set(result["widths"]) == {"16", "24", "32", "48", "64"},
            "prototype-binary widths missing")
    require(result["decision"]["native_mih_licensed"] is False,
            "prototype-binary gate opened")
    print("NeuRoute prototype-binary metric runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prototype-binary-metric.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test()
        require(args.input is not None and args.output is not None,
                "run requires --input and --output")
        contract = load_contract(args.contract)
        with np.load(args.input, mmap_mode="r", allow_pickle=False) as npz:
            result = evaluate(npz, contract)
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-prototype-binary-metric: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
