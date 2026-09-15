#!/usr/bin/env python3
"""Reference-only full-corpus THQ flow sweep for levels 4..8.

The result is deliberately labelled NumPy reference: it establishes the
quality frontier and arithmetic work, not native kernel or storage latency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384
TRAIN = 100_000


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def thresholds(docs: np.ndarray, levels: int) -> np.ndarray:
    fractions = np.arange(1, levels, dtype=np.float64) / levels
    return np.quantile(np.asarray(docs[:TRAIN], dtype=np.float32), fractions,
                       axis=0, method="linear").T.astype(np.float32)


def teacher_top10(docs: np.ndarray, queries: np.ndarray, chunk: int) -> np.ndarray:
    scores = np.empty((N, len(queries)), dtype=np.float32)
    for start in range(0, N, chunk):
        stop = min(start + chunk, N)
        scores[start:stop] = np.asarray(docs[start:stop] @ queries.T, dtype=np.float32)
    ids = np.arange(N, dtype=np.int64)
    result = np.empty((len(queries), 10), dtype=np.int64)
    for qi in range(len(queries)):
        order = np.lexsort((ids, -scores[:, qi]))[:10]
        result[qi] = ids[order]
    return result


def score_flow(docs: np.ndarray, queries: np.ndarray, cuts: np.ndarray,
               mode: str, chunk: int) -> np.ndarray:
    levels = cuts.shape[1] + 1
    qlevels = np.sum(queries[:, :, None] > cuts[None, :, :], axis=2,
                     dtype=np.uint8)
    # LUT is [query, coordinate, document-level].
    lut = np.empty((len(queries), D, levels), dtype=np.float32)
    if mode == "ordinal-l1":
        for qi in range(len(queries)):
            lut[qi] = np.abs(np.arange(levels, dtype=np.float32)[None, :] -
                             qlevels[qi, :, None])
    else:
        for d in range(D):
            for level in range(levels):
                lo = -np.inf if level == 0 else cuts[d, level - 1]
                hi = np.inf if level == levels - 1 else cuts[d, level]
                for qi, query in enumerate(queries[:, d]):
                    distance = lo - query if query < lo else query - hi if query > hi else 0.0
                    lut[qi, d, level] = distance * distance if mode == "interval-squared" else distance
    scores = np.empty((N, len(queries)), dtype=np.float32)
    for start in range(0, N, chunk):
        stop = min(start + chunk, N)
        values = np.asarray(docs[start:stop], dtype=np.float32)
        doc_levels = np.sum(values[:, :, None] > cuts[None, :, :], axis=2,
                             dtype=np.uint8)
        selected = np.take_along_axis(
            lut[:, None, :, :], doc_levels[None, :, :, None], axis=3)[..., 0]
        scores[start:stop] = np.sum(selected, axis=2, dtype=np.float32).T
    ids = np.arange(N, dtype=np.int64)
    result = np.empty((len(queries), 10), dtype=np.int64)
    for qi in range(len(queries)):
        order = np.lexsort((ids, scores[:, qi]))[:10]
        result[qi] = ids[order]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.query_count <= 0 or args.chunk_size <= 0:
        raise SystemExit("query-count and chunk-size must be positive")
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(N, D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(max(args.query_count, 1), D))[:args.query_count]
    started = time.perf_counter()
    teachers = teacher_top10(docs, np.asarray(queries), args.chunk_size)
    teacher_ms = (time.perf_counter() - started) * 1000.0
    rows = []
    for level_count in range(4, 9):
        cuts = thresholds(docs, level_count)
        bits = max(1, math.ceil(math.log2(level_count)))
        packed_bytes = math.ceil(D * bits / 8)
        for mode in ("ordinal-l1", "interval-l1", "interval-squared"):
            started = time.perf_counter()
            top10 = score_flow(docs, np.asarray(queries), cuts, mode, args.chunk_size)
            elapsed = (time.perf_counter() - started) * 1000.0
            overlaps = np.isin(teachers, top10).sum(axis=1) / 10.0
            rows.append({
                "levels": level_count,
                "mode": mode,
                "logical_packed_bytes_per_document": packed_bytes,
                "teacher_overlap_mean": float(np.mean(overlaps)),
                "teacher_overlap_min": float(np.min(overlaps)),
                "ordered_top10_parity": int(np.all(top10 == teachers, axis=1).sum()),
                "queries": args.query_count,
                "reference_scan_ms_mean": elapsed / args.query_count,
            })
    result = {
        "schema_version": 1,
        "family": "native_full_corpus_thq_flow_reference_v1",
        "status": "EXECUTED",
        "evidence_status": "numpy_reference_quality_and_arithmetic_cost_only",
        "documents": N,
        "dimension": D,
        "training_count": TRAIN,
        "query_count": args.query_count,
        "chunk_size": args.chunk_size,
        "document_vectors_sha256": sha(args.documents),
        "queries_sha256": sha(args.queries),
        "teacher_reference_build_ms": teacher_ms,
        "modes": rows,
        "not_established": [
            "native SIMD throughput",
            "cold/warm OS or MDBX page latency",
            "thermometer XOR/POPCNT control",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


if __name__ == "__main__":
    main()
