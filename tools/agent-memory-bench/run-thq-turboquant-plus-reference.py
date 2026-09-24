#!/usr/bin/env python3
"""Source-bound Qdrant TurboQuant+ Bits1 residual-composite control.

This runner follows the pinned Qdrant ``TQMode::Plus`` Bits1 calibration on the
THQ candidate shell.  It persists the 48-byte sign code, Qdrant-style
scaling-factor and error-correction scalar, and evaluates both an ideal float
lane and a scalar ``QuerySimd<8,2>`` lane with the residual composite-cosine
denominator.  It is a source-bound reference, not a wire-format claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from importlib.util import module_from_spec, spec_from_file_location

_normal_path = Path(__file__).with_name("run-thq-turboquant-reference.py")
_spec = spec_from_file_location("tq_normal", _normal_path)
_normal = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_normal)

D, THQ_BYTES, TOP, QUERY_COUNT = _normal.D, _normal.THQ_BYTES, _normal.TOP, _normal.QUERY_COUNT
UPSTREAM_REVISION = _normal.UPSTREAM_REVISION
UPSTREAM_SOURCE = _normal.UPSTREAM_SOURCE


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def unit_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(rows.astype(np.float64), axis=1)
    return (rows / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    return _normal.unpack_thq(codes)


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    return _normal.interval_top(query, ids, codes, thresholds)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return _normal.top_ids(scores, ids, count)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    return _normal.ndcg10(ids, qids, grades)


def fit_levels(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0)
    for d in range(D):
        for level in range(4):
            values = train[levels[:, d] == level, d]
            centroids[d, level] = np.mean(values) if len(values) else fallback[d]
    return centroids


class P2:
    """Qdrant's seven-marker streaming P-square estimator."""
    def __init__(self, probability: float) -> None:
        self.p = float(probability); self.values: list[float] = []
        self.h = self.n = self.np = None
        self.count = 0
        self.targets = np.asarray([0.0, self.p * .5, self.p * .8, self.p,
                                   1.0 + (self.p - 1.0) * .8,
                                   1.0 + (self.p - 1.0) * .5, 1.0])
    def push(self, value: float) -> None:
        self.count += 1
        if self.h is None:
            self.values.append(float(value))
            if len(self.values) < 7: return
            self.values.sort(); self.h = np.asarray(self.values, dtype=np.float64)
            self.n = np.arange(1, 8, dtype=np.float64)
            self.np = 1.0 + 6.0 * self.targets
            return
        if value < self.h[0]: self.h[0] = value; k = 0
        elif value > self.h[6]: self.h[6] = value; k = 5
        else: k = min(5, int(np.searchsorted(self.h[1:], value, side="left")))
        self.n[k + 1:] += 1.0
        self.np = 1.0 + self.targets * (self.count - 1.0)
        for i in range(1, 6):
            while True:
                d = self.np[i] - self.n[i]
                if d >= 1.0 and self.n[i + 1] - self.n[i] > 1.0: sign = 1.0
                elif d <= -1.0 and self.n[i - 1] - self.n[i] < -1.0: sign = -1.0
                else: break
                prev_h, cur_h, next_h = self.h[i - 1:i + 2]
                prev_n, cur_n, next_n = self.n[i - 1:i + 2]
                q = cur_h + sign / (next_n - prev_n) * ((cur_n - prev_n + sign) * (next_h - cur_h) / (next_n - cur_n) + (next_n - cur_n - sign) * (cur_h - prev_h) / (cur_n - prev_n))
                if not np.isfinite(q) or not prev_h < q < next_h:
                    j = i + (1 if sign > 0 else -1)
                    q = cur_h + sign * (self.h[j] - cur_h) / (self.n[j] - cur_n)
                self.h[i] = q; self.n[i] += sign
    def estimate(self) -> float:
        return float(np.quantile(self.values, self.p)) if self.h is None else float(self.h[3])


