#!/usr/bin/env python3
"""Compare E5-space IVF and THQ-space IVF followed by local THQ ranking."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import faiss
import numpy as np

POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)], dtype=np.uint8)


def decode_levels(codes: np.ndarray, bits: int) -> np.ndarray:
    unpacked = np.unpackbits(codes, axis=1, bitorder="little")
    return unpacked.reshape(len(codes), 384, bits).sum(axis=2).astype(np.float32)


def stable_top(values: np.ndarray, identifiers: np.ndarray, limit: int) -> np.ndarray:
    if len(values) <= limit:
        return identifiers[np.lexsort((identifiers, values))]
    selected = np.argpartition(values, limit - 1)[:limit]
    return identifiers[selected][np.lexsort((identifiers[selected], values[selected]))]


def ndcg(predicted: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(identifier): float(score) for identifier, score in zip(qrel_ids, qrel_scores)}
    gains = np.asarray([grades.get(int(identifier), 0.0) for identifier in predicted[:10]], dtype=np.float64)
    discounts = 1.0 / np.log2(np.arange(2, 12, dtype=np.float64))
    ideal = np.sort(qrel_scores.astype(np.float64))[::-1][:10]
    denominator = float(np.sum(ideal * discounts))
    return float(np.sum(gains * discounts) / denominator) if denominator else 0.0


def train_index(values: np.ndarray, nlist: int, train_limit: int,
                spherical: bool, seed: int) -> tuple[np.ndarray, np.ndarray, float]:
    started = time.perf_counter()
    train = np.asarray(values[:train_limit], dtype=np.float32)
    kmeans = faiss.Kmeans(train.shape[1], nlist, niter=15, nredo=1,
                          seed=seed, spherical=spherical, verbose=False, gpu=False)
    kmeans.train(train)
    centroids = np.asarray(kmeans.centroids, dtype=np.float32).copy()
    index = faiss.IndexFlatIP(train.shape[1]) if spherical else faiss.IndexFlatL2(train.shape[1])
    index.add(centroids)
    return centroids, index, (time.perf_counter() - started) * 1000.0


def assign(index: faiss.Index, values: np.ndarray, batch: int) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    result = np.empty(len(values), dtype=np.int32)
    for first in range(0, len(values), batch):
        stop = min(first + batch, len(values))
        _, ids = index.search(np.asarray(values[first:stop], dtype=np.float32), 1)
        result[first:stop] = ids[:, 0]
    return result, (time.perf_counter() - started) * 1000.0


def lists(assignments: np.ndarray, nlist: int) -> tuple[np.ndarray, np.ndarray]:
    positions = np.arange(len(assignments), dtype=np.int64)
    order = np.lexsort((positions, assignments))
    counts = np.bincount(assignments, minlength=nlist).astype(np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    return order, offsets


def run(args: argparse.Namespace) -> dict:
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(args.documents_count, 384))
    queries = np.fromfile(args.queries, dtype="<f4").reshape(args.query_count, 384)
    thq_codes = np.memmap(args.thq_codes, mode="r", dtype=np.uint8,
                          shape=(args.documents_count, args.thq_bytes))
    thq_queries = np.fromfile(args.thq_queries, dtype=np.uint8).reshape(args.query_count, args.thq_bytes)
    qrel_ids = np.fromfile(args.qrel_ids, dtype="<i8").reshape(args.query_count, 20)
    qrel_scores = np.fromfile(args.qrel_scores, dtype="<f4").reshape(args.query_count, 20)
    teacher = np.fromfile(args.teacher_ids, dtype="<i8").reshape(args.query_count, 10)
    # Decode only the training prefix and the full corpus in bounded chunks.
    train_levels = decode_levels(np.asarray(thq_codes[:args.train_limit]), args.thq_bits)
    all_levels = np.memmap(args.output.with_suffix(".levels.f32"), mode="w+",
                           dtype="<f4", shape=(args.documents_count, 384))
    for first in range(0, args.documents_count, args.batch):
        stop = min(first + args.batch, args.documents_count)
        all_levels[first:stop] = decode_levels(np.asarray(thq_codes[first:stop]), args.thq_bits)
    all_levels.flush()
    budgets = tuple(int(value) for value in args.budgets.split(","))
    outputs: dict[str, dict] = {}
    for family, values, spherical in (("e5_ivf", docs, True), ("thq_native_ivf", all_levels, False)):
        centroids, index, train_ms = train_index(values, args.nlist, args.train_limit, spherical, args.seed)
        assignments, assign_ms = assign(index, values, args.batch)
        order, offsets = lists(assignments, args.nlist)
        rows = []
        for qi, query in enumerate(queries):
            coarse_started = time.perf_counter()
            if family == "e5_ivf":
                coarse = centroids @ query
            else:
                query_levels = decode_levels(thq_queries[qi:qi + 1], args.thq_bits)[0]
                delta = centroids - query_levels[None, :]
                coarse = -np.einsum("ij,ij->i", delta, delta)
            ranked_cells = np.lexsort((np.arange(args.nlist), -coarse))
            cumulative = np.cumsum(offsets[ranked_cells + 1] - offsets[ranked_cells])
            coarse_ms = (time.perf_counter() - coarse_started) * 1000.0
            for budget in budgets:
                budget_started = time.perf_counter()
                probe_count = int(np.searchsorted(cumulative, budget, side="left") + 1)
                selected_cells = ranked_cells[:probe_count]
                parts = [order[offsets[cell]:offsets[cell + 1]] for cell in selected_cells]
                candidates = np.concatenate(parts).astype(np.int64, copy=False)
                xor = np.bitwise_xor(np.asarray(thq_codes[candidates]), thq_queries[qi])
                distances = POPCOUNT[xor].sum(axis=1, dtype=np.uint16)
                shortlist = stable_top(distances, candidates, 256)
                exact_scores = np.asarray(docs[shortlist], dtype=np.float32) @ query
                final = shortlist[np.lexsort((shortlist, -exact_scores))[:10]]
                survival = float(np.isin(teacher[qi], shortlist).sum()) / 10.0
                rows.append({"query": qi, "budget": budget, "candidate_count": int(len(candidates)),
                             "nprobe": probe_count, "teacher_survival": survival,
                             "ndcg_at_10": ndcg(final, qrel_ids[qi], qrel_scores[qi]),
                             "coarse_ms": coarse_ms,
                             "query_ms": coarse_ms + (time.perf_counter() - budget_started) * 1000.0})
        outputs[family] = {"nlist": args.nlist, "train_limit": args.train_limit,
                          "train_ms": train_ms, "assignment_ms": assign_ms,
                          "assignment_bytes": int(assignments.nbytes), "rows": rows}
        for budget in budgets:
            selected = [row for row in rows if row["budget"] == budget]
            for metric in ("candidate_count", "nprobe", "teacher_survival", "ndcg_at_10", "coarse_ms", "query_ms"):
                values_metric = np.asarray([row[metric] for row in selected], dtype=np.float64)
                outputs[family].setdefault("summary", {}).setdefault(str(budget), {})[metric] = {
                    "mean": float(values_metric.mean()), "p50": float(np.quantile(values_metric, .5)),
                    "p95": float(np.quantile(values_metric, .95)), "p99": float(np.quantile(values_metric, .99))}
            outputs[family]["summary"][str(budget)]["thq_bytes_per_document"] = args.thq_bytes
    result = {"schema_version": 1, "family": "thq_ivf_comparison_v1",
              "documents": args.documents_count, "queries": args.query_count,
              "dimension": 384, "thq_bits": args.thq_bits, "nlist": args.nlist,
              "budgets": budgets, "outputs": outputs,
              "protocol": {"e5_ivf": "spherical_kmeans_on_E5_then_local_THQ_Hamming",
                           "thq_native_ivf": "kmeans_on_THQ_ordinal_levels_then_local_THQ_Hamming",
                           "final_rerank": "exact_FP32_top10", "train_source": "document_prefix_only"}}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--thq-codes", type=Path, required=True)
    parser.add_argument("--thq-queries", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--documents-count", type=int, default=1_000_000)
    parser.add_argument("--query-count", type=int, default=152)
    parser.add_argument("--thq-bytes", type=int, default=144)
    parser.add_argument("--thq-bits", type=int, default=3)
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--train-limit", type=int, default=100_000)
    parser.add_argument("--batch", type=int, default=20_000)
    parser.add_argument("--budgets", default="20000,50000,100000")
    parser.add_argument("--seed", type=int, default=20260908)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run(args)["outputs"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
