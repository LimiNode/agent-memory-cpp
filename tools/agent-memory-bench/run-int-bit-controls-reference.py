#!/usr/bin/env python3
"""Reference INT8/9/10 quality control on the full DE-1M corpus.

Codes are kept in chunk-local int16 arrays; no packed INT9/INT10 storage claim
is made.  The purpose is to test whether INT8 is near a quality cliff.
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


def teachers(docs: np.ndarray, queries: np.ndarray, chunk: int) -> np.ndarray:
    scores = np.empty((N, len(queries)), dtype=np.float32)
    for start in range(0, N, chunk):
        stop = min(start + chunk, N)
        scores[start:stop] = np.asarray(docs[start:stop] @ queries.T, dtype=np.float32)
    ids = np.arange(N, dtype=np.int64)
    result = np.empty((len(queries), 10), dtype=np.int64)
    for qi in range(len(queries)):
        result[qi] = ids[np.lexsort((ids, -scores[:, qi]))[:10]]
    return result


def quantized_scores(docs: np.ndarray, queries: np.ndarray, bits: int,
                     power: float, chunk: int) -> np.ndarray:
    qmax = (1 << (bits - 1)) - 1
    scores = np.zeros((len(queries), N), dtype=np.float32)
    for start in range(0, N, chunk):
        stop = min(start + chunk, N)
        values = np.asarray(docs[start:stop], dtype=np.float32)
        transformed = np.copysign(np.power(np.abs(values), power), values)
        maxima = np.max(np.abs(transformed), axis=1)
        scales = np.divide(maxima, float(qmax), out=np.ones_like(maxima), where=maxima > 0)
        codes = np.clip(np.rint(transformed / scales[:, None]), -qmax, qmax).astype(np.int16)
        reconstructed = (codes.astype(np.float32) * scales[:, None])
        if power != 1.0:
            reconstructed = np.copysign(np.power(np.abs(reconstructed), 1.0 / power),
                                         reconstructed)
        scores[:, start:stop] = (reconstructed @ queries.T).T
    return scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(N, D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(max(args.query_count, 1), D))[:args.query_count]
    query_array = np.asarray(queries, dtype=np.float32)
    started = time.perf_counter()
    teacher = teachers(docs, query_array, args.chunk_size)
    teacher_ms = (time.perf_counter() - started) * 1000.0
    rows = []
    for bits in (8, 9, 10):
        for power in (1.0, 0.625):
            started = time.perf_counter()
            scores = quantized_scores(docs, query_array, bits, power, args.chunk_size)
            elapsed = (time.perf_counter() - started) * 1000.0
            ids = np.arange(N, dtype=np.int64)
            top = np.empty_like(teacher)
            for qi in range(args.query_count):
                top[qi] = ids[np.lexsort((ids, -scores[qi]))[:10]]
            overlap = np.isin(teacher, top).sum(axis=1) / 10.0
            rows.append({
                "bits": bits,
                "power": power,
                "logical_code_bytes_per_document": math.ceil(D * bits / 8),
                "logical_representation_bytes_per_document": math.ceil(D * bits / 8) + 4,
                "teacher_overlap_mean": float(np.mean(overlap)),
                "teacher_overlap_min": float(np.min(overlap)),
                "ordered_top10_parity": int(np.all(top == teacher, axis=1).sum()),
                "reference_scan_ms_mean": elapsed / args.query_count,
            })
    result = {
        "schema_version": 1,
        "family": "native_full_corpus_int_bit_controls_reference_v1",
        "status": "EXECUTED",
        "evidence_status": "chunk_local_reference_quality_only; no packed storage claim",
        "documents": N,
        "dimension": D,
        "training_count": TRAIN,
        "query_count": args.query_count,
        "chunk_size": args.chunk_size,
        "document_vectors_sha256": sha(args.documents),
        "queries_sha256": sha(args.queries),
        "teacher_reference_build_ms": teacher_ms,
        "arms": rows,
        "not_established": [
            "packed INT9/INT10 byte layout",
            "native SIMD throughput",
            "qrels or held-out domain quality",
            "cold/warm OS or MDBX page latency",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


if __name__ == "__main__":
    main()
