#!/usr/bin/env python3
"""Source-bound THQ4 + Faiss LocalSearchQuantizer 32/48-byte replay."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
PAYLOADS = (32, 48)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]
        out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3,
                                      (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out

def top_ids(scores, ids, count=10):
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]

def cosine(values, query):
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64)
    den = np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
    return (x @ q) / den

def ndcg10(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0

def load_candidates(flat, raw, receipt):
    rows = json.loads(raw.read_text(encoding="utf-8")).get("rows")
    if not isinstance(rows, list) or len(rows) != QUERY_COUNT: raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(r["candidate_count"]) for r in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    if flat.stat().st_size != int(offsets[-1]) * 148: raise RuntimeError("candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000): raise RuntimeError("candidate ID outside corpus")
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    if rec.get("execution_status") != "EXECUTED" or rec.get("raw_sha256") != sha256(raw) or rec.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate receipt binding differs")
    return ids, offsets

def fit_centroids(train, thresholds):
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    out = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0).astype(np.float32)
    for d in range(D):
        for level in range(4):
            v = train[levels[:, d] == level, d]
            out[d, level] = float(np.mean(v)) if len(v) else fallback[d]
    return out

def interval_top(query, ids, codes, thresholds):
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level - 1]
            high = np.inf if level == 3 else thresholds[d, level]
            delta = low - query[d] if query[d] < low else query[d] - high if query[d] > high else 0.0
            lut[d, level] = delta * delta
    return ids[np.lexsort((ids, np.sum(lut[np.arange(D)[None, :], levels], axis=1)))[:min(TOP, len(ids))]]

def fit_lsq(residual, m, seed, train_iters, icm_iters, nperts):
    import faiss
    q = faiss.LocalSearchQuantizer(D, m, 8)
    q.train_iters, q.icm_iters, q.nperts, q.random_seed = int(train_iters), int(icm_iters), int(nperts), int(seed)
    q.train(np.ascontiguousarray(residual, dtype=np.float32))
    cb = faiss.vector_to_array(q.codebooks).astype(np.float32, copy=True)
    offsets = faiss.vector_to_array(q.codebook_offsets).astype(np.int64, copy=True)
    dsub = D // m
    if offsets.shape != (m + 1,) or offsets[-1] * dsub != cb.size: raise RuntimeError("unexpected LSQ codebook layout")
    return q, cb.reshape(-1, dsub), offsets

def self_test():
    import faiss
    x = np.random.default_rng(20260921).normal(size=(32, 16)).astype(np.float32)
    q = faiss.LocalSearchQuantizer(16, 2, 8); q.train_iters = q.icm_iters = q.nperts = 1; q.random_seed = 1; q.train(x)
    codes, decoded = q.compute_codes(x[:4]), q.decode(q.compute_codes(x[:4]))
    if codes.shape != (4, 2) or decoded.shape != (4, 16) or not np.isfinite(decoded).all(): raise RuntimeError("LSQ self-test failed")
    print("THQ Faiss LSQ replay self-test: PASS")

def main():
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "models-output", "codes-output")
    for n in names: p.add_argument(f"--{n}", dest=n.replace("-", "_"), type=Path)
    p.add_argument("--train-iters", type=int, default=4); p.add_argument("--icm-iters", type=int, default=4); p.add_argument("--nperts", type=int, default=1); p.add_argument("--lsq-seed", type=int, default=20260921)
    a = p.parse_args()
    if a.self_test: self_test(); return
    vals = [getattr(a, n.replace("-", "_")) for n in names]
    if any(v is None for v in vals): p.error("all source and output paths are required")
    if a.documents.stat().st_size != 1_000_000 * D * 4: raise RuntimeError("documents must be 1M FP32x384")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D)); ntrain = a.train_vectors.stat().st_size // (D * 4)
    train = np.asarray(np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(ntrain, D)), dtype=np.float32)
    thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D, 3); thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)); qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)); qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)); teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    candidate_ids, offsets = load_candidates(a.candidate_flat, a.candidate_raw, a.candidate_receipt)
    centroids = fit_centroids(train, thresholds); levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8); base_train = centroids[np.arange(D)[None, :], levels]; residual = np.ascontiguousarray(train - base_train, dtype=np.float32)
    quantizers, model_data = {}, {}
    for m in PAYLOADS: quantizers[m], model_data[m] = (lambda z: (z[0], (z[1], z[2])))(fit_lsq(residual, m, a.lsq_seed + m, a.train_iters, a.icm_iters, a.nperts))
    rows, selected_all, codes_all = [], [], {m: [] for m in PAYLOADS}
    for qi, query in enumerate(queries):
        ids = candidate_ids[offsets[qi]:offsets[qi+1]]; selected = interval_top(query, ids, thq, thresholds); selected_all.append(selected); lv = unpack_thq(np.asarray(thq[selected])); base = centroids[np.arange(D)[None, :], lv]; exact = top_ids(cosine(np.asarray(docs[ids]), query), ids)
        for m in PAYLOADS:
            codes = np.asarray(quantizers[m].compute_codes(np.asarray(docs[selected], dtype=np.float32) - base), dtype=np.uint8); decoded = np.asarray(quantizers[m].decode(codes), dtype=np.float32); ranked = top_ids(cosine(base + decoded, query), selected); codes_all[m].append(codes)
            rows.append({"query": qi, "arm": f"faiss_lsq{m}", "side_payload_bytes": m, "cascade_total_bytes": THQ_BYTES + m, "top10_ids": ranked.astype(int).tolist(), "thq4_top128_ids": selected.astype(int).tolist(), "candidate_fp32_top10_ids": exact.astype(int).tolist(), "candidate_fp32_overlap": float(np.isin(exact, ranked).sum() / 10), "teacher_overlap": float(np.isin(teacher[qi], ranked).sum() / 10), "qrels_ndcg10": ndcg10(ranked, qrel_ids[qi], qrel_scores[qi])})
    a.models_output.parent.mkdir(parents=True, exist_ok=True); a.codes_output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.models_output, centroids=centroids.astype("<f4"), **{f"lsq{m}_codebooks": model_data[m][0].astype("<f4") for m in PAYLOADS}, **{f"lsq{m}_offsets": model_data[m][1].astype("<i8") for m in PAYLOADS}); np.savez_compressed(a.codes_output, selected_ids=np.stack(selected_all).astype("<i8"), **{f"codes_{m}": np.stack(codes_all[m]) for m in PAYLOADS})
    summaries = {}
    for m in PAYLOADS:
        r = [x for x in rows if x["arm"] == f"faiss_lsq{m}"]; summaries[f"faiss_lsq{m}"] = {"mean_qrels_ndcg10": float(np.mean([x["qrels_ndcg10"] for x in r])), "p05_qrels_ndcg10": float(np.percentile([x["qrels_ndcg10"] for x in r], 5)), "worst_qrels_ndcg10": float(np.min([x["qrels_ndcg10"] for x in r])), "mean_candidate_fp32_overlap": float(np.mean([x["candidate_fp32_overlap"] for x in r])), "mean_teacher_overlap": float(np.mean([x["teacher_overlap"] for x in r])), "side_payload_bytes": m, "cascade_total_bytes": THQ_BYTES + m, "global_codebook_bytes": int(model_data[m][0].size * 4), "full_1m_logical_total_bytes": 1_000_000 * (THQ_BYTES + m) + int(model_data[m][0].size * 4)}
    sources = {n: getattr(a, n.replace("-", "_")) for n in names[:11]}; import faiss
    result = {"schema_version": 1, "family": "thq_faiss_lsq_replay_v1", "status": "EXECUTED", "source_replay": True, "runner_sha256": sha256(Path(__file__)), "query_count": QUERY_COUNT, "metric": "cosine", "faiss_version": faiss.__version__, "lsq_train_iters": a.train_iters, "lsq_icm_iters": a.icm_iters, "lsq_nperts": a.nperts, "lsq_seed": a.lsq_seed, "independent_fits": True, "payloads": list(PAYLOADS), "artifact_hashes": {"models": sha256(a.models_output), "codes": sha256(a.codes_output)}, "source_hashes": {n: sha256(v) for n, v in sources.items()}, "summaries": summaries, "rows": rows, "limitations": ["Faiss LocalSearchQuantizer research control, not AVQ/AAQ/QINCo", "independent LSQ32/LSQ48 fits; no shared-prefix assumption", "candidate-local side-code replay; 1M storage is logical accounting", "held-out confirmation pending"]}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
