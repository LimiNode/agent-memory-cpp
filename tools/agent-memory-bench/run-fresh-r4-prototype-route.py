#!/usr/bin/env python3
"""Build a fresh three-seed R4-shaped prototype route.

This deliberately uses a transparent three-seed spherical IVF fusion rather
than claiming the historical NeuRoute model.  It is a fresh route diagnostic;
the receipt names the prototype status and binds every source/model hash.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--nlist", type=int, default=256)
    parser.add_argument("--nprobe", type=int, default=8)
    parser.add_argument("--candidate-k", type=int, default=5000)
    parser.add_argument("--train-rows", type=int, default=25000)
    args = parser.parse_args()
    try:
        import faiss
    except ImportError as error:
        raise SystemExit("faiss is required") from error
    dimension = 384
    count = args.documents.stat().st_size // (dimension * 4)
    query_count = args.queries.stat().st_size // (dimension * 4)
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, dimension))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, dimension))
    train = np.memmap(args.train_vectors, mode="r", dtype="<f4")
    train = np.asarray(train[: args.train_rows * dimension], dtype="float32").reshape(-1, dimension).copy()
    train /= np.maximum(np.linalg.norm(train, axis=1, keepdims=True), 1e-30)
    q = np.asarray(queries, dtype="float32").copy()
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-30)
    seeds = (2026082701, 2026082702, 2026082703)
    postings_all = []
    centroids_all = []
    model_hash = hashlib.sha256()
    for seed in seeds:
        kmeans = faiss.Kmeans(dimension, args.nlist, niter=20, seed=seed, verbose=False, spherical=True)
        kmeans.train(train)
        centroids = np.asarray(kmeans.centroids, dtype="float32").reshape(args.nlist, dimension)
        assignments = []
        for begin in range(0, count, 16384):
            end = min(count, begin + 16384)
            block = np.asarray(docs[begin:end], dtype="float32").copy()
            block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-30)
            _, cells = kmeans.index.search(block, 1)
            assignments.append(cells[:, 0].astype("int32"))
        assigned = np.concatenate(assignments)
        postings = [np.flatnonzero(assigned == cell).astype("int32") for cell in range(args.nlist)]
        postings_all.append(postings)
        centroids_all.append(centroids)
        model_hash.update(centroids.tobytes())
    ids = np.empty((query_count, args.candidate_k), dtype="int32")
    raw_counts = []
    for qi in range(query_count):
        candidates = set()
        for seed_index in range(len(seeds)):
            scores = centroids_all[seed_index] @ q[qi]
            cells = np.argsort(-scores, kind="stable")[: min(args.nprobe, args.nlist)]
            for cell in cells:
                candidates.update(int(value) for value in postings_all[seed_index][int(cell)])
        candidate_ids = np.asarray(sorted(candidates), dtype="int32")
        raw_counts.append(int(candidate_ids.size))
        best_ids = np.empty(0, dtype="int32")
        best_scores = np.empty(0, dtype="float32")
        for begin in range(0, candidate_ids.size, 16384):
            chunk_ids = candidate_ids[begin:min(begin + 16384, candidate_ids.size)]
            values_block = np.asarray(docs[chunk_ids], dtype="float32").copy()
            values_block /= np.maximum(np.linalg.norm(values_block, axis=1, keepdims=True), 1e-30)
            values = values_block @ q[qi]
            take = min(args.candidate_k, values.size)
            local = np.argpartition(-values, take - 1)[:take]
            merged_ids = np.concatenate((best_ids, chunk_ids[local]))
            merged_scores = np.concatenate((best_scores, values[local]))
            order = np.lexsort((merged_ids, -merged_scores))[:args.candidate_k]
            best_ids, best_scores = merged_ids[order], merged_scores[order]
        ranked = best_ids
        take = ranked.size
        ids[qi, :take] = ranked
        if take < args.candidate_k:
            ids[qi, take:] = -1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ids.astype("<i4").tofile(args.output)
    manifest = {"schema_version": 1, "family": "fresh_modern_r4_prototype_route_v1",
                "status": "PROTOTYPE_EXECUTED", "production_activation": False,
                "document_count": count, "query_count": query_count, "dimension": dimension,
                "seeds": list(seeds), "nlist": args.nlist, "nprobe": args.nprobe,
                "candidate_k": args.candidate_k, "training_rows": int(train.shape[0]),
                "candidate_tie_policy": "score_desc_id_asc", "metric": "cosine",
                "raw_candidate_count_mean": float(np.mean(raw_counts)),
                "raw_candidate_count_max": int(max(raw_counts)),
                "source_hashes": {"documents": sha256(args.documents), "train_vectors": sha256(args.train_vectors), "queries": sha256(args.queries)},
                "route_model_sha256": model_hash.hexdigest(), "output_sha256": sha256(args.output),
                "limitations": ["fresh three-seed IVF prototype, not historical NeuRoute R4 model", "exact FP32 rerank; no THQ packed scorer timing"]}
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))

if __name__ == "__main__":
    main()