def fit_error_correction(train_residual: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, dict[str, object]]:
    rotated = _normal.rotate(train_residual)
    lengths = np.linalg.norm(rotated.astype(np.float64), axis=1)
    pre = rotated * (np.sqrt(float(D)) / np.maximum(lengths, 1e-12))[:, None]
    outer = float(np.sqrt(2.0 / np.pi))
    p_outer = 0.5 * (1.0 + math.erf(outer / math.sqrt(2.0)))
    low_p, high_p = 1.0 - p_outer, p_outer
    sample = pre[: min(2048, len(pre))]
    low = [P2(low_p) for _ in range(D)]; high = [P2(high_p) for _ in range(D)]
    for row in sample:
        for d, value in enumerate(row): low[d].push(float(value)); high[d].push(float(value))
    lows = np.asarray([e.estimate() for e in low], dtype=np.float64)
    highs = np.asarray([e.estimate() for e in high], dtype=np.float64)
    width = highs - lows
    shift = -(lows + highs) / 2.0
    scale = np.where(width > 1e-3, 2.0 * outer / width, 1.0)
    return shift.astype(np.float32), scale.astype(np.float32), outer, {"sample_size": int(len(sample)), "sample_policy": "first 2048 rows of the fixed canonical train stream", "estimator": "P2Quantile<7>", "quantile_interval": [low_p, high_p], "min_quantile_width": 1e-3, "outer_centroid": outer}


def encode_wide_query(values: np.ndarray) -> tuple[np.ndarray, np.float32]:
    """Scalar reference for Qdrant QuerySimd<8,2> integer quantization."""
    values = np.asarray(values, dtype=np.float32)
    q_abs = np.float32(max(float(np.max(np.abs(values))), float(np.finfo(np.float32).eps)))
    q_scale = np.float32(32639.0 / q_abs)
    scaled = values * q_scale
    signed = np.where(scaled >= 0.0, np.floor(scaled + 0.5),
                      np.ceil(scaled - 0.5)).astype(np.int32)
    return np.clip(signed, -32639, 32639).astype(np.int32), q_scale


