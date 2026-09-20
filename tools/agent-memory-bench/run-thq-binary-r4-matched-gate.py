#!/usr/bin/env python3
"""Matched THQ4/RaBitQ/BBQ filter gate on the frozen R4 candidate shell.

The three codecs are alternative filters.  Each emits K documents and the
same FP32 cosine oracle reranks those K documents.  This runner is deliberately
source-bound: it refuses compact result JSON, decoded INT8, or legacy 144-byte
payloads as substitutes for the FP32 document/query/qrels sources.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np

from binary_code_references import BBQLikeReference, RabitQReference, _packed_signed_dot

D = 384
DOC_BYTES = D * 4
THQ_BYTES = 96
K_VALUES = (32, 64, 128, 256, 512)
SEED = 20260920
NORM_TOL = 1e-3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), fraction * 100.0))


def top_indices(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), int(scores.size))
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    # IDs are the deterministic secondary key for every tie, including equal
    # binary scores and equal FP32 cosine values.
    return np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    values = np.asarray(codes, dtype=np.uint8)
    if values.ndim != 2 or values.shape[1] != THQ_BYTES:
        raise ValueError("THQ codes must have shape (rows, 96)")
    unpacked = np.unpackbits(values, axis=1, bitorder="little")
    return (unpacked[:, 0::2] + 2 * unpacked[:, 1::2]).astype(np.uint8)


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8"))
    rows = metadata.get("rows")
    if not isinstance(rows, list) or len(rows) != 152:
        raise RuntimeError("candidate raw must contain exactly 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat size does not match raw row counts")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate document ID is outside the 1M corpus")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_data.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not EXECUTED")
    if receipt_data.get("raw_sha256") != sha256(raw):
        raise RuntimeError("candidate raw/receipt SHA mismatch")
    flat_meta = receipt_data.get("flat_file", {})
    if flat_meta.get("sha256") != sha256(flat):
        raise RuntimeError("candidate flat/receipt SHA mismatch")
    return ids, offsets


def load_f32(path: Path, rows: int | None = None) -> np.ndarray:
    if path.stat().st_size % (4 * D):
        raise RuntimeError(f"{path} is not an exact float32x384 matrix")
    actual = path.stat().st_size // (4 * D)
    if rows is not None and actual != rows:
        raise RuntimeError(f"{path} has {actual} rows, expected {rows}")
    return np.asarray(np.memmap(path, mode="r", dtype="<f4", shape=(actual, D)), dtype=np.float32)


def max_norm_error(values: np.ndarray) -> float:
    maximum = 0.0
    for start in range(0, len(values), 65536):
        block = np.asarray(values[start:start + 65536], dtype=np.float32)
        maximum = max(maximum, float(np.max(np.abs(np.linalg.norm(block, axis=1) - 1.0))))
    return maximum


def load_int_matrix(path: Path, dtype: str, cols: int, rows: int) -> np.ndarray:
    item = np.dtype(dtype).itemsize
    if path.stat().st_size != rows * cols * item:
        raise RuntimeError(f"{path} has unexpected matrix size")
    return np.asarray(np.memmap(path, mode="r", dtype=dtype, shape=(rows, cols)))


def ndcg10(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    ranked = [2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]]
    ideal = sorted((2.0 ** value - 1.0 for value in grades.values()), reverse=True)[:10]
    discounts = np.log2(np.arange(2, 2 + len(ranked), dtype=np.float64))
    ideal_discounts = np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))
    denominator = float(np.sum(np.asarray(ideal) / ideal_discounts)) if ideal else 0.0
    return float(np.sum(np.asarray(ranked) / discounts) / denominator) if denominator else 0.0


def cosine_scores(query: np.ndarray, docs: np.ndarray) -> np.ndarray:
    q = np.asarray(query, dtype=np.float32)
    x = np.asarray(docs, dtype=np.float32)
    dots = x @ q
    denom = np.linalg.norm(x, axis=1) * max(float(np.linalg.norm(q)), np.finfo(np.float32).tiny)
    return dots / np.maximum(denom, np.finfo(np.float32).tiny)


def thq_lut(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            if query[coordinate] < low:
                delta = low - query[coordinate]
            elif query[coordinate] > high:
                delta = query[coordinate] - high
            else:
                delta = 0.0
            lut[coordinate, level] = np.float32(delta * delta)
    return lut


def score_from_encoded(arm: str, query: np.ndarray, encoded: np.ndarray, positions: np.ndarray,
                       rabitq: RabitQReference, bbq: BBQLikeReference, levels: np.ndarray,
                       _thresholds: np.ndarray) -> np.ndarray:
    if arm == "thq4":
        return -np.sum(encoded[np.arange(D)[None, :], levels], axis=1)
    if arm == "rabitq_rr1":
        dot = _packed_signed_dot(encoded, rabitq.codes[positions], D) * rabitq.gains[positions]
        return dot + float(rabitq.mean @ query)
    block_dot = bbq._block_dots(encoded, bbq.codes[positions])
    return np.sum(block_dot * bbq.block_scales[positions].astype(np.float32), axis=1) + float(bbq.mean @ query)


def candidate_codec_models(train: np.ndarray, docs: np.ndarray, ids: np.ndarray):
    rabitq = RabitQReference.fit(train, bits=D, seed=SEED, metric="ip")
    bbq = BBQLikeReference.fit(train, bits=D, blocks=8, seed=SEED, metric="ip", scale_storage="fp16")
    transformed = (docs - rabitq.mean) @ rabitq.rotation
    abs_sum = np.sum(np.abs(transformed), axis=1, dtype=np.float32)
    gains = np.divide(np.sum(transformed * transformed, axis=1, dtype=np.float32), abs_sum,
                      out=np.zeros_like(abs_sum), where=abs_sum > 0.0)
    rabitq = dataclasses.replace(rabitq, codes=np.packbits(transformed >= 0, axis=1, bitorder="little"),
                                 gains=gains, source_norm_sq=np.sum(docs * docs, axis=1, dtype=np.float32))
    transformed_b = (docs - bbq.mean) @ bbq.rotation
    width = D // 8
    blocks = transformed_b.reshape(len(docs), 8, width)
    abs_block = np.sum(np.abs(blocks), axis=2, dtype=np.float32)
    norm_block = np.sum(blocks * blocks, axis=2, dtype=np.float32)
    scales = np.divide(norm_block, abs_block, out=np.zeros_like(norm_block), where=abs_block > 0.0).astype(np.float16)
    bbq = dataclasses.replace(bbq, codes=np.packbits(blocks >= 0, axis=2, bitorder="little"),
                              block_scales=scales, source_norm_sq=np.sum(docs * docs, axis=1, dtype=np.float32))
    return rabitq, bbq


def self_test() -> None:
    rng = np.random.default_rng(20260920)
    docs = rng.normal(size=(97, D)).astype(np.float32)
    queries = rng.normal(size=(3, D)).astype(np.float32)
    train = rng.normal(size=(128, D)).astype(np.float32)
    rabitq, bbq = candidate_codec_models(train, docs, np.arange(len(docs)))
    for query in queries:
        positions = np.arange(len(docs), dtype=np.int64)
        encoded_r = (query - rabitq.mean) @ rabitq.rotation
        encoded_b = (query - bbq.mean) @ bbq.rotation
        a = rabitq.scores(query)
        b = bbq.scores(query)
        a_control = score_from_encoded("rabitq_rr1", query, encoded_r, positions,
                                       rabitq, bbq, np.zeros((len(docs), D), dtype=np.uint8),
                                       np.zeros((D, 3), dtype=np.float32))
        b_control = score_from_encoded("bbq_block1", query, encoded_b, positions,
                                       rabitq, bbq, np.zeros((len(docs), D), dtype=np.uint8),
                                       np.zeros((D, 3), dtype=np.float32))
        assert np.isfinite(a).all() and np.isfinite(b).all()
        assert np.allclose(a, a_control, rtol=1e-6, atol=1e-6)
        assert np.allclose(b, b_control, rtol=1e-6, atol=1e-6)
    levels = np.zeros((2, THQ_BYTES), dtype=np.uint8)
    levels[0, 0] = 0b11100100
    assert np.array_equal(unpack_thq(levels)[0, :4], np.array([0, 1, 2, 3], dtype=np.uint8))
    print("matched binary R4 runner self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds", "candidate-flat",
                 "candidate-raw", "candidate-receipt", "queries", "qrel-ids", "qrel-scores",
                 "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.documents, args.train_vectors, args.thq4_codes, args.thq4_thresholds, args.candidate_flat,
                args.candidate_raw, args.candidate_receipt, args.queries, args.qrel_ids, args.qrel_scores,
                args.teacher_ids, args.output)
    if any(value is None for value in required):
        parser.error("all source paths and --output are required unless --self-test is used")
    if args.warmups < 0 or args.repeats < 3:
        parser.error("--warmups must be non-negative and --repeats must be at least 3")

    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    documents = load_f32(args.documents, rows=1_000_000)
    train = load_f32(args.train_vectors)
    queries = load_f32(args.queries, rows=152)
    norm_errors = {"documents": max_norm_error(documents), "queries": max_norm_error(queries),
                   "train_vectors": max_norm_error(train)}
    if any(value > NORM_TOL for value in norm_errors.values()):
        raise RuntimeError(f"IP/cosine unit-norm contract violated: {norm_errors}")
    qrel_ids = load_int_matrix(args.qrel_ids, "<i8", 20, 152)
    qrel_scores = load_int_matrix(args.qrel_scores, "<f4", 20, 152)
    teacher_ids = load_int_matrix(args.teacher_ids, "<i8", 10, 152)
    code_bytes = args.thq4_codes.stat().st_size
    if code_bytes != 1_000_000 * THQ_BYTES:
        raise RuntimeError("canonical THQ4 code source must contain 1M x 96 bytes")
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4")
    if thresholds.size != D * 3:
        raise RuntimeError("THQ thresholds must contain 384x3 float32 values")
    thresholds = thresholds.reshape(D, 3)
    unique_ids = np.unique(candidate_ids)
    candidate_docs = np.asarray(documents[unique_ids], dtype=np.float32)
    rabitq, bbq = candidate_codec_models(train, candidate_docs, unique_ids)
    id_to_pos = {int(doc): pos for pos, doc in enumerate(unique_ids)}
    rows: list[dict[str, object]] = []
    arm_payload = {"thq4": THQ_BYTES, "rabitq_rr1": 48 + 4, "bbq_block1": 48 + 8 * 2}
    model_bytes = {"thq4": D * 3 * 4, "rabitq_rr1": D * D * 4 + D * 4,
                   "bbq_block1": D * D * 4 + D * 4}
    for query_index, query in enumerate(queries):
        ids = candidate_ids[offsets[query_index]:offsets[query_index + 1]]
        positions = np.asarray([id_to_pos[int(doc)] for doc in ids], dtype=np.int64)
        docs = candidate_docs[positions]
        exact = cosine_scores(query, docs)
        exact_top10 = ids[top_indices(exact, ids, 10)]
        levels = unpack_thq(np.asarray(thq_codes[ids]))
        for arm in arm_payload:
            for k in K_VALUES:
                filter_times, total_times, encode_times = [], [], []
                selected_ids = None
                final_ids = None
                for repeat in range(args.warmups + args.repeats):
                    start_total = time.perf_counter_ns()
                    if arm == "thq4":
                        encoded_query = thq_lut(query, thresholds)
                    elif arm == "rabitq_rr1":
                        encoded_query = (query - rabitq.mean) @ rabitq.rotation
                    else:
                        encoded_query = (query - bbq.mean) @ bbq.rotation
                    encode_elapsed = (time.perf_counter_ns() - start_total) / 1e6
                    start_filter = time.perf_counter_ns()
                    scores = score_from_encoded(arm, query, encoded_query, positions, rabitq, bbq,
                                                levels, thresholds)
                    if not np.isfinite(scores).all():
                        raise RuntimeError(f"non-finite {arm} scores for query {query_index}")
                    order = top_indices(scores, ids, k)
                    filtered = ids[order]
                    filter_elapsed = (time.perf_counter_ns() - start_filter) / 1e6
                    reranked = filtered[top_indices(cosine_scores(query, documents[filtered]), filtered, 10)]
                    total_elapsed = (time.perf_counter_ns() - start_total) / 1e6
                    if repeat >= args.warmups:
                        encode_times.append(encode_elapsed)
                        filter_times.append(filter_elapsed)
                        total_times.append(total_elapsed)
                    selected_ids, final_ids = filtered, reranked
                assert selected_ids is not None and final_ids is not None
                rows.append({
                    "query": query_index, "arm": arm, "K": k,
                    "candidate_count": int(len(ids)),
                    "filter_top10_overlap": float(np.isin(exact_top10, selected_ids).sum() / 10.0),
                    "final_top10_overlap": float(np.isin(exact_top10, final_ids).sum() / 10.0),
                    "qrels_ndcg10": ndcg10(final_ids, qrel_ids[query_index], qrel_scores[query_index]),
                    "teacher_overlap": float(np.isin(teacher_ids[query_index], final_ids).sum() / 10.0),
                    "python_reference_filter_only_ms": statistics.median(filter_times),
                    "python_reference_filter_plus_fp32_cosine_rerank_ms": statistics.median(total_times),
                    "python_reference_query_encode_ms": statistics.median(encode_times),
                    "python_reference_filter_p50_ms": percentile(filter_times, .50), "python_reference_filter_p95_ms": percentile(filter_times, .95),
                    "python_reference_filter_p99_ms": percentile(filter_times, .99),
                    "python_reference_cascade_p50_ms": percentile(total_times, .50), "python_reference_cascade_p95_ms": percentile(total_times, .95),
                    "python_reference_cascade_p99_ms": percentile(total_times, .99),
                    "global_model_bytes": int(model_bytes[arm]),
                    "candidate_id_bytes": int(len(ids) * 4),
                    "filter_payload_bytes": int(len(ids) * arm_payload[arm]),
                    "rerank_document_bytes": int(k * DOC_BYTES),
                    "logical_bytes_addressed": int(model_bytes[arm] + len(ids) * 4 + len(ids) * arm_payload[arm] + k * DOC_BYTES),
                    "downstream_documents": int(k),
                    "selected_ids": selected_ids.astype(int).tolist(),
                    "final_ids": final_ids.astype(int).tolist(),
                })
    summaries = {}
    for arm in arm_payload:
        summaries[arm] = {}
        for k in K_VALUES:
            subset = [row for row in rows if row["arm"] == arm and row["K"] == k]
            quality = [float(row["qrels_ndcg10"]) for row in subset]
            summaries[arm][str(k)] = {
                "mean_qrels_ndcg10": float(np.mean(quality)), "p05_per_query_ndcg10": percentile(quality, .05),
                "worst_query_ndcg10": float(np.min(quality)),
                "mean_filter_top10_overlap": float(np.mean([row["filter_top10_overlap"] for row in subset])),
                "mean_final_top10_overlap": float(np.mean([row["final_top10_overlap"] for row in subset])),
                "mean_python_reference_filter_only_ms": float(np.mean([row["python_reference_filter_only_ms"] for row in subset])),
                "mean_python_reference_cascade_ms": float(np.mean([row["python_reference_filter_plus_fp32_cosine_rerank_ms"] for row in subset])),
                "p95_python_reference_cascade_ms": percentile([float(row["python_reference_filter_plus_fp32_cosine_rerank_ms"]) for row in subset], .95),
                "mean_logical_bytes_addressed": float(np.mean([row["logical_bytes_addressed"] for row in subset])),
            }
    result = {
        "schema_version": 2, "family": "thq_binary_r4_matched_gate_v2", "status": "EXECUTED",
        "source_replay": True,
        "runner_sha256": sha256(Path(__file__)),
        "metric": "cosine", "final_reranker": "same FP32 cosine oracle over K filtered documents",
        "timing_semantics": "python_reference_numpy_not_native_serving_latency",
        "norm_contract": {"required": "unit_l2_norm", "tolerance": NORM_TOL, "max_abs_error": norm_errors},
        "candidate_count": int(len(candidate_ids)), "query_count": 152, "candidate_unique_documents": int(len(unique_ids)),
        "K_values": list(K_VALUES), "seed": SEED, "tie_policy": "score descending, document ID ascending",
        "source_hashes": {name: sha256(path) for name, path in {
            "documents": args.documents, "train_vectors": args.train_vectors, "thq4_codes": args.thq4_codes,
            "thq4_thresholds": args.thq4_thresholds, "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
            "candidate_receipt": args.candidate_receipt, "queries": args.queries, "qrel_ids": args.qrel_ids,
            "qrel_scores": args.qrel_scores, "teacher_ids": args.teacher_ids}.items()},
        "protocol": {"thq4": {"representation": "384D ordinal, 4 interval levels", "payload_bytes": 96, "scorer": "interval-squared ADC"},
                     "rabitq_rr1": {"bits": 384, "metric": "ip", "seed": SEED, "gain": "||rotated_centered_document||^2 / L1(rotated_centered_document)"},
                     "bbq_block1": {"bits": 384, "blocks": 8, "scale_storage": "fp16", "metric": "ip", "seed": SEED}},
        "payload_bytes": arm_payload, "summaries": summaries, "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
