#!/usr/bin/env python3
"""Benchmark the pinned standalone RaBitQ/BBQ-like research codecs.

Input is a NumPy NPZ containing ``vectors`` and ``queries``.  Optional
``targets`` is an integer matrix of teacher row IDs used for recall@K.  This
runner deliberately reports codec/search cost and storage; it does not claim a
production ANN implementation (the search is exhaustive over the encoded
rows, making the representation trade-off measurable in isolation).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from binary_code_references import BBQLikeReference, RabitQReference, format_manifest


def p95(values: list[float]) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), 0.95)) if values else 0.0


def evaluate(name: str, codec: object, vectors: np.ndarray, queries: np.ndarray, targets: np.ndarray | None, top_k: int) -> dict[str, object]:
    encode_ms: list[float] = []
    search_ms: list[float] = []
    recalls: list[float] = []
    overlaps: list[float] = []
    ndcgs: list[float] = []
    rerank_ms: list[float] = []
    add_ms: list[float] = []
    # Compute quality from a single batched score matrix.  The previous
    # per-query implementation was useful for smoke tests but made the frozen
    # 22k-document lane needlessly expensive.
    batch_scores = codec.scores_batch(queries)  # type: ignore[attr-defined]
    exact_scores = queries @ vectors.T
    exact_top10 = np.argpartition(-exact_scores, 9, axis=1)[:, :10]
    timing_count = min(64, queries.shape[0])
    for index, query in enumerate(queries):
        if index >= timing_count:
            break
        started = time.perf_counter()
        codec.encode_query(query)  # type: ignore[attr-defined]
        add_ms.append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        # scores() includes query transform and correction metadata application.
        codec.scores(query)  # type: ignore[attr-defined]
        encode_ms.append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        candidates = codec.search(query, top_k)  # type: ignore[attr-defined]
        search_ms.append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        candidate_scores = vectors[candidates] @ query
        reranked = candidates[np.argsort(-candidate_scores, kind="stable")[:10]]
        rerank_ms.append((time.perf_counter() - started) * 1000.0)
        oracle_top10 = exact_top10[index]
        overlaps.append(float(np.intersect1d(reranked, oracle_top10).size) / 10.0)
        gains = exact_scores[index, oracle_top10]
        rel = np.maximum(gains - gains[-1], 0.0)
        dcg = float(np.sum((vectors[reranked] @ query - gains[-1]).clip(min=0.0) / np.log2(np.arange(2, 12))))
        ideal = float(np.sum(rel / np.log2(np.arange(2, 12))))
        ndcgs.append(dcg / ideal if ideal > 0.0 else 1.0)
    # Re-run candidate selection from the batched approximate scores for all
    # queries, so quality is not estimated from the timing sample only.
    all_overlaps: list[float] = []
    all_ndcgs: list[float] = []
    count = min(batch_scores.shape[1], top_k * int(codec.oversample))  # type: ignore[attr-defined]
    for index in range(queries.shape[0]):
        candidates = np.argpartition(-batch_scores[index], count - 1)[:count]
        reranked = candidates[np.argsort(-exact_scores[index, candidates], kind="stable")[:10]]
        oracle_top10 = exact_top10[index]
        all_overlaps.append(float(np.intersect1d(reranked, oracle_top10).size) / 10.0)
        gains = exact_scores[index, oracle_top10]
        rel = np.maximum(gains - gains[-1], 0.0)
        dcg = float(np.sum(np.maximum(exact_scores[index, reranked] - gains[-1], 0.0) / np.log2(np.arange(2, 12))))
        ideal = float(np.sum(rel / np.log2(np.arange(2, 12))))
        all_ndcgs.append(dcg / ideal if ideal > 0.0 else 1.0)
        if targets is not None:
            expected = set(np.asarray(targets[index]).reshape(-1).tolist())
            recalls.append(len(expected.intersection(candidates.tolist())) / max(1, len(expected)))
    row: dict[str, object] = {
        "method": name,
        "spec": codec.spec,  # type: ignore[attr-defined]
        "format": format_manifest(codec),
        "bits": int(codec.bits),  # type: ignore[attr-defined]
        "oversample": int(codec.oversample),  # type: ignore[attr-defined]
        "query_encode_ms_p95": p95(encode_ms),
        "candidate_search_ms_p95": p95(search_ms),
        "exact_rerank_ms_p95": p95(rerank_ms),
        "add_ms": p95(add_ms),
        "delete_ms": 0.0,
        "payload_bytes": int(codec.payload_bytes),  # type: ignore[attr-defined]
        "model_bytes": int(codec.model_bytes),  # type: ignore[attr-defined]
        "index_bytes": 0,
        "peak_working_set_bytes": int(codec.payload_bytes + codec.model_bytes),  # type: ignore[attr-defined]
        "candidate_recall_at_k": float(np.mean(recalls)) if recalls else None,
        "recall_p05": float(np.quantile(recalls, 0.05)) if recalls else None,
        "recall_worst_query": float(np.min(recalls)) if recalls else None,
        "final_top10_overlap": float(np.mean(all_overlaps)),
        "final_ndcg_at_10": float(np.mean(all_ndcgs)),
        "final_top10_overlap_p05": float(np.quantile(all_overlaps, 0.05)),
        "final_top10_overlap_worst_query": float(np.min(all_overlaps)),
        "exact_local_k8": False,
        "rebuild_required": "offline_fit",
        "lifecycle_note": "add_ms is encode-only; delete is a tombstone operation; batch rebuild is required for compaction",
    }
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bits", default="16,24,32,48,64,96,128")
    parser.add_argument("--blocks", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--oversample", type=int, default=4)
    args = parser.parse_args()
    data = np.load(args.input, allow_pickle=False)
    vectors = np.asarray(data["vectors"], dtype=np.float32)
    queries = np.asarray(data["queries"], dtype=np.float32)
    targets = np.asarray(data["targets"], dtype=np.int64) if "targets" in data else None
    if vectors.ndim != 2 or queries.ndim != 2 or vectors.shape[1] != queries.shape[1]:
        raise ValueError("vectors and queries must be 2-D with matching dimensions")
    bits = [int(value) for value in args.bits.split(",") if value.strip()]
    rows: list[dict[str, object]] = []
    for width in bits:
        rows.append(evaluate("rabitq_reference", RabitQReference.fit(vectors, width, args.seed, args.oversample), vectors, queries, targets, args.top_k))
        if width % args.blocks == 0:
            rows.append(evaluate("bbq_like_reference", BBQLikeReference.fit(vectors, width, args.blocks, args.seed, args.oversample), vectors, queries, targets, args.top_k))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "rows": rows}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