def balanced_radix256_planes(signed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Scalar equivalent of the two signed-byte QueryPlanes<8,2> split."""
    values = np.asarray(signed, dtype=np.int32)
    high = np.floor_divide(values + 128, 256)
    low = values - high * 256
    require(np.all(low >= -128) and np.all(low <= 127), "radix low plane overflow")
    require(np.all(high >= -128) and np.all(high <= 127), "radix high plane overflow")
    return low.astype(np.int8), high.astype(np.int8)


def wide_dot(sign_codes: np.ndarray, values: np.ndarray, outer: float) -> float:
    """Exact scalar Query1bitWideSimd score before EC correction."""
    signed, q_scale = encode_wide_query(values)
    low, high = balanced_radix256_planes(signed)
    signs = (np.asarray(sign_codes, dtype=np.int64) * 2) - 1
    dot = int(np.dot(signs, low.astype(np.int64)))
    dot += 256 * int(np.dot(signs, high.astype(np.int64)))
    return float(np.float32((float(outer) * dot) / float(q_scale)))


def self_test() -> None:
    signed = np.arange(-32639, 32640, dtype=np.int32)
    low, high = balanced_radix256_planes(signed)
    replayed = low.astype(np.int32) + 256 * high.astype(np.int32)
    require(np.array_equal(replayed, signed), "exhaustive balanced radix-256 replay differs")
    values = np.asarray([-1.0, -0.501, -1e-6, 0.0, 1e-6, 0.501, 1.0], dtype=np.float32)
    quantized, _ = encode_wide_query(values)
    require(int(quantized[0]) == -32639 and int(quantized[-1]) == 32639,
            "wide-query endpoint quantization differs")
    shift = np.linspace(-0.1, 0.1, D, dtype=np.float32)
    scale = np.linspace(0.75, 1.25, D, dtype=np.float32)
    zero = np.zeros((1, D), dtype=np.float32)
    levels, factors, _, _, _ = encode_plus(zero, shift, scale, float(np.sqrt(2.0 / np.pi)))
    decoded = decode_plus(levels, factors, shift, scale, float(np.sqrt(2.0 / np.pi)))
    require(float(factors[0]) == 0.0 and np.array_equal(decoded, zero), "zero-residual fallback differs")
    print("TurboQuant+ scalar reference self-test: PASS")


def encode_plus(residual: np.ndarray, shift: np.ndarray, scale: np.ndarray, outer: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rotated = _normal.rotate(residual)
    lengths = np.linalg.norm(rotated.astype(np.float64), axis=1).astype(np.float32)
    safe = np.maximum(lengths, 1e-12)
    pre = rotated * (np.sqrt(float(D)) / safe)[:, None]
    ec = np.sum(pre * (-shift[None, :]), axis=1).astype(np.float32)
    plus = (pre + shift[None, :]) * scale[None, :]
    levels = (plus > 0.0).astype(np.uint8)
    transformed = (pre + shift[None, :]) * scale[None, :]
    quantized_centroid = ((2.0 * levels.astype(np.float32) - 1.0) * outer) / scale[None, :] - shift[None, :]
    centroid_norm = np.linalg.norm(quantized_centroid.astype(np.float64), axis=1).astype(np.float32)
    scaling_factor = (lengths / np.maximum(centroid_norm, 1e-12)).astype(np.float32)
    return levels, scaling_factor, ec, pre, centroid_norm


def decode_plus(levels: np.ndarray, scaling_factor: np.ndarray, shift: np.ndarray, scale: np.ndarray, outer: float) -> np.ndarray:
    values = (((2.0 * levels.astype(np.float32) - 1.0) * outer) / scale[None, :]) - shift[None, :]
    centroid_norm = np.linalg.norm(values.astype(np.float64), axis=1).astype(np.float32)
    lengths = scaling_factor * centroid_norm
    pre = values * (lengths / np.sqrt(float(D)))[:, None]
    return _normal.inverse_rotate(pre)


def main() -> None:
    parser = argparse.ArgumentParser()
    names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output")
    for name in names:
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.artifact is None:
        parser.error("--artifact is required outside --self-test")
    if any(getattr(args, name.replace("-", "_")) is None for name in names):
        parser.error("all source and output paths are required")

    docs = _normal.load_f32(args.documents, 1_000_000)
    train = unit_rows(_normal.load_f32(args.train_vectors))
    queries = unit_rows(_normal.load_f32(args.queries, QUERY_COUNT))
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    record_bytes = int(json.loads(args.candidate_receipt.read_text(encoding="utf-8")).get("flat_file", {}).get("record_bytes", 148))
    require(record_bytes in (100, 148) and args.candidate_flat.stat().st_size == int(offsets[-1]) * record_bytes, "candidate record layout differs")
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes))
    candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(receipt.get("execution_status") == "EXECUTED" and receipt.get("raw_sha256") == sha256(args.candidate_raw) and receipt.get("flat_file", {}).get("sha256") == sha256(args.candidate_flat), "candidate provenance differs")
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    train_centroids = fit_levels(train, thresholds)
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    train_base = train_centroids[np.arange(D)[None, :], train_levels]
    shift, scale, outer, fit_contract = fit_error_correction(train - train_base)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    selected_rows = [interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]
    selected_unique = np.unique(np.concatenate(selected_rows))
    levels = unpack_thq(np.asarray(thq_codes[selected_unique]))
    base = train_centroids[np.arange(D)[None, :], levels]
    residual = np.asarray(docs[selected_unique], dtype=np.float32) - base
    codes, scaling_factors, ec, _, centroid_norms = encode_plus(residual, shift, scale, outer)
    decoded = decode_plus(codes, scaling_factors, shift, scale, outer)
    composite_norms = np.linalg.norm(base + decoded, axis=1).astype(np.float32)
    pos = {int(doc): i for i, doc in enumerate(selected_unique)}
    row_ids = np.concatenate(selected_rows).astype(np.int64)
    row_offsets = np.concatenate(([0], np.cumsum([len(row) for row in selected_rows], dtype=np.int64)))
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.artifact, document_ids=np.asarray(selected_unique, dtype=np.int64), base=np.asarray(base, dtype=np.float32), codes=np.asarray(codes, dtype=np.uint8), scaling_factors=np.asarray(scaling_factors, dtype=np.float32), centroid_norms=np.asarray(centroid_norms, dtype=np.float32), final_norms=np.asarray(composite_norms, dtype=np.float32), ec_correction=np.asarray(ec, dtype=np.float32), shift=shift, scale=scale, row_ids=row_ids, row_offsets=row_offsets)
    rows = []
    for qi, query in enumerate(queries):
        ids = selected_rows[qi]
        indexes = np.asarray([pos[int(doc)] for doc in ids])
        direct_vec = base[indexes] + decoded[indexes]
        direct_scores = (direct_vec @ query) / np.maximum(np.linalg.norm(direct_vec, axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny)
        direct_ranked = top_ids(direct_scores, ids, 10)
        rows.append({"query": qi, "arm": "turboquant_plus1_direct", "top10_ids": direct_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(direct_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], direct_ranked).sum() / 10), "side_payload_bytes": 56, "cascade_total_bytes": THQ_BYTES + 56})
        rotated_q = _normal.rotate(query[None, :])[0]
        q_plus = (rotated_q / scale).astype(np.float32)
        qm = float(np.dot(rotated_q, -shift))
        ideal_residual = (decoded[indexes] @ query)
        ideal_scores = ((base[indexes] @ query) + ideal_residual) / np.maximum(composite_norms[indexes], 1e-12)
        ideal_ranked = top_ids(ideal_scores, ids, 10)
        rows.append({"query": qi, "arm": "turboquant_plus1_float_composite", "top10_ids": ideal_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(ideal_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], ideal_ranked).sum() / 10), "side_payload_bytes": 60, "cascade_total_bytes": THQ_BYTES + 60})
        wide_scores = np.asarray([(base[int(pos[int(doc)])] @ query + (wide_dot(codes[int(pos[int(doc)])], rotated_q / scale, outer) + qm) * float(scaling_factors[int(pos[int(doc)])])) / max(float(composite_norms[int(pos[int(doc)])]), 1e-12) for doc in ids], dtype=np.float64)
        wide_ranked = top_ids(wide_scores, ids, 10)
        rows.append({"query": qi, "arm": "turboquant_plus1_wide_composite", "top10_ids": wide_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(wide_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], wide_ranked).sum() / 10), "side_payload_bytes": 60, "cascade_total_bytes": THQ_BYTES + 60, "query_logical_bytes": D * 2, "query_plane_bytes": 2 * 8 * 64})
    summaries = {arm: {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows if r["arm"] == arm])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rows if r["arm"] == arm], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rows if r["arm"] == arm]))} for arm in ("turboquant_plus1_direct", "turboquant_plus1_float_composite", "turboquant_plus1_wide_composite")}
    sources = {name: getattr(args, name.replace("-", "_")) for name in names[:-1]}
    result = {"schema_version": 3, "family": "thq_qdrant_tq_plus_residual_composite_gate_c_v3", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "codec_metric": "Qdrant TQMode::Plus Bits1 scalar Query1bitWideSimd plus residual composite cosine correction", "upstream_revision": UPSTREAM_REVISION, "upstream_source": UPSTREAM_SOURCE, "bits": 1, "query_path": "Query1bitWideSimd scalar reference", "query_memory_contract": {"logical_int16_bytes": D * 2, "qdrant_query_plane_bytes": 2 * 8 * 64, "plane_length_bytes": 64}, "query_count": QUERY_COUNT, "selected_unique_documents": int(len(selected_unique)), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "artifact_path": str(args.artifact), "artifact_sha256": sha256(args.artifact), "payload_contract": {"base_side_payload_bytes": 56, "composite_side_payload_bytes": 60, "fields": ["48-bit sign code", "float32 scaling_factor = residual_l2 / quantized_centroid_norm", "float32 ec_correction", "float32 final composite norm diagnostic"], "global_metadata_bytes": int(shift.nbytes + scale.nbytes)}, "fit_contract": fit_contract, "summaries": summaries, "rows": rows, "limitations": ["Python scalar model of Qdrant QuerySimd<8,2>; no native SIMD", "candidate-local THQ top128 replay", "composite norm is persisted as a diagnostic payload field", "wire-format bytes are represented by a persisted NumPy audit artifact"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
