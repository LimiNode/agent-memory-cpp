#!/usr/bin/env python3
"""Measure semantic-direction affinity ceilings and THQ locality.

The experiment separates continuous affinity scoring from thermometer-code
locality.  Direction rows are either spherical k-means centroids or seeded
Gaussian random directions.  THQ names always denote the number of ordinal
levels; consequently THQ-L has L-1 thermometer bits per coordinate.
"""
from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import faiss
import numpy as np

POPCOUNT = np.asarray([value.bit_count() for value in range(256)], dtype=np.uint8)


def close_memmap(array: np.memmap | None) -> None:
    """Release a Windows memory-mapped file before deleting its path."""
    if array is not None:
        mapping = getattr(array, "_mmap", None)
        if mapping is not None:
            mapping.close()


def ndcg(predicted: np.ndarray, qrel_ids: np.ndarray,
         qrel_scores: np.ndarray) -> float:
    grades = {int(identifier): float(score)
              for identifier, score in zip(qrel_ids, qrel_scores)
              if int(identifier) >= 0}
    gains = np.asarray([grades.get(int(identifier), 0.0)
                        for identifier in predicted[:10]], dtype=np.float64)
    discounts = 1.0 / np.log2(np.arange(2, 12, dtype=np.float64))
    ideal = np.sort(qrel_scores.astype(np.float64))[::-1][:10]
    denominator = float(np.sum(ideal * discounts))
    return float(np.sum(gains * discounts) / denominator) if denominator else 0.0


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int,
            descending: bool) -> np.ndarray:
    limit = min(limit, len(scores))
    key = -scores if descending else scores
    if len(scores) <= limit:
        order = np.lexsort((ids, key))
        return ids[order]
    selected = np.argpartition(key, limit - 1)[:limit]
    selected_ids = ids[selected]
    order = np.lexsort((selected_ids, key[selected]))
    return selected_ids[order]


