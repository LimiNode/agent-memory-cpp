#!/usr/bin/env python3
"""Bounded source-pinned AAQ residual pilot using the first-author code.

The official AAQ fit/coordinate-descent implementation is imported from an
external checkout.  To keep the CPU pilot bounded, residuals are projected to
32 dimensions and the paper repository's 8x16 (4-byte) configuration is used.
This is a faithful AAQ mechanics pilot, not a full-dimensional 32/48-byte arm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D, PROJECTED_D, THQ_BYTES, QUERY_COUNT, TOP = 384, 32, 96, 152, 128
M, K = 8, 16


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(codes: np.ndarray) -> np.ndarray:
    packed = np.asarray(codes, dtype=np.uint8)
    levels = np.empty((len(packed), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = packed[:, byte]
        levels[:, 4 * byte : 4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1
        )
    return levels


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids])
    ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in rel.values()]))[::-1][:10]
    dcg = float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("aaq-root", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "lsq-models", "lsq-codes", "output", "artifact"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--train-rows", type=int, default=4096)
    p.add_argument("--fit-iters", type=int, default=1)
    p.add_argument("--coordinate-passes", type=int, default=3)
    p.add_argument("--threshold", type=float, default=0.2)
    p.add_argument("--pool-size", type=int, default=4)
    a = p.parse_args()
    if a.train_rows < M * K or a.fit_iters < 1 or a.coordinate_passes < 1 or not 0.0 < a.threshold < 1.0:
        p.error("invalid bounded AAQ configuration")

    root = a.aaq_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root / "src"))
    from encoding import CoordinateDes, multiprocessing_index
    from scannAQ import ScannAQ
    from scannAQ_function import codebook_init

    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    train = np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(25_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    grades = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(3, D)
    models = np.load(a.lsq_models, allow_pickle=False)
    centroids = np.asarray(models["centroids"], dtype=np.float32)
    source_codes = np.load(a.lsq_codes, allow_pickle=False)
    selected = np.asarray(source_codes["selected_ids"], dtype=np.int64)
    unique_ids = np.unique(selected)

    train_values = np.asarray(train[: a.train_rows], dtype=np.float32)
    train_levels = np.sum(train_values[:, None, :] > thresholds[None, :, :], axis=1)
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train_values - train_base
    residual_mean = train_residual.mean(axis=0, dtype=np.float64).astype(np.float32)
    _, _, vt = np.linalg.svd(np.asarray(train_residual - residual_mean, dtype=np.float32), full_matrices=False)
    components = np.asarray(vt[:PROJECTED_D], dtype=np.float32)
    projected_train = np.asarray((train_residual - residual_mean) @ components.T, dtype=np.float64)
    projected_norms = np.linalg.norm(projected_train, axis=1)
    normalized_train = projected_train / np.maximum(projected_norms[:, None], 1e-30)

    parameter = SimpleNamespace(M=M, K=K, poolSize=a.pool_size, encodeidea=CoordinateDes, N=16, nor=1, T=a.threshold, iter_num=a.coordinate_passes)
    parameter.D = PROJECTED_D
    parameter.eta = (PROJECTED_D - 1) * a.threshold**2 / (1.0 - a.threshold**2)
    codebook = codebook_init(normalized_train, M, K)
    trainer = ScannAQ(normalized_train, parameter)
    codebook, _, _ = trainer.codebook_train(normalized_train, codebook, parameter, maxiter=a.fit_iters)

    exact_unique = np.asarray(docs[unique_ids], dtype=np.float32)
    unique_levels = unpack(np.asarray(thq[unique_ids]))
    unique_base = centroids[np.arange(D)[None, :], unique_levels]
    unique_residual = exact_unique - unique_base
    projected = np.asarray((unique_residual - residual_mean) @ components.T, dtype=np.float64)
    residual_scales = np.linalg.norm(projected, axis=1).astype(np.float32)
    normalized = projected / np.maximum(residual_scales[:, None], 1e-30)
    _, local_codes = multiprocessing_index(normalized, np.zeros((len(normalized), 2)), codebook, parameter, a.pool_size)
    decoded_projected = np.sum(codebook[:, local_codes + np.arange(M) * K], axis=2).T
    decoded_residual = (decoded_projected * residual_scales[:, None]) @ components + residual_mean
    projected_upper_residual = projected @ components + residual_mean
    reconstructed = unique_base + decoded_residual.astype(np.float32)
    projected_upper = unique_base + projected_upper_residual.astype(np.float32)
    final_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32)
    position = {int(doc): index for index, doc in enumerate(unique_ids)}

    rows = []
    for qi, query in enumerate(np.asarray(queries, dtype=np.float32)):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        for arm, values, side_bytes in (
            ("pca32_projection_upper", projected_upper[indexes], 0),
            ("official_aaq_pca32_m8k16", reconstructed[indexes], 12),
        ):
            rank = top_ids(cosine(values, query), ids)
            rows.append({"query": qi, "arm": arm, "top10_ids": rank.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(rank, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], rank).sum() / 10.0), "side_payload_bytes": side_bytes, "cascade_total_bytes": THQ_BYTES + side_bytes})

    a.artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.artifact, selected_ids=selected, unique_ids=unique_ids, codes=local_codes.astype(np.uint8), codebook=np.asarray(codebook, dtype=np.float32), centroids=centroids, residual_mean=residual_mean, components=components, residual_scales=residual_scales, final_norms=final_norms)
    summaries = {}
    for arm in sorted({row["arm"] for row in rows}):
        arm_rows = [row for row in rows if row["arm"] == arm]
        summaries[arm] = {"mean_qrels_ndcg10": float(np.mean([row["qrels_ndcg10"] for row in arm_rows])), "p05_qrels_ndcg10": float(np.percentile([row["qrels_ndcg10"] for row in arm_rows], 5)), "worst_qrels_ndcg10": float(np.min([row["qrels_ndcg10"] for row in arm_rows])), "mean_teacher_overlap": float(np.mean([row["teacher_overlap"] for row in arm_rows])), "side_payload_bytes": arm_rows[0]["side_payload_bytes"], "cascade_total_bytes": arm_rows[0]["cascade_total_bytes"]}
    global_model_bytes = int(codebook.nbytes + centroids.nbytes + residual_mean.nbytes + components.nbytes)
    sources = {name: getattr(a, name.replace("-", "_")) for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "lsq-models", "lsq-codes")}
    result = {"schema_version": 1, "family": "thq_official_aaq_pca32_bounded_pilot_v1", "status": "EXECUTED", "source_replay": True, "quality_status": "BOUNDED_PILOT", "metric": "cosine", "upstream_repository": "https://github.com/jzhang-0/Anisotropic-Additive-Quantization", "upstream_revision": revision, "upstream_license_status": "NO_LICENSE_FILE_OBSERVED", "official_source_usage": ["ScannAQ.codebook_train", "encoding.CoordinateDes", "encoding.multiprocessing_index", "scannAQ_function.codebook_init"], "config": {"train_rows": a.train_rows, "projected_dimensions": PROJECTED_D, "M": M, "K": K, "code_bytes": 4, "residual_scale_bytes": 4, "final_norm_bytes": 4, "fit_iters": a.fit_iters, "coordinate_passes": a.coordinate_passes, "threshold": a.threshold, "eta": parameter.eta, "pool_size": a.pool_size}, "payload_contract": {"final_norm_included": True, "side_payload_bytes": 12, "fields": ["4-byte M8K16 code", "FP32 projected residual scale", "FP32 final norm"]}, "candidate_stream_hash": "d76cabd553bbd1453908a9cd28fe3578895cf2cd3876026a5b1fd5813839bc79", "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "artifact_sha256": sha256(a.artifact), "global_model_bytes": global_model_bytes, "summaries": summaries, "rows": rows, "limitations": ["official AAQ mechanics but bounded PCA32 residual pilot, not full-dimensional AAQ", "4-byte M8K16 code follows the upstream example and is not a 32/48-byte capacity comparison", "first canonical training rows only; no qrels tuning", "FP32 residual scale and final norm charged in 12-byte side payload", "external unlicensed checkout is source-pinned and no upstream code is vendored", "candidate-local quality only; no native timing"]}
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
