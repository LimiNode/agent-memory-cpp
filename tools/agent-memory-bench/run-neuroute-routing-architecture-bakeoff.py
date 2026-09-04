#!/usr/bin/env python3
"""Common replay for document IVF and ordinal/router alternatives.

The input NPZ contains ``documents [N,D]``, ``queries [Q,D]`` and
``teacher_top10 [Q,10]``.  Optional ``teacher_gains [Q,N]`` supplies qrels;
without it, rank-derived gains are used and reported as a surrogate.  This is
an apples-to-apples routing ceiling study: every method returns document IDs,
then the same exact top-K evaluator measures quality.  The learned and LTHQ
lanes intentionally use exhaustive code scans as diagnostic controls; they do
not claim a production ANN implementation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

_lthq_path = Path(__file__).with_name("run-neuroute-lthq.py")
_lthq_spec = importlib.util.spec_from_file_location("neuroute_lthq_runner", _lthq_path)
_lthq_module = importlib.util.module_from_spec(_lthq_spec) if _lthq_spec and _lthq_spec.loader else None
if _lthq_module is not None:
    _lthq_spec.loader.exec_module(_lthq_module)
fit_lthq = None if _lthq_module is None else _lthq_module.fit_lthq


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def top(scores: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(scores))
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    ids = np.argpartition(-scores, count - 1)[:count]
    return ids[np.lexsort((ids, -scores[ids]))]


def kmeans(vectors: np.ndarray, count: int, seed: int,
           iterations: int = 12) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ids = rng.choice(len(vectors), size=count, replace=False)
    centers = np.asarray(vectors[ids], dtype=np.float32).copy()
    for _ in range(iterations):
        labels = np.argmax(vectors @ centers.T, axis=1)
        for cluster in range(count):
            members = vectors[labels == cluster]
            if len(members):
                center = members.mean(axis=0)
                norm = np.linalg.norm(center)
                centers[cluster] = center / norm if norm else center
    return centers


def lists(vectors: np.ndarray, centers: np.ndarray) -> list[np.ndarray]:
    labels = np.argmax(vectors @ centers.T, axis=1)
    order = np.argsort(labels, kind="stable")
    counts = np.bincount(labels, minlength=len(centers))
    offsets = np.concatenate(([0], np.cumsum(counts)))
    return [order[offsets[i]:offsets[i + 1]] for i in range(len(centers))]


def hamming(codes: np.ndarray, query_code: np.ndarray) -> np.ndarray:
    return np.unpackbits(np.bitwise_xor(codes, query_code[None, :]),
                         axis=1, bitorder="little").sum(axis=1,
                                                         dtype=np.int32)


def binary_projection(vectors: np.ndarray, bits: int, seed: int
                      ) -> tuple[np.ndarray, np.ndarray]:
    centered = vectors - vectors.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    rng = np.random.default_rng(seed + bits)
    projection = vt[:min(bits, vt.shape[0])].T
    if projection.shape[1] < bits:
        projection = np.concatenate((projection,
            rng.normal(size=(vectors.shape[1], bits - projection.shape[1]))),
                                    axis=1)
    return vectors.mean(axis=0).astype(np.float32), projection.astype(np.float32)


def binary_codes(vectors: np.ndarray, mean: np.ndarray,
                 projection: np.ndarray) -> np.ndarray:
    return np.packbits((vectors - mean) @ projection >= 0.0, axis=1,
                       bitorder="little")


def metrics(selected: np.ndarray, teacher: np.ndarray,
            gains: np.ndarray | None, query_index: int, k: int) -> tuple[float, float]:
    selected = selected[:k]
    target = teacher[query_index]
    overlap = float(len(set(map(int, selected)) & set(map(int, target)))) / 10.0
    if gains is None:
        relevance = {int(doc): 1.0 / np.log2(rank + 2)
                     for rank, doc in enumerate(target)}
        ideal = np.asarray(list(relevance.values()), dtype=np.float64)
    else:
        relevance = {int(doc): float(gains[query_index, int(doc)])
                     for doc in target}
        ideal = np.sort(gains[query_index])[::-1][:10]
    values = np.asarray([relevance.get(int(doc), 0.0) for doc in selected[:10]],
                        dtype=np.float64)
    discount = 1.0 / np.log2(np.arange(2, len(values) + 2))
    ideal_discount = 1.0 / np.log2(np.arange(2, len(ideal) + 2))
    denom = float(np.sum(ideal * ideal_discount))
    ndcg = float(np.sum(values * discount) / denom) if denom else 0.0
    return overlap, ndcg


def route_ivf(query: np.ndarray, vectors: np.ndarray, centers: np.ndarray,
              postings: list[np.ndarray], nprobe: int, budget: int,
              residual: bool = False) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    cells = top(centers @ query, nprobe)
    candidate_parts = [postings[int(cell)] for cell in cells]
    candidates = np.concatenate(candidate_parts)
    if residual:
        # Exact local residual scoring is equivalent to exact scoring within
        # the probed cells, but makes the intended K8/local-refinement step
        # explicit for the routing comparison.
        candidate_cells = np.concatenate([
            np.full(len(part), int(cell), dtype=np.int32)
            for cell, part in zip(cells, candidate_parts)])
        residuals = vectors[candidates] - centers[candidate_cells]
        scores = residuals @ query + centers[candidate_cells] @ query
    else:
        scores = vectors[candidates] @ query
    selected = candidates[top(scores, budget)]
    return selected, (time.perf_counter() - started) * 1000.0


def route_binary(query: np.ndarray, vectors: np.ndarray, codes: np.ndarray,
                 mean: np.ndarray, projection: np.ndarray, budget: int,
                 replication: int) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    qcode = binary_codes(query[None, :], mean, projection)[0]
    distances = hamming(codes, qcode)
    primary_budget = min(len(codes), max(1, budget * replication))
    primary = top(-distances.astype(np.float32), primary_budget)
    if replication <= 1:
        return primary, (time.perf_counter() - started) * 1000.0
    # Replication is modelled as an index expansion: a query may inspect a
    # wider deterministic Hamming neighbourhood for the same logical budget.
    # The returned pool is intentionally allowed to exceed ``budget`` so that
    # the quality/bytes trade-off is visible in the report.
    return np.asarray(primary, dtype=np.int64), (time.perf_counter() - started) * 1000.0


def run(args: argparse.Namespace) -> dict[str, Any]:
    archive = np.load(args.input, allow_pickle=False)
    required = ("documents", "queries", "teacher_top10")
    require(all(name in archive for name in required),
            "routing input must contain documents, queries, teacher_top10")
    documents = np.asarray(archive["documents"], dtype=np.float32)
    queries = np.asarray(archive["queries"], dtype=np.float32)
    teacher = np.asarray(archive["teacher_top10"], dtype=np.int64)
    gains = np.asarray(archive["teacher_gains"], dtype=np.float32) if "teacher_gains" in archive else None
    require(teacher.shape[0] == len(queries), "teacher query count differs")
    split = min(args.config_queries, len(queries))
    rows: list[dict[str, Any]] = []
    centers = kmeans(documents, args.nlist, args.seed)
    postings = lists(documents, centers)
    for nprobe in args.nprobe:
        for budget in args.budgets:
            for qi, query in enumerate(queries):
                selected, elapsed = route_ivf(query, documents, centers, postings, nprobe, budget)
                overlap, score = metrics(selected, teacher, gains, qi, budget)
                rows.append({"architecture": "direct_document_ivf", "partition": "config" if qi < split else "internal", "nlist": args.nlist, "nprobe": nprobe, "budget": budget, "query": qi, "overlap": overlap, "ndcg": score, "candidate_count": len(selected), "route_ms": elapsed, "payload_bytes_per_document": 4 * documents.shape[1]})
                selected, elapsed = route_ivf(query, documents, centers, postings, nprobe, budget, residual=True)
                overlap, score = metrics(selected, teacher, gains, qi, budget)
                rows.append({"architecture": "float_ivf_local_residual_k8", "partition": "config" if qi < split else "internal", "nlist": args.nlist, "nprobe": nprobe, "budget": budget, "query": qi, "overlap": overlap, "ndcg": score, "candidate_count": len(selected), "route_ms": elapsed, "payload_bytes_per_document": 4 * documents.shape[1]})
    for bits in args.bits:
        mean, projection = binary_projection(documents, bits, args.seed)
        codes = binary_codes(documents, mean, projection)
        payload = (bits + 7) // 8
        for replication in args.replication:
            for qi, query in enumerate(queries):
                selected, elapsed = route_binary(query, documents, codes, mean, projection, max(args.budgets), replication)
                overlap, score = metrics(selected, teacher, gains, qi, max(args.budgets))
                rows.append({"architecture": "learned_semantic_router_replication", "partition": "config" if qi < split else "internal", "bits": bits, "replication": replication, "budget": max(args.budgets), "query": qi, "overlap": overlap, "ndcg": score, "candidate_count": len(selected), "route_ms": elapsed, "payload_bytes_per_document": payload})
    ordinal_threshold_sets: list[tuple[str, int, np.ndarray]] = []
    for levels in args.ordinal_levels:
        ordinal_threshold_sets.append(("thq_quantile", levels,
            np.quantile(documents, np.arange(1, levels) / levels, axis=0).T.astype(np.float32)))
    train_names = ("train_vectors", "train_queries", "train_positive_ids", "train_negative_ids")
    if fit_lthq is not None and all(name in archive for name in train_names):
        train_vectors = np.asarray(archive["train_vectors"], dtype=np.float32)
        train_queries = np.asarray(archive["train_queries"], dtype=np.float32)
        learned_cache: dict[int, np.ndarray] = {}
        for levels in args.ordinal_levels:
            learned_cache[levels], _ = fit_lthq(
                train_vectors, train_queries, np.asarray(archive["train_positive_ids"]),
                np.asarray(archive["train_negative_ids"]), levels, args.seed + levels,
                candidate_quantiles=8, passes=1, margin=1.0)
            ordinal_threshold_sets.append(("lthq_t", levels, learned_cache[levels]))
    for ordinal_name, levels, thresholds in ordinal_threshold_sets:
        codes = np.packbits((documents[:, :, None] > thresholds[None, :, :]).reshape(len(documents), -1), axis=1, bitorder="little")
        for qi, query in enumerate(queries):
            qcode = np.packbits((query[None, :, None] > thresholds[None, :, :]).reshape(1, -1), axis=1, bitorder="little")[0]
            started = time.perf_counter(); selected = top(-hamming(codes, qcode).astype(np.float32), max(args.budgets)); elapsed = (time.perf_counter() - started) * 1000.0
            overlap, score = metrics(selected, teacher, gains, qi, max(args.budgets))
            rows.append({"architecture": "lthq_ordinal_router", "thresholds": ordinal_name, "levels": levels, "partition": "config" if qi < split else "internal", "budget": max(args.budgets), "query": qi, "overlap": overlap, "ndcg": score, "candidate_count": len(selected), "route_ms": elapsed, "payload_bytes_per_document": (documents.shape[1] * (levels - 1) + 7) // 8})
    summaries: list[dict[str, Any]] = []
    for key in sorted({tuple(sorted((k, v) for k, v in row.items() if k not in ("query", "overlap", "ndcg", "route_ms", "candidate_count", "partition"))) for row in rows}):
        selected_rows = [row for row in rows if tuple(sorted((k, v) for k, v in row.items() if k not in ("query", "overlap", "ndcg", "route_ms", "candidate_count", "partition"))) == key]
        for partition in ("config", "internal"):
            current = [row for row in selected_rows if row["partition"] == partition]
            if not current: continue
            summaries.append({**dict(key), "partition": partition, "query_count": len(current), "mean_overlap": float(np.mean([r["overlap"] for r in current])), "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)), "worst_overlap": float(np.min([r["overlap"] for r in current])), "mean_ndcg": float(np.mean([r["ndcg"] for r in current])), "worst_ndcg": float(np.min([r["ndcg"] for r in current])), "route_ms_p95": float(np.quantile([r["route_ms"] for r in current], .95)), "mean_candidate_count": float(np.mean([r["candidate_count"] for r in current])), "payload_bytes_per_document": current[0]["payload_bytes_per_document"]})
    return {"schema_version": 1, "family": "neuroute_routing_architecture_bakeoff", "raw_rows": rows, "summaries": summaries, "teacher_gains_present": gains is not None, "global_code_scans_are_diagnostic_only": True}


def smoke() -> None:
    rng = np.random.default_rng(4)
    docs = rng.normal(size=(96, 8)).astype(np.float32)
    queries = docs[:8] + rng.normal(scale=.01, size=(8, 8)).astype(np.float32)
    teacher = np.asarray([top(docs @ q, 10) for q in queries], dtype=np.int64)
    path = Path(".routing-smoke.npz")
    np.savez(path, documents=docs, queries=queries, teacher_top10=teacher)
    args = argparse.Namespace(input=path, nlist=8, nprobe=[2], budgets=[16], bits=[12], replication=[1, 2], ordinal_levels=[3], seed=3, config_queries=4)
    report = run(args)
    require(report["summaries"], "routing smoke produced no summaries")
    path.unlink(missing_ok=True)
    print("routing architecture bake-off self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--nprobe", default="8,16,32,64,128")
    parser.add_argument("--budgets", default="256,512,768,1024,2048")
    parser.add_argument("--bits", default="12,14,16")
    parser.add_argument("--replication", default="1,2,3,4")
    parser.add_argument("--ordinal-levels", default="3,4,5")
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--config-queries", type=int, default=76)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        smoke(); return 0
    require(args.input is not None and args.output is not None, "--input and --output are required")
    args.nprobe = [int(x) for x in args.nprobe.split(",")]
    args.budgets = [int(x) for x in args.budgets.split(",")]
    args.bits = [int(x) for x in args.bits.split(",")]
    args.replication = [int(x) for x in args.replication.split(",")]
    args.ordinal_levels = [int(x) for x in args.ordinal_levels.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