def decode_paths(manifest_path: Path) -> tuple[dict, Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest, manifest_path.parent


def path_from(row: dict, root: Path) -> Path:
    value = Path(row["path"])
    return value if value.is_absolute() else (root / value).resolve()


def pack_affinity(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """Pack landmark-major thermometer bits for explicit thresholds."""
    flags = values[:, :, None] > thresholds[None, :, :]
    return np.packbits(flags.reshape(len(values), -1), axis=1,
                       bitorder="little")


def hamming_top_and_ranks(
        codes: np.ndarray, query_code: np.ndarray, limit: int, chunk: int,
        teacher_ids: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """Return stable top IDs and optional stable 1-based teacher ranks."""
    all_ids = np.arange(len(codes), dtype=np.int64)
    best_dist = np.empty(limit, dtype=np.uint16)
    best_ids = np.empty(limit, dtype=np.int64)
    filled = 0
    target_dist = None
    ranks = np.asarray([], dtype=np.int64)
    if teacher_ids is not None:
        teacher_ids = np.asarray(teacher_ids, dtype=np.int64)
        target_dist = POPCOUNT[
            np.bitwise_xor(codes[teacher_ids], query_code)
        ].sum(axis=1, dtype=np.uint16)
        ranks = np.ones(len(teacher_ids), dtype=np.int64)
    for first in range(0, len(codes), chunk):
        stop = min(len(codes), first + chunk)
        distances = POPCOUNT[
            np.bitwise_xor(codes[first:stop], query_code)
        ].sum(axis=1, dtype=np.uint16)
        ids = all_ids[first:stop]
        if target_dist is not None and teacher_ids is not None:
            for position, distance in enumerate(target_dist):
                ranks[position] += int(np.count_nonzero(
                    (distances < distance) |
                    ((distances == distance) &
                     (ids < teacher_ids[position]))))
        take = min(limit, len(distances))
        local = np.argpartition(distances, take - 1)[:take]
        local_ids = ids[local]
        local_dist = distances[local]
        if filled:
            candidate_ids = np.concatenate((best_ids[:filled], local_ids))
            candidate_dist = np.concatenate((best_dist[:filled], local_dist))
        else:
            candidate_ids, candidate_dist = local_ids, local_dist
        order = np.lexsort((candidate_ids, candidate_dist))[:limit]
        filled = min(limit, len(order))
        best_ids[:filled] = candidate_ids[order]
        best_dist[:filled] = candidate_dist[order]
    return best_ids[:filled], ranks


def scalar_summary(values: list[float]) -> dict:
    array = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(array.mean()),
        "p05": float(np.quantile(array, .05)),
        "p50": float(np.quantile(array, .50)),
        "p95": float(np.quantile(array, .95)),
        "worst": float(array.min()),
    }


def summarize(rows: list[dict], budgets: tuple[int, ...]) -> dict:
    result: dict[str, dict] = {}
    for budget in budgets:
        selected = [row for row in rows if row["budget"] == budget]
        result[str(budget)] = {
            metric: scalar_summary([row[metric] for row in selected])
            for metric in ("teacher_survival", "ndcg_at_10", "query_ms")
        }
        survival = [row["teacher_survival"] for row in selected]
        result[str(budget)]["full_10_fraction"] = float(
            np.mean(np.asarray(survival) == 1.0))
    return result


def summarize_ranks(rows: list[np.ndarray]) -> dict:
    if not rows:
        return {}
    values = np.concatenate(rows).astype(np.float64)
    return {
        "r50": float(np.quantile(values, .50)),
        "r95": float(np.quantile(values, .95)),
        "r99": float(np.quantile(values, .99)),
        "worst": float(values.max()),
    }


def make_directions(family: str, documents: np.ndarray, dimension: int,
                    count: int, iterations: int, seed: int) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    if family == "kmeans":
        kmeans = faiss.Kmeans(dimension, count, niter=iterations, nredo=1,
                              seed=seed + count, spherical=True,
                              verbose=False, gpu=False)
        kmeans.train(documents)
        directions = np.asarray(kmeans.centroids, dtype=np.float32).copy()
    elif family == "gaussian":
        rng = np.random.default_rng(seed + count)
        directions = rng.normal(size=(count, dimension)).astype(np.float32)
        directions /= np.maximum(np.linalg.norm(directions, axis=1,
                                                 keepdims=True), 1.0e-12)
    else:
        raise ValueError(f"unsupported direction family: {family}")
    return directions, (time.perf_counter() - started) * 1000.0


def oracle_vectors(directions: np.ndarray, query_affinity: np.ndarray,
                   metrics: tuple[str, ...]) -> dict[str, np.ndarray]:
    vectors: dict[str, np.ndarray] = {}
    if "naive_dot" in metrics or "cosine" in metrics:
        vectors["naive_dot"] = query_affinity
    if "gram_whitened" in metrics:
        gram = directions @ directions.T
        inverse = np.linalg.pinv(gram, rcond=1.0e-6).astype(np.float32)
        vectors["gram_whitened"] = query_affinity @ inverse.T
    return vectors


def evaluate_oracles(
        affinity_docs: np.ndarray, query_affinity: np.ndarray,
        directions: np.ndarray, metrics: tuple[str, ...], ids: np.ndarray,
        teacher: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray,
        budgets: tuple[int, ...], chunk: int) -> dict:
    vectors = oracle_vectors(directions, query_affinity, metrics)
    needs_cosine = "cosine" in metrics
    document_norms = None
    if needs_cosine:
        document_norms = np.memmap(
            Path(affinity_docs.filename).with_suffix(".norms.f32"),
            mode="w+", dtype="<f4", shape=(len(affinity_docs),))
        for first in range(0, len(affinity_docs), chunk):
            stop = min(len(affinity_docs), first + chunk)
            values = np.asarray(affinity_docs[first:stop])
            document_norms[first:stop] = np.linalg.norm(values, axis=1)
        document_norms.flush()
    rows: dict[str, list[dict]] = {metric: [] for metric in metrics}
    max_budget = max(budgets)
    for query_index in range(len(query_affinity)):
        for metric in metrics:
            started = time.perf_counter()
            scores = np.empty(len(affinity_docs), dtype=np.float32)
            vector = vectors["naive_dot" if metric == "cosine" else metric][query_index]
            query_norm = max(float(np.linalg.norm(query_affinity[query_index])),
                             1.0e-12)
            for first in range(0, len(affinity_docs), chunk):
                stop = min(len(affinity_docs), first + chunk)
                scores[first:stop] = np.asarray(
                    affinity_docs[first:stop]) @ vector
                if metric == "cosine" and document_norms is not None:
                    scores[first:stop] /= np.maximum(
                        np.asarray(document_norms[first:stop]) * query_norm,
                        1.0e-12)
            ranked = top_ids(scores, ids, max_budget, True)
            query_ms = (time.perf_counter() - started) * 1000.0
            for budget in budgets:
                selected = ranked[:budget]
                rows[metric].append({
                    "query": query_index,
                    "budget": budget,
                    "teacher_survival": float(
                        np.isin(teacher[query_index], selected).sum()) / 10.0,
                    "ndcg_at_10": ndcg(selected, qrel_ids[query_index],
                                       qrel_scores[query_index]),
                    "query_ms": query_ms,
                })
    result = {metric: summarize(metric_rows, budgets)
              for metric, metric_rows in rows.items()}
    if document_norms is not None:
        norm_path = Path(document_norms.filename)
        close_memmap(document_norms)
        del document_norms
        gc.collect()
        norm_path.unlink()
    return result


def run(args: argparse.Namespace) -> dict:
    manifest, root = decode_paths(args.manifest)
    total_documents = int(manifest["documents"])
    count = min(total_documents, args.max_documents or total_documents)
    total_queries = int(manifest["queries"])
    query_count = min(total_queries, args.max_queries or total_queries)
    dimension = int(manifest["dimension"])
    references = manifest["references"]
    documents = np.memmap(
        path_from(references["document_vectors"], root), mode="r", dtype="<f4",
        shape=(total_documents, dimension))
    queries = np.fromfile(path_from(references["queries"], root),
                          dtype="<f4").reshape(-1, dimension)[:query_count]
    teacher = np.fromfile(path_from(references["teacher_ids"], root),
                          dtype="<i8").reshape(-1, 10)[:query_count]
    qrel_ids = np.fromfile(path_from(references["qrel_ids"], root),
                           dtype="<i8").reshape(-1, 20)[:query_count]
    qrel_scores = np.fromfile(path_from(references["qrel_scores"], root),
                              dtype="<f4").reshape(-1, 20)[:query_count]
    if np.any(teacher >= count):
        raise ValueError("teacher IDs fall outside --max-documents; use the full fixture")
    train_count = min(args.train_documents, count)
    training = np.asarray(documents[:train_count], dtype=np.float32)
    ids = np.arange(count, dtype=np.int64)
    budgets = tuple(sorted(set(args.budgets)))
    output = {
        "schema_version": 2,
        "family": "semantic_direction_affinity_thq_v2",
        "documents": count,
        "queries": query_count,
        "dimension": dimension,
        "train_documents": train_count,
        "directions": {},
        "protocol": {
            "direction_families": list(args.direction_families),
            "affinity": "direction_inner_product",
            "oracle_metrics": list(args.oracle_metrics),
            "quantization": "per-direction training-prefix quantile thermometer",
            "naming": "THQ-L means L ordinal levels and L-1 thermometer bits per coordinate",
            "teacher": "frozen exact E5 top-10",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for family in args.direction_families:
        family_output: dict[str, dict] = {}
        for direction_count in args.landmarks:
            directions, build_ms = make_directions(
                family, training, dimension, direction_count,
                args.kmeans_iterations, args.seed)
            train_affinity = training @ directions.T
            query_affinity = np.asarray(queries, dtype=np.float32) @ directions.T
            stem = f"{args.output.stem}-{family}-m{direction_count}"
            affinity_path = args.output.with_name(stem + "-affinity.f32")
            affinity_docs = np.memmap(
                affinity_path, mode="w+", dtype="<f4",
                shape=(count, direction_count))
            for first in range(0, count, args.chunk):
                stop = min(count, first + args.chunk)
                affinity_docs[first:stop] = np.asarray(
                    documents[first:stop]) @ directions.T
            affinity_docs.flush()
            entry: dict = {
                "direction_build_ms": build_ms,
                "direction_bytes": int(directions.nbytes),
                "fp32_affinity_bytes_per_document": direction_count * 4,
                "oracles": evaluate_oracles(
                    affinity_docs, query_affinity, directions,
                    args.oracle_metrics, ids, teacher, qrel_ids, qrel_scores,
                    budgets, args.chunk),
                "thq": {},
            }
            for levels in args.levels:
                threshold_count = levels - 1
                thresholds = np.quantile(
                    train_affinity,
                    np.arange(1, levels, dtype=np.float32) / levels,
                    axis=0).T.astype(np.float32)
                code_bytes = (direction_count * threshold_count + 7) // 8
                codes_path = args.output.with_name(
                    stem + f"-thq{levels}.u8")
                codes = np.memmap(codes_path, mode="w+", dtype=np.uint8,
                                  shape=(count, code_bytes))
                for first in range(0, count, args.chunk):
                    stop = min(count, first + args.chunk)
                    codes[first:stop] = pack_affinity(
                        np.asarray(affinity_docs[first:stop]), thresholds)
                codes.flush()
                query_codes = pack_affinity(query_affinity, thresholds)
                rows: list[dict] = []
                rank_rows: list[np.ndarray] = []
                started = time.perf_counter()
                for query_index in range(query_count):
                    query_started = time.perf_counter()
                    selected, ranks = hamming_top_and_ranks(
                        codes, query_codes[query_index], max(budgets),
                        args.chunk,
                        None if args.skip_ranks else teacher[query_index])
                    query_ms = (time.perf_counter() - query_started) * 1000.0
                    if len(ranks):
                        rank_rows.append(ranks)
                    for budget in budgets:
                        chosen = selected[:budget]
                        rows.append({
                            "query": query_index,
                            "budget": budget,
                            "teacher_survival": float(np.isin(
                                teacher[query_index], chosen).sum()) / 10.0,
                            "ndcg_at_10": ndcg(
                                chosen, qrel_ids[query_index],
                                qrel_scores[query_index]),
                            "query_ms": query_ms,
                        })
                entry["thq"][str(levels)] = {
                    "levels": levels,
                    "thresholds_per_coordinate": threshold_count,
                    "thermometer_bits_per_coordinate": threshold_count,
                    "bytes_per_document": code_bytes,
                    "scan_total_ms": (time.perf_counter() - started) * 1000.0,
                    "summary": summarize(rows, budgets),
                    "rank_summary": summarize_ranks(rank_rows),
                    "thresholds": "training_prefix_quantiles",
                    "code_path": str(codes_path.resolve()),
                }
                close_memmap(codes)
                del codes
                gc.collect()
                if not args.keep_codes:
                    codes_path.unlink()
            close_memmap(affinity_docs)
            del affinity_docs
            gc.collect()
            if not args.keep_codes:
                affinity_path.unlink()
            family_output[str(direction_count)] = entry
        output["directions"][family] = family_output
    close_memmap(documents)
    args.output.write_text(json.dumps(output, indent=2) + "\n",
                           encoding="utf-8")
    return output


def csv_ints(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in value.split(",") if item)


def csv_strings(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--direction-families", default="kmeans,gaussian")
    parser.add_argument("--landmarks", default="32,64,128,256")
    parser.add_argument("--levels", default="3,4,5")
    parser.add_argument("--oracle-metrics",
                        default="naive_dot,cosine,gram_whitened")
    parser.add_argument("--budgets", default="256,1000,5000,10000")
    parser.add_argument("--train-documents", type=int, default=100000)
    parser.add_argument("--max-documents", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--chunk", type=int, default=20000)
    parser.add_argument("--kmeans-iterations", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--keep-codes", action="store_true")
    parser.add_argument("--skip-ranks", action="store_true")
    args = parser.parse_args()
    args.direction_families = csv_strings(args.direction_families)
    args.landmarks = csv_ints(args.landmarks)
    args.levels = csv_ints(args.levels)
    args.oracle_metrics = csv_strings(args.oracle_metrics)
    args.budgets = csv_ints(args.budgets)
    if not all(level >= 2 for level in args.levels):
        parser.error("--levels values must be at least 2")
    supported_families = {"kmeans", "gaussian"}
    if not set(args.direction_families) <= supported_families:
        parser.error("unsupported --direction-families value")
    supported_metrics = {"naive_dot", "cosine", "gram_whitened"}
    if not set(args.oracle_metrics) <= supported_metrics:
        parser.error("unsupported --oracle-metrics value")
    result = run(args)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
