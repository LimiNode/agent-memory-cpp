#!/usr/bin/env python3
"""Independent persisted-model and Faiss-API replay audit for OPQ/PQ."""
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, THQ_BYTES, TOP = 384, 96, 128

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def unpack_thq(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = codes[:, b]
        out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3,
                                      (x >> 4) & 3, x >> 6), axis=1)
    return out

def top(ids: np.ndarray, scores: np.ndarray, k: int, ascending=False) -> np.ndarray:
    return ids[np.lexsort((ids, scores if ascending else -scores))[:k]]

def ndcg(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(i): float(g) for i, g in zip(qids, grades) if int(i) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(i), 0.0) - 1.0 for i in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    den = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    num = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    return float(num / den) if den else 0.0

def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("result", "model", "documents", "train_vectors", "queries", "qrel_ids", "qrel_scores",
                 "thq_codes", "thresholds", "candidate_flat", "candidate_raw"):
        p.add_argument("--" + name.replace("_", "-"), dest=name, type=Path, required=True)
    args = p.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_faiss_official_opq_control_v1" or result.get("status") != "EXECUTED":
        raise RuntimeError("unexpected OPQ result family/status")
    if result.get("query_count") != 152 or result.get("metric") != "cosine":
        raise RuntimeError("OPQ protocol differs")
    config = result.get("opq_config", {})
    if config.get("niter") != 50 or config.get("niter_pq") not in (4, 40) or config.get("niter_pq_0") != 40:
        raise RuntimeError("OPQ configuration is not pinned")
    if config.get("niter_pq") == 40 and config.get("official_faiss_defaults"):
        raise RuntimeError("strong 50/40/40 arm mislabeled as Faiss default")
    rows = result.get("rows", [])
    if len(rows) != 152 or {int(row["query"]) for row in rows} != set(range(152)):
        raise RuntimeError("OPQ row cardinality differs")
    model = np.load(args.model)
    rotation = np.asarray(model["rotation"], dtype=np.float32)
    centers = np.asarray(model["centers"], dtype=np.float32)
    if rotation.shape != (D, D) or centers.shape != (32, 16, 12):
        raise RuntimeError("OPQ model shape differs")
    if not np.isfinite(rotation).all() or not np.isfinite(centers).all():
        raise RuntimeError("OPQ model contains non-finite values")
    if abs(float(np.max(np.abs(rotation.T @ rotation - np.eye(D, dtype=np.float32))))) > 5e-4:
        raise RuntimeError("OPQ rotation is not orthogonal")
    if result.get("model_sha256") != sha(args.model):
        raise RuntimeError("OPQ model hash differs")

    import faiss
    documents = np.memmap(args.documents, mode="r", dtype="<f4",
                          shape=(args.documents.stat().st_size // (4 * D), D))
    train = np.memmap(args.train_vectors, mode="r", dtype="<f4",
                      shape=(args.train_vectors.stat().st_size // (4 * D), D))
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(args.queries.stat().st_size // (4 * D), D))
    qrel_ids = np.fromfile(args.qrel_ids, dtype="<i8").reshape(len(queries), 20)
    qrel_scores = np.fromfile(args.qrel_scores, dtype="<f4").reshape(len(queries), 20)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq_codes, mode="r", dtype=np.uint8,
                    shape=(documents.shape[0], THQ_BYTES))
    raw_rows = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in raw_rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    record_bytes = args.candidate_flat.stat().st_size // int(offsets[-1])
    flat = np.fromfile(args.candidate_flat, dtype=np.uint8).reshape(int(offsets[-1]), record_bytes)
    candidate_ids = np.asarray(flat[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
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
        lv = unpack_thq(np.asarray(thq[ids]))
        lut = np.empty((D, 4), dtype=np.float32)
        for d in range(D):
            for level in range(4):
                lo = -np.inf if level == 0 else thresholds[d, level - 1]
                hi = np.inf if level == 3 else thresholds[d, level]
                delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
                lut[d, level] = delta * delta
        selected.append(top(ids, np.sum(lut[np.arange(D)[None, :], lv], axis=1), min(TOP, len(ids)), True))
    union = np.unique(np.concatenate(selected)).astype(np.int64)
    union_levels = unpack_thq(np.asarray(thq[union]))
    union_base = centroids[np.arange(D)[None, :], union_levels]
    residual = np.asarray(documents[union], dtype=np.float32) - union_base
    transformed = np.ascontiguousarray(residual @ rotation.T, dtype=np.float32)
    pq = faiss.ProductQuantizer(D, 32, 4)
    faiss.copy_array_to_vector(centers.astype("<f4").ravel(), pq.centroids)
    faiss_codes = np.asarray(pq.compute_codes(transformed), dtype=np.uint8).reshape(len(union), 16)
    unpacked = np.empty((len(union), 32), dtype=np.uint8)
    # Faiss packs 4-bit PQ assignments low-nibble first.
    unpacked[:, 0::2] = faiss_codes & 15
    unpacked[:, 1::2] = faiss_codes >> 4
    manual = np.empty_like(unpacked)
    for subspace in range(32):
        part = transformed[:, subspace * 12:(subspace + 1) * 12]
        manual[:, subspace] = ((part[:, None, :] - centers[subspace][None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    if not np.array_equal(unpacked, manual):
        raise RuntimeError("Faiss PQ assignments differ from independent manual replay")
    decoded_rot = np.asarray(pq.decode(faiss_codes), dtype=np.float32)
    manual_decoded = centers[np.arange(32)[None, :], manual].reshape(len(union), D)
    if not np.allclose(decoded_rot, manual_decoded, atol=2e-5, rtol=2e-5):
        raise RuntimeError("Faiss PQ decode differs from persisted centroids")
    # Direct Faiss LinearTransform parity proves the row/column orientation.
    lt = faiss.LinearTransform(D, D)
    faiss.copy_array_to_vector(rotation.astype("<f4").ravel(), lt.A)
    lt.is_trained = True
    api_transformed = np.asarray(lt.apply_py(np.asarray(residual)), dtype=np.float32)
    if not np.allclose(api_transformed, transformed, atol=2e-5, rtol=2e-5):
        raise RuntimeError("Faiss LinearTransform orientation differs from replay")
    if not np.all(np.isfinite(decoded_rot)):
        raise RuntimeError("Faiss decoded vectors are non-finite")
    reconstructed = union_base + decoded_rot @ rotation
    by_id = {int(doc): i for i, doc in enumerate(union)}
    for qi, row in enumerate(rows):
        ids = selected[qi]
        positions = np.asarray([by_id[int(i)] for i in ids])
        q = np.asarray(queries[qi])
        scores = (reconstructed[positions] @ q) / np.maximum(
            np.linalg.norm(reconstructed[positions], axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
        expected = top(ids, scores, min(10, len(ids)))
        if expected.astype(int).tolist() != row.get("top10_ids", []):
            raise RuntimeError(f"OPQ top10 mismatch at query {qi}")
        value = ndcg(expected, qrel_ids[qi], qrel_scores[qi])
        if abs(value - float(row["qrels_ndcg10"])) > 2e-6:
            raise RuntimeError(f"OPQ nDCG mismatch at query {qi}")
    print("THQ official Faiss OPQ independent replay audit PASS")

if __name__ == "__main__":
    main()
