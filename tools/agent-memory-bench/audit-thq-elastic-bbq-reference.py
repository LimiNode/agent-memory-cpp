#!/usr/bin/env python3
"""Fail-closed audit for the source-bound Lucene BBQ controls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, QUERY_COUNT = 384, 152
REVISION = "0b346c5141e5ffeca8129a0280fa55768a19c643"
GRID = ((-0.798, 0.798), (-1.493, 1.493), (-2.051, 2.051), (-2.514, 2.514),
        (-2.916, 2.916), (-3.278, 3.278), (-3.611, 3.611), (-3.922, 3.922))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]]


def unit_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(rows.astype(np.float64), axis=1)
    return (rows / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def replay_centroid(path: Path) -> tuple[np.ndarray, dict[str, float]]:
    require(path.stat().st_size % (D * 4) == 0, "documents are not FP32x384")
    count = path.stat().st_size // (D * 4)
    values = np.memmap(path, mode="r", dtype="<f4", shape=(count, D))
    total = np.zeros(D, dtype=np.float64)
    norm_min, norm_max, norm_deviation = np.inf, 0.0, 0.0
    for start in range(0, count, 65536):
        batch = np.asarray(values[start:start + 65536], dtype=np.float64)
        norms = np.linalg.norm(batch, axis=1)
        total += (batch / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).sum(axis=0)
        norm_min = min(norm_min, float(np.min(norms)))
        norm_max = max(norm_max, float(np.max(norms)))
        norm_deviation = max(norm_deviation, float(np.max(np.abs(norms - 1.0))))
    centroid = total / max(count, 1)
    centroid /= max(float(np.linalg.norm(centroid)), np.finfo(np.float64).tiny)
    return centroid.astype(np.float32), {"min": norm_min, "max": norm_max, "max_abs_deviation_from_one": norm_deviation}


def java_round(values: np.ndarray) -> np.ndarray:
    return np.floor(np.asarray(values, dtype=np.float64) + 0.5)


def query_quantize(vector: np.ndarray, centroid: np.ndarray, bits: int = 4) -> tuple[np.ndarray, float, float, float, int]:
    original = np.asarray(vector, dtype=np.float32); c = np.asarray(centroid, dtype=np.float32); v = original - c
    norm2 = float(np.dot(v, v)); mean = float(np.mean(v)); std = float(np.std(v)); lo = float(np.min(v)); hi = float(np.max(v)); a = float(np.clip(GRID[bits - 1][0] * std + mean, lo, hi)); b = float(np.clip(GRID[bits - 1][1] * std + mean, lo, hi))
    if b <= a:
        return np.zeros(len(v), dtype=np.uint8), a, b, float(np.dot(original, c)), 0
    def loss(x: float, y: float) -> float:
        step = (y - x) / ((1 << bits) - 1); k = java_round((np.clip(v, x, y) - x) / step); err = v - (k * step + x)
        return float(0.9 * np.dot(v, err) ** 2 / max(norm2, 1e-30) + 0.1 * np.dot(err, err))
    current = loss(a, b)
    for _ in range(5):
        step_inv = ((1 << bits) - 1) / (b - a); k = java_round((np.clip(v, a, b) - a) * step_inv); s = k / ((1 << bits) - 1); daa = float(np.dot(1.0 - s, 1.0 - s)); dab = float(np.dot(1.0 - s, s)); dbb = float(np.dot(s, s)); dax = float(np.dot(v, 1.0 - s)); dbx = float(np.dot(v, s)); scale = 0.9 / max(norm2, 1e-30); m0 = scale * dax * dax + 0.1 * daa; m1 = scale * dax * dbx + 0.1 * dab; m2 = scale * dbx * dbx + 0.1 * dbb; det = m0 * m2 - m1 * m1
        if abs(det) < 1e-20: break
        na = float((m2 * dax - m1 * dbx) / det); nb = float((m0 * dbx - m1 * dax) / det); new = loss(na, nb)
        if nb <= na or not np.isfinite(new) or new > current: break
        a, b, current = na, nb, new
    step = (b - a) / ((1 << bits) - 1); code = java_round((np.clip(v, a, b) - a) / step).astype(np.uint8)
    return code, a, b, float(np.dot(original, c)), int(np.sum(code))


def mixed_score(qfit: tuple[np.ndarray, float, float, float, int], codes: np.ndarray,
                lows: np.ndarray, highs: np.ndarray, corrections: np.ndarray,
                centroid: np.ndarray) -> np.ndarray:
    qcode, qlow, qhigh, qcorr, qsum = qfit; qstep = (qhigh - qlow) / 15.0; xstep = highs - lows
    residual = lows * qlow * D + qlow * xstep * np.sum(codes, axis=1) + lows * qstep * qsum + xstep * qstep * (codes @ qcode)
    return residual + corrections + qcorr - float(np.dot(centroid, centroid))


def self_test() -> None:
    c = np.full(D, 1.0 / np.sqrt(D), dtype=np.float32); q, a, b, corr, total = query_quantize(c, c)
    require(q.shape == (D,) and a <= b and np.isfinite(corr) and 0 <= total <= 15 * D, "BBQ audit self-test failed")
    print("Elastic BBQ reference audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path); parser.add_argument("--runner", type=Path); parser.add_argument("--artifact", type=Path); parser.add_argument("--documents", type=Path); parser.add_argument("--train-vectors", dest="train_vectors", type=Path); parser.add_argument("--queries", type=Path); parser.add_argument("--qrel-ids", dest="qrel_ids", type=Path); parser.add_argument("--qrel-scores", dest="qrel_scores", type=Path); parser.add_argument("--teacher-ids", dest="teacher_ids", type=Path); parser.add_argument("--thq4-codes", dest="thq4_codes", type=Path); parser.add_argument("--thq4-thresholds", dest="thq4_thresholds", type=Path); parser.add_argument("--candidate-flat", dest="candidate_flat", type=Path); parser.add_argument("--candidate-raw", dest="candidate_raw", type=Path); parser.add_argument("--candidate-receipt", dest="candidate_receipt", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test: self_test(); return
    source_args = (args.documents, args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores, args.teacher_ids, args.thq4_codes, args.thq4_thresholds, args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    if any(value is None for value in (args.result, args.runner, args.artifact, *source_args, args.output)): parser.error("result, runner, artifact, all source paths, and output are required")
    result = json.loads(args.result.read_text(encoding="utf-8")); rows = result.get("rows", [])
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True and result.get("schema_version") == 5, "result is not the v3 source replay")
    require(result.get("reference_revision") == REVISION and result.get("bits") == 1 and result.get("query_bits_control") == 4, "BBQ source or bit contract differs")
    require(len(rows) == 304 and all(int(row.get("side_payload_bytes")) == 62 and int(row.get("aligned_payload_bytes")) == 64 for row in rows), "BBQ row or payload accounting differs")
    require(result.get("runner_sha256") == sha256(args.runner), "result runner binding differs")
    expected_sources = {"documents": args.documents, "train-vectors": args.train_vectors, "queries": args.queries, "qrel-ids": args.qrel_ids, "qrel-scores": args.qrel_scores, "teacher-ids": args.teacher_ids, "thq4-codes": args.thq4_codes, "thq4-thresholds": args.thq4_thresholds, "candidate-flat": args.candidate_flat, "candidate-raw": args.candidate_raw, "candidate-receipt": args.candidate_receipt}
    require(result.get("artifact_sha256") == sha256(args.artifact) and all(result.get("source_hashes", {}).get(name) == sha256(path) for name, path in expected_sources.items()), "artifact or source binding differs")
    with np.load(args.artifact, allow_pickle=False) as payload:
        document_ids = np.asarray(payload["document_ids"], dtype=np.int64); base = np.asarray(payload["base"], dtype=np.float32); centroid = np.asarray(payload["centroid"], dtype=np.float32); rotation = np.asarray(payload["rotation"], dtype=np.float32); codes = np.asarray(payload["codes"], dtype=np.uint8); lows = np.asarray(payload["lows"], dtype=np.float32); highs = np.asarray(payload["highs"], dtype=np.float32); corrections = np.asarray(payload["corrections"], dtype=np.float32); row_ids = np.asarray(payload["row_ids"], dtype=np.int64); row_offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    replayed_centroid, norm_diagnostics = replay_centroid(args.documents)
    require(base.shape == codes.shape and base.shape[1:] == (D,) and lows.shape == highs.shape == corrections.shape == (len(document_ids),), "persisted BBQ payload shapes differ")
    require(rotation.shape == (D, D) and np.max(np.abs(rotation.T @ rotation - np.eye(D))) < 5e-5, "preconditioner is not orthogonal")
    require(row_offsets.shape == (QUERY_COUNT + 1,) and row_offsets[-1] == len(row_ids), "persisted row offsets differ")
    require(np.max(np.abs(base - centroid[None, :])) < 2e-6 and np.max(np.abs(replayed_centroid - centroid)) < 3e-6 and abs(float(np.linalg.norm(centroid)) - 1.0) < 2e-5, "corpus centroid source replay differs")
    positions = {int(doc): i for i, doc in enumerate(document_ids)}; queries = unit_rows(np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)), dtype=np.float32)); mismatches = {"bbq_lucene_direct": 0, "bbq_lucene_asymmetric": 0}
    # The persisted intervals are fitted in the preconditioned frame.  Decode
    # there first, then map back to the original cosine frame; using the raw
    # centroid before the inverse rotation silently invalidates block-PCA.
    transformed_centroid = centroid @ rotation
    decoded_centered = lows[:, None] + (highs - lows)[:, None] * codes
    decoded = (transformed_centroid[None, :] + decoded_centered) @ rotation.T
    for row in rows:
        qi = int(row["query"]); ids = np.asarray(row["thq4_top128_ids"], dtype=np.int64); persisted = row_ids[row_offsets[qi]:row_offsets[qi + 1]]; require(np.array_equal(ids, persisted), f"persisted row IDs differ at query {qi}"); indexes = np.asarray([positions[int(doc)] for doc in ids]); q = queries[qi]
        if row["arm"] == "bbq_lucene_direct":
            values = decoded[indexes]; scores = (values @ q) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
        else:
            qfit = query_quantize(q @ rotation, transformed_centroid); scores = mixed_score(qfit, codes[indexes].astype(np.float64), lows[indexes].astype(np.float64), highs[indexes].astype(np.float64), corrections[indexes].astype(np.float64), transformed_centroid)
        ranked = top_ids(scores, ids, 10); mismatches[row["arm"]] += int(not np.array_equal(ranked, np.asarray(row["top10_ids"], dtype=np.int64)))
    require(all(value == 0 for value in mismatches.values()), f"independent BBQ top10 mismatches: {mismatches}")
    audit = {"schema_version": 5, "family": "thq_elastic_bbq_reference_audit_v3", "status": "PASS", "source_binding": True, "independent_decode_replay": True, "centroid_source_replay": True, "result_sha256": sha256(args.result), "runner_sha256": sha256(args.runner), "artifact_sha256": sha256(args.artifact), "query_sha256": sha256(args.queries), "documents_sha256": sha256(args.documents), "document_norm_diagnostics": norm_diagnostics, "preconditioner_contract": result.get("preconditioner_contract"), "row_count": len(rows), "independent_decode_top10_mismatch_count": mismatches, "checks": ["Lucene revision and source binding", "all canonical source SHA-256 bindings", "independent unit-normalized document centroid replay", "exact normalized coordinate-descent interpolation", "block-orthogonal preconditioner parity", "persisted payload decode and direct/asymmetric top10 parity", "logical/aligned payload accounting"], "limitations": ["portable scalar controls, not native Elastic SIMD or BBQ-disk serving", "candidate-local THQ top128 replay; oversampling/rescore remains separate"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print("Elastic BBQ reference audit PASS")


if __name__ == "__main__": main()
