#!/usr/bin/env python3
"""Historical-only Prototype-IVF calibration sweep for the three-mode gate."""
from __future__ import annotations
import argparse, hashlib, json, time
import gc
from pathlib import Path
import numpy as np

D, N, Q = 384, 1_000_000, 152

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def pct(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, int(np.ceil(fraction * len(ordered))) - 1)])

def self_test() -> None:
    if pct([0.1, 0.2, 0.3], .5) != .2:
        raise AssertionError("prototype calibration self-test differs")
    print("prototype-ivf calibration self-test PASS")

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--documents", type=Path)
    p.add_argument("--train-vectors", type=Path)
    p.add_argument("--queries", type=Path)
    p.add_argument("--teacher", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--query-count", type=int, default=Q)
    a = p.parse_args()
    if a.self_test:
        self_test(); return
    if not all((a.documents, a.train_vectors, a.queries, a.teacher, a.output)):
        p.error("all inputs are required")
    import faiss
    query_count = min(a.query_count, Q)
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(N, D))
    train = np.asarray(np.memmap(a.train_vectors, mode="r", dtype="<f4"), dtype="f4").reshape(-1, D)
    train = train / np.maximum(np.linalg.norm(train, axis=1, keepdims=True), 1e-30)
    queries = np.asarray(np.memmap(a.queries, mode="r", dtype="<f4", shape=(query_count, D)), dtype="f4").copy()
    queries /= np.maximum(np.linalg.norm(queries, axis=1, keepdims=True), 1e-30)
    teacher = np.fromfile(a.teacher, dtype="<i8").reshape(-1, 10)[:query_count]
    # Compute the exact FP32 oracle in bounded chunks so calibration does not
    # require a second 1M-vector resident index alongside the source mmap.
    exact_ids = np.empty((query_count, 10), dtype="i8")
    for qi, query in enumerate(queries):
        best_scores = np.full(10, -np.inf, dtype="f4")
        best_ids = np.full(10, N, dtype="i8")
        for begin in range(0, N, 32768):
            end = min(begin + 32768, N)
            block = np.asarray(docs[begin:end], dtype="f4").copy()
            block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-30)
            scores = block @ query
            take = min(10, len(scores))
            local = np.argpartition(scores, -take)[-take:]
            merged_scores = np.concatenate((best_scores, scores[local]))
            merged_ids = np.concatenate((best_ids, (begin + local).astype("i8")))
            order = np.lexsort((merged_ids, -merged_scores))[:10]
            best_scores, best_ids = merged_scores[order], merged_ids[order]
            del block, scores, local, merged_scores, merged_ids, order
        gc.collect()
        exact_ids[qi] = best_ids
    rows = []
    for nlist in (256, 512, 1024):
        kmeans = faiss.Kmeans(D, nlist, niter=20, seed=20261002, verbose=False, spherical=True)
        kmeans.train(train)
        centroids = np.asarray(kmeans.centroids, dtype="f4").reshape(nlist, D)
        postings: list[list[int]] = [[] for _ in range(nlist)]
        for begin in range(0, N, 65536):
            block = np.asarray(docs[begin:min(begin + 65536, N)], dtype="f4").copy()
            block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-30)
            _, cells = kmeans.index.search(block, 1)
            for offset, cell in enumerate(cells[:, 0].tolist()):
                postings[int(cell)].append(begin + offset)
        centroid_scores = queries @ centroids.T
        for nprobe in (4, 8, 16, 32):
            route_started = time.perf_counter()
            per_query = []
            for qi in range(query_count):
                cells = np.argsort(-centroid_scores[qi], kind="stable")[:min(nprobe, nlist)]
                candidates = np.asarray(sorted({doc for cell in cells for doc in postings[int(cell)]}), dtype="i4")
                values = np.asarray(docs[candidates], dtype="f4").copy()
                values /= np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-30)
                scores = values @ queries[qi]
                order = np.lexsort((candidates, -scores))
                ranked = candidates[order]
                for budget in (5000, 10000, 20000, 50000):
                    selected = ranked[:min(budget, len(ranked))]
                    teacher_cov = float(np.isin(teacher[qi], selected).sum() / 10.0)
                    exact_recall = float(np.isin(exact_ids[qi], selected).sum() / 10.0)
                    per_query.append((budget, len(selected), teacher_cov, exact_recall))
            for budget in (5000, 10000, 20000, 50000):
                values = [x for x in per_query if x[0] == budget]
                cov = [x[2] for x in values]; rec = [x[3] for x in values]
                rows.append({"nlist": nlist, "nprobe": nprobe, "candidate_budget": budget,
                             "mean_candidate_count": float(np.mean([x[1] for x in values])),
                             "mean_teacher_coverage": float(np.mean(cov)),
                             "p05_teacher_coverage": pct(cov, .05),
                             "worst_teacher_coverage": float(min(cov)),
                             "mean_exact_recall_at_candidate": float(np.mean(rec)),
                             "p05_exact_recall_at_candidate": pct(rec, .05),
                             "worst_exact_recall_at_candidate": float(min(rec)),
                             "route_elapsed_ms": (time.perf_counter() - route_started) * 1000.0,
                             "route_ms_per_query": (time.perf_counter() - route_started) * 1000.0 / query_count})
    out = {"schema_version": 1, "family": "prototype_ivf_historical_calibration_v1",
           "status": "PASS", "source_kind": "historical_only", "query_count": query_count,
           "grid": {"nlist": [256, 512, 1024], "nprobe": [4, 8, 16, 32], "candidate_budget": [5000, 10000, 20000, 50000]},
           "source_hashes": {"documents": sha(a.documents), "train_vectors": sha(a.train_vectors), "queries": sha(a.queries), "teacher": sha(a.teacher)},
           "rows": rows, "limitations": ["No fresh qrels were opened; teacher is historical calibration only", "route_elapsed_ms includes calibration work and is not a serving latency claim"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "rows": len(rows), "best_mean_exact_recall": max(rows, key=lambda x: x["mean_exact_recall_at_candidate"])}, sort_keys=True))

if __name__ == "__main__":
    main()
