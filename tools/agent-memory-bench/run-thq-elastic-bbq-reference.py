#!/usr/bin/env python3
"""Source-bound Elastic/Lucene BBQ scalar-quantization control.

This ports the public ``OptimizedScalarQuantizer`` 1-bit document semantics:
unit-cosine inputs, a segment centroid, exact interval coordinate descent, and
the mixed 1-bit-document/4-bit-query correction formula.  The replay keeps
codes byte-per-dimension in its Python working artifact, while reporting the
logical packed 48-bit payload and 62/64-byte record accounting.  A separate
deterministic block-PCA preconditioner is measured as global metadata.  It is
not an HNSW/BBQ-disk serving benchmark; oversampling and native SIMD are
separate gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
LUCENE_REVISION = "0b346c5141e5ffeca8129a0280fa55768a19c643"
LUCENE_SOURCE = "lucene/core/src/java/org/apache/lucene/util/quantization/OptimizedScalarQuantizer.java"
LAMBDA, ITERS = 0.1, 5
MINIMUM_MSE_GRID = ((-0.798, 0.798), (-1.493, 1.493), (-2.051, 2.051),
                    (-2.514, 2.514), (-2.916, 2.916), (-3.278, 3.278),
                    (-3.611, 3.611), (-3.922, 3.922))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok: raise RuntimeError(message)


def java_round(values: np.ndarray) -> np.ndarray:
    """Elementwise equivalent of Java Math.round for finite float values."""
    return np.floor(np.asarray(values, dtype=np.float64) + 0.5)


def load_f32(path: Path, rows: int | None = None) -> np.ndarray:
    require(path.stat().st_size % (D * 4) == 0, f"not FP32x{D}: {path}"); actual = path.stat().st_size // (D * 4); require(rows is None or actual == rows, f"unexpected row count: {path}"); return np.asarray(np.memmap(path, mode="r", dtype="<f4", shape=(actual, D)), dtype=np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(score) for doc, score in zip(qids, grades) if int(doc) >= 0 and float(score) > 0}; gains = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64); ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in rel.values()], dtype=np.float64))[::-1][:10]; den = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0; return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den)) if den else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8); out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]; out[:, 4 * b:4 * b + 4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids])); lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level - 1]; high = np.inf if level == 3 else thresholds[d, level]; delta = low - query[d] if query[d] < low else query[d] - high if query[d] > high else 0.0; lut[d, level] = delta * delta
    return ids[np.lexsort((ids, np.sum(lut[np.arange(D)[None, :], levels], axis=1)))[:min(TOP, len(ids))]]


def fit_one(vector: np.ndarray, centroid: np.ndarray, bits: int = 1) -> tuple[np.ndarray, float, float, float, int]:
    """Lucene OptimizedScalarQuantizer for a centered cosine vector.

    ``additionalCorrection`` is the original-vector/centroid dot product.  The
    implementation intentionally follows the Java reference's normalized
    interpolation variable ``s = k/(points-1)``; using the raw integer ``k``
    here changes the coordinate-descent objective and was the old BBQ bug.
    """
    require(1 <= bits <= 8, "BBQ bits must be in [1,8]")
    original = np.asarray(vector, dtype=np.float32)
    c = np.asarray(centroid, dtype=np.float32)
    v = original - c
    norm2 = float(np.dot(v, v)); mean = float(np.mean(v)); std = float(np.std(v)); lo = float(np.min(v)); hi = float(np.max(v))
    a = float(np.clip(MINIMUM_MSE_GRID[bits - 1][0] * std + mean, lo, hi)); b = float(np.clip(MINIMUM_MSE_GRID[bits - 1][1] * std + mean, lo, hi))
    if b <= a: return np.zeros(D, dtype=np.uint8), a, b, 0.0, 0
    def loss(x: float, y: float) -> float:
        step = (y - x) / ((1 << bits) - 1); k = java_round((np.clip(v, x, y) - x) / step); deq = k * step + x; err = v - deq; return float((1.0 - LAMBDA) * np.dot(v, err) ** 2 / max(norm2, 1e-30) + LAMBDA * np.dot(err, err))
    current = loss(a, b)
    for _ in range(ITERS):
        step_inv = ((1 << bits) - 1) / (b - a); k = java_round((np.clip(v, a, b) - a) * step_inv); s = k / ((1 << bits) - 1)
        daa = float(np.dot(1.0 - s, 1.0 - s)); dab = float(np.dot(1.0 - s, s)); dbb = float(np.dot(s, s)); dax = float(np.dot(v, 1.0 - s)); dbx = float(np.dot(v, s)); scale = (1.0 - LAMBDA) / max(norm2, 1e-30); det = (scale * dax * dax + LAMBDA * daa) * (scale * dbx * dbx + LAMBDA * dbb) - (scale * dax * dbx + LAMBDA * dab) ** 2
        if abs(det) < 1e-20: break
        m0 = scale * dax * dax + LAMBDA * daa; m1 = scale * dax * dbx + LAMBDA * dab; m2 = scale * dbx * dbx + LAMBDA * dbb; na = float((m2 * dax - m1 * dbx) / det); nb = float((m0 * dbx - m1 * dax) / det); new = loss(na, nb)
        if nb <= na or not np.isfinite(new) or new > current: break
        a, b, current = na, nb, new
    step = (b - a) / ((1 << bits) - 1); code = java_round((np.clip(v, a, b) - a) / step).astype(np.uint8); return code, a, b, float(np.dot(original, c)), int(np.sum(code))


def unit_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(rows.astype(np.float64), axis=1)
    return (rows / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def fit_block_pca(train: np.ndarray, centroid: np.ndarray, block: int = 8) -> np.ndarray:
    """Fit a deterministic block-diagonal orthogonal preconditioner."""
    rotation = np.eye(D, dtype=np.float32)
    centered = np.asarray(train, dtype=np.float32) - centroid
    for start in range(0, D, block):
        cov = np.asarray(centered[:, start:start + block].T @ centered[:, start:start + block], dtype=np.float64) / max(len(centered), 1)
        _, vectors = np.linalg.eigh(cov)
        for col in range(vectors.shape[1]):
            pivot = int(np.argmax(np.abs(vectors[:, col])))
            if vectors[pivot, col] < 0: vectors[:, col] *= -1
        rotation[start:start + block, start:start + block] = vectors.astype(np.float32)
    return rotation


def mixed_score(query: np.ndarray, qfit: tuple[np.ndarray, float, float, float, int],
                codes: np.ndarray, lows: np.ndarray, highs: np.ndarray,
                corrections: np.ndarray, centroid: np.ndarray) -> np.ndarray:
    """Lucene's mixed residual dot with both centroid corrections."""
    qcode, qlow, qhigh, qcorr, qsum = qfit
    qstep = (qhigh - qlow) / 15.0
    xstep = (highs - lows)
    residual = (lows * qlow * D + qlow * xstep * np.sum(codes, axis=1)
                + lows * qstep * qsum + xstep * qstep * (codes @ qcode))
    return residual + corrections + qcorr - float(np.dot(centroid, centroid))


