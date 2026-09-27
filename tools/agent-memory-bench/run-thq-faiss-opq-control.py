#!/usr/bin/env python3
"""Source-bound official Faiss OPQ/PQ control on the frozen THQ4 shell."""
from __future__ import annotations

import argparse, hashlib, json, time
from pathlib import Path
import numpy as np

D, THQ_BYTES, TOP = 384, 96, 128

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def unpack(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = codes[:, b]
        out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3,
                                      (x >> 4) & 3, x >> 6), axis=1)
    return out

def top(ids: np.ndarray, scores: np.ndarray, k: int, ascending=False) -> np.ndarray:
    key = scores if ascending else -scores
    return ids[np.lexsort((ids, key))[:k]]

def ndcg(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(i): float(g) for i, g in zip(qids, grades) if int(i) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(i), 0.0) - 1.0 for i in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    den = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / den) if den else 0.0

def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "train_vectors", "queries", "qrel_ids", "qrel_scores",
                 "thq_codes", "thresholds", "candidate_flat", "candidate_raw", "output"):
        p.add_argument("--" + name.replace("_", "-"), dest=name, type=Path, required=True)
    p.add_argument("--seed", type=int, default=20260927)
    p.add_argument("--opq-niter", type=int, default=50)
    p.add_argument("--opq-niter-pq", type=int, default=40)
    p.add_argument("--opq-niter-pq-0", type=int, default=40)
    p.add_argument("--pq-kmeans-iters", type=int, default=40)
    p.add_argument("--model-input", type=Path)
    args = p.parse_args()
    import faiss

    train = np.memmap(args.train_vectors, mode="r", dtype="<f4",
                      shape=(args.train_vectors.stat().st_size // (4 * D), D))
    docs = np.memmap(args.documents, mode="r", dtype="<f4",
                     shape=(args.documents.stat().st_size // (4 * D), D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(args.queries.stat().st_size // (4 * D), D))
    qrel_ids = np.fromfile(args.qrel_ids, dtype="<i8").reshape(len(queries), 20)
    qrel_scores = np.fromfile(args.qrel_scores, dtype="<f4").reshape(len(queries), 20)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq_codes, mode="r", dtype=np.uint8,
                    shape=(docs.shape[0], THQ_BYTES))
    rows = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    flat_bytes = args.candidate_flat.stat().st_size
    if flat_bytes % int(offsets[-1]) != 0:
        raise RuntimeError("candidate flat/raw cardinality mismatch")
    record_bytes = flat_bytes // int(offsets[-1])
    if record_bytes not in (100, 148):
        raise RuntimeError("unsupported candidate record size")
    flat = np.fromfile(args.candidate_flat, dtype=np.uint8).reshape(int(offsets[-1]), record_bytes)
    candidate_ids = np.asarray(flat[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(candidate_ids < 0) or np.any(candidate_ids >= docs.shape[0]):
        raise RuntimeError("candidate ID outside corpus")
    levels = np.sum(np.asarray(train)[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(np.asarray(train), axis=0)
    for d in range(D):
        for level in range(4):
            values = np.asarray(train)[levels[:, d] == level, d]
            centroids[d, level] = np.mean(values) if len(values) else fallback[d]
    selected = []
    for qi, query in enumerate(np.asarray(queries)):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        lv = unpack(np.asarray(thq[ids]))
        lut = np.empty((D, 4), dtype=np.float32)
        for d in range(D):
            for level in range(4):
                lo = -np.inf if level == 0 else thresholds[d, level - 1]
                hi = np.inf if level == 3 else thresholds[d, level]
                delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
                lut[d, level] = delta * delta
        selected.append(top(ids, np.sum(lut[np.arange(D)[None, :], lv], axis=1), min(TOP, len(ids)), True))
    union = np.unique(np.concatenate(selected)).astype(np.int64)
    union_levels = unpack(np.asarray(thq[union]))
    union_base = centroids[np.arange(D)[None, :], union_levels]
    residual_train = np.asarray(train) - centroids[np.arange(D)[None, :], levels]
    residual = np.asarray(docs[union], dtype=np.float32) - union_base
    started = time.perf_counter()
    if args.model_input is not None:
        loaded = np.load(args.model_input)
        rotation = np.asarray(loaded["rotation"], dtype=np.float32)
        centers = np.asarray(loaded["centers"], dtype=np.float32)
        fit_seconds = None
    else:
        pq = faiss.ProductQuantizer(D, 32, 4)
        pq.cp.niter = args.pq_kmeans_iters; pq.cp.seed = args.seed; pq.cp.verbose = False
        opq = faiss.OPQMatrix(D, 32); opq.pq = pq
        opq.niter = args.opq_niter; opq.niter_pq = args.opq_niter_pq
        opq.niter_pq_0 = args.opq_niter_pq_0; opq.verbose = False
        opq.train(np.ascontiguousarray(residual_train, dtype=np.float32))
        fit_seconds = time.perf_counter() - started
        rotation = faiss.vector_to_array(opq.A).reshape(D, D).astype(np.float32)
        centers = faiss.vector_to_array(pq.centroids).reshape(32, 16, D // 32).astype(np.float32)
    model_path = args.output.with_suffix(".model.npz")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(model_path, rotation=rotation, centers=centers)
    transformed = np.ascontiguousarray(residual @ rotation, dtype=np.float32)
    codes = np.empty((len(transformed), 32), dtype=np.uint8)
    width = D // 32
    for subspace in range(32):
        part = transformed[:, subspace * width:(subspace + 1) * width]
        distances = ((part[:, None, :] - centers[subspace][None, :, :]) ** 2).sum(axis=2)
        codes[:, subspace] = distances.argmin(axis=1).astype(np.uint8)
    decoded = centers[np.arange(32)[None, :], codes].reshape(len(union), D) @ rotation.T
    reconstructed = union_base + decoded
    by_id = {int(doc): i for i, doc in enumerate(union)}
    rows_out = []; qualities = []
    for qi, query in enumerate(np.asarray(queries)):
        ids = selected[qi]; positions = np.asarray([by_id[int(i)] for i in ids])
        scores = ((reconstructed[positions] + 0.0) @ query) / np.maximum(
            np.linalg.norm(reconstructed[positions], axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny)
        ranked = top(ids, scores, min(10, len(ids)))
        value = ndcg(ranked, qrel_ids[qi], qrel_scores[qi]); qualities.append(value)
        rows_out.append({"query": qi, "top10_ids": ranked.astype(int).tolist(), "qrels_ndcg10": value,
                         "candidate_fp32_overlap": float(np.isin(ranked, top(ids, np.asarray(docs[ids]) @ query, min(10, len(ids)))).sum() / 10.0)})
    result = {"schema_version": 1, "family": "thq_faiss_official_opq_control_v1", "status": "EXECUTED",
              "source_replay": True, "metric": "cosine", "query_count": len(queries), "payload_bytes": 16,
              "subquantizers": 32, "bits": 4, "seed": args.seed,
              "opq_config": {"niter": args.opq_niter, "niter_pq": args.opq_niter_pq,
                             "niter_pq_0": args.opq_niter_pq_0, "pq_kmeans_iters": args.pq_kmeans_iters,
                             "official_faiss_defaults": args.opq_niter == 50 and args.opq_niter_pq == 40 and args.opq_niter_pq_0 == 40},
              "fit_seconds": fit_seconds, "model_replay_only": args.model_input is not None,
              "mean_qrels_ndcg10": float(np.mean(qualities)),
              "candidate_union_documents": int(len(union)), "model_path": str(model_path),
              "model_sha256": sha(model_path), "rotation_sha256": hashlib.sha256(rotation.astype("<f4").tobytes()).hexdigest(),
              "centroids_sha256": hashlib.sha256(centers.astype("<f4").tobytes()).hexdigest(),
              "source_hashes": {name: sha(path) for name, path in vars(args).items()
                                 if isinstance(path, Path) and path != args.output and path.is_file()}, "rows": rows_out,
              "limitations": ["candidate-local 152-query replay", "official Faiss OPQ/PQ control; not native serving benchmark"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mean_qrels_ndcg10", "fit_seconds", "candidate_union_documents", "opq_config")}))

if __name__ == "__main__":
    main()
