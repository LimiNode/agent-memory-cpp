#!/usr/bin/env python3
"""Independent, fail-closed audit for the matched binary R4 gate."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np

from binary_code_references import BBQLikeReference, RabitQReference, _packed_signed_dot

D = 384
THQ_BYTES = 96
NORM_TOL = 1e-3
K_VALUES = (32, 64, 128, 256, 512)
ARMS = ("thq4", "rabitq_rr1", "bbq_block1")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def top_indices(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]


def cosine_scores(query: np.ndarray, docs: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(docs, axis=1) * max(float(np.linalg.norm(query)), np.finfo(np.float32).tiny)
    return (docs @ query) / np.maximum(denom, np.finfo(np.float32).tiny)


def ndcg10(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores) if int(doc) >= 0 and float(score) > 0}
    values = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in grades.values()], dtype=np.float64))[::-1][:10]
    dcg = float(np.sum(values / np.log2(np.arange(2, 2 + len(values)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def thq_scores(query: np.ndarray, levels: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level - 1]
            high = np.inf if level == 3 else thresholds[d, level]
            delta = low - query[d] if query[d] < low else (query[d] - high if query[d] > high else 0.0)
            lut[d, level] = delta * delta
    return -np.sum(lut[np.arange(D)[None, :], levels], axis=1)


def load_f32(path: Path, rows: int | None = None) -> np.ndarray:
    require(path.stat().st_size % (D * 4) == 0, f"not FP32x{D}: {path}")
    actual = path.stat().st_size // (D * 4)
    require(rows is None or actual == rows, f"unexpected row count: {path}")
    return np.asarray(np.memmap(path, mode="r", dtype="<f4", shape=(actual, D)), dtype=np.float32)


def max_norm_error(values: np.ndarray) -> float:
    maximum = 0.0
    for start in range(0, len(values), 65536):
        block = np.asarray(values[start:start + 65536], dtype=np.float32)
        maximum = max(maximum, float(np.max(np.abs(np.linalg.norm(block, axis=1) - 1.0))))
    return maximum


def load_matrix(path: Path, dtype: str, rows: int, cols: int) -> np.ndarray:
    require(path.stat().st_size == rows * cols * np.dtype(dtype).itemsize, f"unexpected matrix size: {path}")
    return np.asarray(np.memmap(path, mode="r", dtype=dtype, shape=(rows, cols)))


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8"))
    rows = metadata.get("rows")
    require(isinstance(rows, list) and len(rows) == 152, "candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    require(flat.stat().st_size == total * 148, "candidate flat size mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    require(receipt_data.get("execution_status") == "EXECUTED", "candidate receipt is not EXECUTED")
    require(receipt_data.get("raw_sha256") == sha256(raw), "candidate raw SHA mismatch")
    require(receipt_data.get("flat_file", {}).get("sha256") == sha256(flat), "candidate flat SHA mismatch")
    return ids, offsets


def fit_models(train: np.ndarray, docs: np.ndarray):
    rabitq = RabitQReference.fit(train, bits=D, seed=20260920, metric="ip")
    bbq = BBQLikeReference.fit(train, bits=D, blocks=8, seed=20260920, metric="ip", scale_storage="fp16")
    transformed = (docs - rabitq.mean) @ rabitq.rotation
    abs_sum = np.sum(np.abs(transformed), axis=1, dtype=np.float32)
    gains = np.divide(np.sum(transformed * transformed, axis=1, dtype=np.float32), abs_sum,
                      out=np.zeros_like(abs_sum), where=abs_sum > 0.0)
    rabitq = dataclasses.replace(rabitq, codes=np.packbits(transformed >= 0, axis=1, bitorder="little"),
                                 gains=gains, source_norm_sq=np.sum(docs * docs, axis=1, dtype=np.float32))
    transformed_b = (docs - bbq.mean) @ bbq.rotation
    blocks = transformed_b.reshape(len(docs), 8, D // 8)
    abs_block = np.sum(np.abs(blocks), axis=2, dtype=np.float32)
    norm_block = np.sum(blocks * blocks, axis=2, dtype=np.float32)
    scales = np.divide(norm_block, abs_block, out=np.zeros_like(norm_block), where=abs_block > 0.0).astype(np.float16)
    bbq = dataclasses.replace(bbq, codes=np.packbits(blocks >= 0, axis=2, bitorder="little"),
                              block_scales=scales, source_norm_sq=np.sum(docs * docs, axis=1, dtype=np.float32))
    return rabitq, bbq


def self_test() -> None:
    require(top_indices(np.array([2.0, 2.0]), np.array([4, 3]), 1).tolist() == [1], "document-ID tie ordering")
    require(np.isclose(ndcg10(np.array([1]), np.array([1]), np.array([1.0])), 1.0), "nDCG calculation")
    print(json.dumps({"status": "PASS", "checks": ["independent top-id ordering", "nDCG calculation"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--source", action="append", nargs=2, metavar=("NAME", "PATH"))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.result or not args.source:
        parser.error("--result and --source NAME PATH are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 2 and result.get("family") == "thq_binary_r4_matched_gate_v2", "unsupported result family")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True, "result is not source-replay bound")
    require(result.get("metric") == "cosine" and result.get("final_reranker") == "same FP32 cosine oracle over K filtered documents", "reranker contract differs")
    require(result.get("timing_semantics") == "python_reference_numpy_not_native_serving_latency", "timing semantics are not explicit")
    require(result.get("payload_bytes") == {"thq4": 96, "rabitq_rr1": 52, "bbq_block1": 64}, "payload manifest differs")
    runner = args.runner or Path(__file__).with_name("run-thq-binary-r4-matched-gate.py")
    require(result.get("runner_sha256") == sha256(runner), "runner SHA mismatch")
    sources = {name: Path(path) for name, path in args.source}
    expected_hashes = result.get("source_hashes", {})
    require(set(expected_hashes) == set(sources), "source list differs from manifest")
    for name, path in sources.items():
        require(path.is_file() and sha256(path) == expected_hashes[name], f"source SHA mismatch: {name}")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 152 * 3 * 5, "unexpected row cardinality")
    row_map = {}
    for row in rows:
        key = (str(row.get("arm")), int(row.get("K", -1)), int(row.get("query", -1)))
        require(key[0] in ARMS and key[1] in K_VALUES and 0 <= key[2] < 152, f"invalid row key: {key}")
        require(key not in row_map, f"duplicate row: {key}")
        require(len(row.get("selected_ids", [])) == key[1] and len(row.get("final_ids", [])) == 10, f"ID cardinality differs: {key}")
        require(row.get("downstream_documents") == key[1], f"downstream count differs: {key}")
        payload = {"thq4": 96, "rabitq_rr1": 52, "bbq_block1": 64}[key[0]]
        model = {"thq4": D * 3 * 4, "rabitq_rr1": D * D * 4 + D * 4,
                 "bbq_block1": D * D * 4 + D * 4}[key[0]]
        require(row["candidate_id_bytes"] == row["candidate_count"] * 4, f"candidate-ID bytes differ: {key}")
        require(row["filter_payload_bytes"] == row["candidate_count"] * payload, f"payload bytes differ: {key}")
        require(row["global_model_bytes"] == model, f"model bytes differ: {key}")
        require(row["rerank_document_bytes"] == key[1] * D * 4, f"rerank bytes differ: {key}")
        require(row["logical_bytes_addressed"] == row["global_model_bytes"] + row["candidate_id_bytes"] + row["filter_payload_bytes"] + row["rerank_document_bytes"], f"logical byte accounting differs: {key}")
        row_map[key] = row
    required = {"documents", "train_vectors", "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt", "queries", "qrel_ids", "qrel_scores", "teacher_ids"}
    require(set(sources) == required, "full source replay manifest is required")
    documents = load_f32(sources["documents"], 1_000_000)
    train = load_f32(sources["train_vectors"])
    queries = load_f32(sources["queries"], 152)
    norm_errors = {"documents": max_norm_error(documents), "queries": max_norm_error(queries),
                   "train_vectors": max_norm_error(train)}
    require(all(value <= NORM_TOL for value in norm_errors.values()), f"IP/cosine unit-norm contract violated: {norm_errors}")
    require(result.get("norm_contract", {}).get("required") == "unit_l2_norm", "norm contract missing")
    require(float(result["norm_contract"].get("tolerance", -1.0)) == NORM_TOL, "norm tolerance differs")
    recorded_norms = result["norm_contract"].get("max_abs_error", {})
    require(set(recorded_norms) == set(norm_errors), "norm diagnostic fields differ")
    for name, value in norm_errors.items():
        require(np.isclose(float(recorded_norms[name]), value, rtol=0.0, atol=1e-7), f"norm diagnostic differs: {name}")
    qrel_ids = load_matrix(sources["qrel_ids"], "<i8", 152, 20)
    qrel_scores = load_matrix(sources["qrel_scores"], "<f4", 152, 20)
    teacher_ids = load_matrix(sources["teacher_ids"], "<i8", 152, 10)
    require(sources["thq4_codes"].stat().st_size == 1_000_000 * THQ_BYTES, "THQ4 source size differs")
    thq_codes = np.memmap(sources["thq4_codes"], mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(sources["thq4_thresholds"], dtype="<f4").reshape(D, 3)
    ids, offsets = load_candidates(sources["candidate_flat"], sources["candidate_raw"], sources["candidate_receipt"])
    unique_ids = np.unique(ids)
    candidate_docs = np.asarray(documents[unique_ids], dtype=np.float32)
    rabitq, bbq = fit_models(train, candidate_docs)
    positions_by_id = {int(doc): pos for pos, doc in enumerate(unique_ids)}
    for qi, query in enumerate(queries):
        row_ids = ids[offsets[qi]:offsets[qi + 1]]
        positions = np.asarray([positions_by_id[int(doc)] for doc in row_ids], dtype=np.int64)
        docs = candidate_docs[positions]
        levels = unpack_thq(np.asarray(thq_codes[row_ids]))
        for arm in ARMS:
            if arm == "thq4":
                scores = thq_scores(query, levels, thresholds)
            elif arm == "rabitq_rr1":
                encoded = (query - rabitq.mean) @ rabitq.rotation
                scores = _packed_signed_dot(encoded, rabitq.codes[positions], D) * rabitq.gains[positions] + float(rabitq.mean @ query)
            else:
                encoded = (query - bbq.mean) @ bbq.rotation
                dots = bbq._block_dots(encoded, bbq.codes[positions])
                scores = np.sum(dots * bbq.block_scales[positions].astype(np.float32), axis=1) + float(bbq.mean @ query)
            for k in K_VALUES:
                filtered = row_ids[top_indices(scores, row_ids, k)]
                reranked = filtered[top_indices(cosine_scores(query, documents[filtered]), filtered, 10)]
                row = row_map[(arm, k, qi)]
                require(np.array_equal(filtered, np.asarray(row["selected_ids"], dtype=np.int64)), f"filter replay mismatch: {(arm, k, qi)}")
                require(np.array_equal(reranked, np.asarray(row["final_ids"], dtype=np.int64)), f"rerank replay mismatch: {(arm, k, qi)}")
                require(np.isclose(ndcg10(reranked, qrel_ids[qi], qrel_scores[qi]), float(row["qrels_ndcg10"]), atol=1e-6), f"nDCG replay mismatch: {(arm, k, qi)}")
                overlap = float(np.isin(teacher_ids[qi], reranked).sum() / 10.0)
                require(np.isclose(overlap, float(row["teacher_overlap"]), atol=1e-6), f"teacher replay mismatch: {(arm, k, qi)}")
    print(json.dumps({"status": "PASS", "checks": ["source SHA replay", "runner SHA replay", "independent filter score replay", "top-K replay", "FP32 rerank replay", "nDCG replay", "teacher-overlap replay", "split byte accounting"]}, indent=2))


if __name__ == "__main__":
    main()