def self_test() -> None:
    c = np.full(D, 1.0 / np.sqrt(D), dtype=np.float32); x = unit_rows(np.linspace(-1.0, 1.0, D, dtype=np.float32)[None, :])[0]; code, a, b, correction, total = fit_one(x, c); require(code.shape == (D,) and 0 <= total <= D and b >= a and np.isfinite(correction), "BBQ quantizer self-test failed"); rotation = fit_block_pca(np.vstack((x, -x)), c); require(np.max(np.abs(rotation.T @ rotation - np.eye(D))) < 1e-5, "BBQ preconditioner is not orthogonal"); print("Elastic BBQ reference self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--preconditioner", choices=("none", "block-pca"), default="none"); names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output")
    for name in names: parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--artifact", type=Path, help="persisted BBQ payload used by the independent decode audit")
    args = parser.parse_args()
    if args.self_test: self_test(); return
    if any(getattr(args, name.replace("-", "_")) is None for name in names): parser.error("all source and output paths are required")
    docs = load_f32(args.documents, 1_000_000); train = unit_rows(load_f32(args.train_vectors)); queries = unit_rows(load_f32(args.queries, QUERY_COUNT)); qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))); grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))); teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8")); counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64); offsets = np.concatenate(([0], np.cumsum(counts))); total = int(offsets[-1]); records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(total, 148)); candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64); receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8")); require(receipt.get("execution_status") == "EXECUTED" and receipt.get("raw_sha256") == sha256(args.candidate_raw) and receipt.get("flat_file", {}).get("sha256") == sha256(args.candidate_flat), "candidate provenance differs")
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3); thq_codes = np.memmap(args.thq4_codes, mode="r", dtype="<u1", shape=(1_000_000, THQ_BYTES)); selected_rows = [interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]; selected_unique = np.unique(np.concatenate(selected_rows));
    # Lucene's cosine contract uses unit vectors and a segment/corpus centroid.
    # Compute it in float64 so the persisted global metadata is deterministic.
    centroid = np.zeros(D, dtype=np.float64)
    for start in range(0, len(docs), 65536): centroid += np.asarray(docs[start:start + 65536], dtype=np.float64).sum(axis=0)
    centroid = centroid / max(len(docs), 1); centroid = centroid / max(float(np.linalg.norm(centroid)), np.finfo(np.float64).tiny); centroid = centroid.astype(np.float32)
    rotation = fit_block_pca(train, centroid) if args.preconditioner == "block-pca" else np.eye(D, dtype=np.float32)
    transformed_centroid = centroid @ rotation
    selected_original = unit_rows(np.asarray(docs[selected_unique], dtype=np.float32)); selected_transformed = selected_original @ rotation
    fitted = [fit_one(row, transformed_centroid) for row in selected_transformed]; codes = np.stack([x[0] for x in fitted]); lows = np.asarray([x[1] for x in fitted], np.float32); highs = np.asarray([x[2] for x in fitted], np.float32); corrections = np.asarray([x[3] for x in fitted], np.float32); pos = {int(doc): i for i, doc in enumerate(selected_unique)}
    decoded_centered = lows[:, None] + (highs - lows)[:, None] * codes; decoded_transformed = transformed_centroid[None, :] + decoded_centered; decoded = decoded_transformed @ rotation.T; rows = []
    artifact = args.artifact or args.output.with_suffix(".payload.npz")
    selected_flat = np.concatenate(selected_rows).astype(np.int64)
    offsets_out = np.concatenate(([0], np.cumsum([len(row) for row in selected_rows], dtype=np.int64)))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez(artifact, document_ids=np.asarray(selected_unique, dtype=np.int64), base=np.broadcast_to(centroid, (len(selected_unique), D)).astype(np.float32), centroid=np.asarray(centroid, dtype=np.float32), rotation=np.asarray(rotation, dtype=np.float32), codes=np.asarray(codes, dtype=np.uint8), lows=np.asarray(lows, dtype=np.float32), highs=np.asarray(highs, dtype=np.float32), corrections=np.asarray(corrections, dtype=np.float32), row_ids=selected_flat, row_offsets=offsets_out)
    for qi, query in enumerate(queries):
        ids = selected_rows[qi]; indexes = np.asarray([pos[int(doc)] for doc in ids]); reconstructed = decoded[indexes]; scores = (reconstructed @ query) / np.maximum(np.linalg.norm(reconstructed, axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny); ranked = top_ids(scores, ids, 10); rows.append({"query": qi, "arm": "bbq_lucene_direct", "top10_ids": ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], ranked).sum() / 10), "side_payload_bytes": 62, "aligned_payload_bytes": 64, "cascade_total_bytes": THQ_BYTES + 62})
        # Lucene's asymmetric BBQ shape: 1-bit document residual and 4-bit
        # query residual.  The THQ centroid contribution is kept exact; the
        # corrected OSQ term ranks the residual without reconstructing it.
        qfit = fit_one(query @ rotation, transformed_centroid, bits=4); asymmetric_scores = mixed_score(query, qfit, codes[indexes].astype(np.float64), lows[indexes].astype(np.float64), highs[indexes].astype(np.float64), corrections[indexes].astype(np.float64), transformed_centroid); asymmetric_ranked = top_ids(asymmetric_scores, ids, 10); rows.append({"query": qi, "arm": "bbq_lucene_asymmetric", "top10_ids": asymmetric_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(asymmetric_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], asymmetric_ranked).sum() / 10), "side_payload_bytes": 62, "aligned_payload_bytes": 64, "query_bits": 4, "query_ephemeral_bytes": D // 2, "cascade_total_bytes": THQ_BYTES + 62})
    summaries = {arm: {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows if r["arm"] == arm])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rows if r["arm"] == arm], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rows if r["arm"] == arm]))} for arm in ("bbq_lucene_direct", "bbq_lucene_asymmetric")}; sources = {name: getattr(args, name.replace("-", "_")) for name in names[:-1]}; result = {"schema_version": 4, "family": "thq_elastic_bbq_reference_gate_c_v2", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "codec_metric": "Lucene OSQ centered cosine; direct decode and mixed asymmetric scorer", "preconditioner": args.preconditioner, "reference_revision": LUCENE_REVISION, "reference_source": LUCENE_SOURCE, "bits": 1, "query_bits_control": 4, "lambda": LAMBDA, "iters": ITERS, "inline_trailer": "float lowerInterval, float upperInterval, float additionalCorrection, uint16 quantizedComponentSum", "query_correction_formula": "doc_centroid_dot + query_centroid_dot - centroid_norm2 + ax*ay*D + ay*lx*doc_sum + ax*ly*query_sum + lx*ly*quantized_dot", "query_count": QUERY_COUNT, "selected_unique_documents": int(len(selected_unique)), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "artifact_path": str(artifact), "artifact_sha256": sha256(artifact), "payload_contract": {"logical_side_payload_bytes": 62, "aligned_side_payload_bytes": 64, "persisted_fields": ["document_ids", "centroid", "rotation", "codes", "lows", "highs", "corrections", "row_ids", "row_offsets"], "global_metadata_bytes": int(centroid.nbytes + rotation.nbytes)}, "summaries": summaries, "rows": rows, "limitations": ["portable scalar control, not native Elastic SIMD or BBQ-disk serving", "candidate-local THQ top128 replay; oversampling/rescore is a separate diagnostic", "reference source is pinned to Lucene revision, not a released compatibility promise"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
