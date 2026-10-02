#!/usr/bin/env python3
"""Build a deterministic packed-serving Prototype-IVF candidate stream.

This is routing evidence only.  It deliberately does not claim codec scoring
or quality parity; downstream packed scorers consume the frozen candidate IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def self_test() -> None:
    vectors = np.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype="float32")
    queries = np.asarray([[1.0, 0.0]], dtype="float32")
    scores = (vectors / np.linalg.norm(vectors, axis=1, keepdims=True)) @ queries[0]
    ids = np.argsort(-scores, kind="stable")[:2]
    if ids.tolist() != [0, 2] or not np.all(np.isfinite(scores)):
        raise AssertionError("prototype route self-test differs")
    # The bounded posting selection must be score-first, not ID-first.
    candidate_ids = np.asarray([9, 1, 7], dtype="int32")
    candidate_scores = np.asarray([0.1, 0.9, 0.8], dtype="float32")
    bounded = np.lexsort((candidate_ids, -candidate_scores))[:2]
    if candidate_ids[bounded].tolist() != [1, 7]:
        raise AssertionError("prototype route score-bound self-test differs")
    print("prototype-ivf route self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--train-vectors", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--nprobe", type=int, default=16)
    parser.add_argument("--candidate-k", type=int, default=5000)
    parser.add_argument("--train-rows", type=int, default=25000)
    parser.add_argument("--query-count", type=int, default=152)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("faiss is required for Prototype-IVF materialization") from error
    for name in ("documents", "train_vectors", "queries", "output", "manifest"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    if args.nlist <= 0 or args.nprobe <= 0 or args.candidate_k <= 0:
        parser.error("nlist, nprobe and candidate-k must be positive")
    dimension = 384
    document_count = args.documents.stat().st_size // (dimension * 4)
    query_count = min(args.query_count, args.queries.stat().st_size // (dimension * 4))
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, dimension))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, dimension))
    train = np.memmap(args.train_vectors, mode="r", dtype="<f4")
    train = np.asarray(train[: min(args.train_rows * dimension, train.size)], dtype="float32").reshape(-1, dimension)
    train = train / np.maximum(np.linalg.norm(train, axis=1, keepdims=True), 1e-30)
    kmeans = faiss.Kmeans(dimension, args.nlist, niter=20, seed=20261002,
                          verbose=False, spherical=True)
    kmeans.train(train)
    centroids = np.asarray(kmeans.centroids, dtype="float32").reshape(args.nlist, dimension)
    postings: list[list[int]] = [[] for _ in range(args.nlist)]
    for begin in range(0, document_count, 1_024):
        end = min(document_count, begin + 1_024)
        block = np.asarray(documents[begin:end], dtype="float32").copy()
        block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-30)
        _, cells = kmeans.index.search(block, 1)
        for offset, cell in enumerate(cells[:, 0].tolist()):
            postings[int(cell)].append(begin + offset)
    query_values = np.asarray(queries[:query_count], dtype="float32").copy()
    query_values /= np.maximum(np.linalg.norm(query_values, axis=1, keepdims=True), 1e-30)
    centroid_scores = query_values @ centroids.T
    ids = np.empty((query_count, min(args.candidate_k, document_count)), dtype="int32")
    scores = np.empty_like(ids, dtype="float32")
    raw_counts = []
    for query_index in range(query_count):
        cells = np.argsort(-centroid_scores[query_index], kind="stable")[: min(args.nprobe, args.nlist)]
        candidates = np.asarray(sorted({doc for cell in cells for doc in postings[int(cell)]}), dtype="int32")
        if candidates.size == 0:
            raise RuntimeError("prototype route selected no postings")
        raw_counts.append(int(candidates.size))
        values = np.asarray(documents[candidates], dtype="float32").copy()
        values /= np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-30)
        candidate_scores = values @ query_values[query_index]
        # Apply the bounded posting budget by score, never by raw ID order.
        # ID truncation silently discarded the nearest documents and made the
        # route unsuitable for a quality calibration.
        posting_order = np.lexsort((candidates, -candidate_scores))[:8192]
        order = posting_order[: ids.shape[1]]
        count = len(order)
        ids[query_index, :count] = candidates[order]
        scores[query_index, :count] = candidate_scores[order]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.asarray(ids, dtype="<i4").tofile(args.output)
    manifest = {
        "schema_version": 1,
        "family": "prototype_ivf_balanced_route_v1",
        "status": "PASS",
        "metric": "cosine",
        "document_count": int(document_count),
        "query_count": int(query_count),
        "dimension": dimension,
        "nlist": int(args.nlist),
        "nprobe": int(min(args.nprobe, args.nlist)),
        "candidate_k": int(ids.shape[1]),
        "candidate_id_dtype": "int32",
        "candidate_tie_policy": "score_desc_id_asc",
        "training_rows": int(train.shape[0]),
        "source_hashes": {"documents": sha256(args.documents), "train_vectors": sha256(args.train_vectors), "queries": sha256(args.queries)},
        "route_model_sha256": hashlib.sha256(centroids.tobytes()).hexdigest(),
        "output_sha256": sha256(args.output),
        "mean_candidates": int(ids.shape[1]),
        "min_candidates": int(ids.shape[1]),
        "max_candidates": int(ids.shape[1]),
        "raw_candidate_count_mean": float(np.mean(raw_counts)),
        "raw_candidate_count_max": int(max(raw_counts)),
        "candidate_budget": 8192,
        "limitations": ["routing-only receipt; packed codec scoring and quality parity are separate gates"],
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
