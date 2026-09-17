#!/usr/bin/env python3
"""Run the four matched codec arms on one frozen R4 candidate stream.

This is the Gate 1 candidate-local benchmark.  The full-corpus native C++
control is deliberately a separate experiment; this runner never scans
documents that are absent from the supplied candidate stream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384
THQ_BYTES = 96
K = 128


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def ndcg(ids: np.ndarray, qids: np.ndarray, scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qids, scores)
              if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int, ascending: bool = True) -> np.ndarray:
    order = np.lexsort((ids, scores if ascending else -scores))[:limit]
    return ids[order]


def page_count(ids: np.ndarray, record_bytes: int) -> int:
    pages: set[int] = set()
    for doc in ids.astype(np.int64, copy=False):
        begin = int(doc) * record_bytes
        end = begin + record_bytes - 1
        pages.update(range(begin // 4096, end // 4096 + 1))
    return len(pages)


def score_int8(codes: np.ndarray, scales: np.ndarray, query: np.ndarray,
               nonlinear: bool) -> np.ndarray:
    if not nonlinear:
        return (codes.astype(np.float32) @ query) * scales
    table = np.power(np.arange(128, dtype=np.float32), 1.6)
    gains = np.power(scales.astype(np.float32), 1.6)
    decoded = np.sign(codes).astype(np.float32) * table[np.abs(codes)] * gains[:, None]
    return decoded @ query


def score_thq(codes: np.ndarray, thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            lo = -np.inf if level == 0 else thresholds[d, level - 1]
            hi = np.inf if level == 3 else thresholds[d, level]
            delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
            lut[d, level] = delta * delta
    levels = np.stack([(codes >> shift) & 3 for shift in (0, 2, 4, 6)], axis=2)
    levels = levels.reshape(codes.shape[0], D)
    return np.sum(lut[np.arange(D)[None, :], levels], axis=1, dtype=np.float32)


def score_thq_byte_lut(codes: np.ndarray, thresholds: np.ndarray,
                       query: np.ndarray) -> np.ndarray:
    """Reference the native 96x256 byte-LUT accumulation order exactly."""
    coordinate = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            lo = -np.inf if level == 0 else thresholds[d, level - 1]
            hi = np.inf if level == 3 else thresholds[d, level]
            delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
            coordinate[d, level] = delta * delta
    byte_lut = np.empty((THQ_BYTES, 256), dtype=np.float32)
    for byte in range(THQ_BYTES):
        for packed in range(256):
            byte_lut[byte, packed] = sum(
                coordinate[byte * 4 + lane, (packed >> (lane * 2)) & 3]
                for lane in range(4)
            )
    return np.sum(byte_lut[np.arange(THQ_BYTES)[None, :], codes], axis=1,
                  dtype=np.float32)


def self_test() -> None:
    ids = np.asarray([5, 3, 4], dtype=np.int64)
    scores = np.asarray([1.0, 1.0, 2.0], dtype=np.float32)
    if top_ids(scores, ids, 3, ascending=False).tolist() != [4, 3, 5]:
        raise RuntimeError("deterministic top-k tie policy differs")
    if page_count(np.asarray([10], dtype=np.int64), 384) != 2:
        raise RuntimeError("record-span page accounting differs")
    zeros = np.zeros((1, THQ_BYTES), dtype=np.uint8)
    thresholds = np.zeros((D, 3), dtype=np.float32)
    query = np.zeros(D, dtype=np.float32)
    if score_thq(zeros, thresholds, query).tolist() != [0.0]:
        raise RuntimeError("packed THQ reference score differs")
    packed = np.arange(THQ_BYTES, dtype=np.uint8)[None, :]
    if not np.array_equal(score_thq(packed, thresholds, query),
                          score_thq_byte_lut(packed, thresholds, query)):
        raise RuntimeError("coordinate and byte-LUT THQ references differ")
    print("run-native-full-corpus-candidate-gate self-test PASS")


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    native = json.loads(args.native_receipt.read_text(encoding="utf-8"))
    candidate_receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    candidate_raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    rows = candidate_raw["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    expected_records = int(offsets[-1])
    require(len(rows) == 152, "candidate query count differs")
    require(np.all((counts >= 5000) & (counts <= 5099)),
            "candidate overshoot is outside the frozen 5000..5099 contract")
    require(args.candidate_flat.stat().st_size == expected_records * 148,
            "candidate flat size differs")
    candidate = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8,
                          shape=(expected_records, 148))
    candidate_ids = np.frombuffer(np.asarray(candidate[:, :4]).tobytes(), dtype="<i4")
    candidate_ids = candidate_ids.reshape(-1)
    require(native.get("manifest_sha256") == sha(args.manifest), "native manifest binding differs")
    require(candidate_receipt.get("raw_sha256") == sha(args.candidate_raw),
            "candidate raw binding differs")
    require(candidate_receipt.get("flat_file", {}).get("sha256") == sha(args.candidate_flat),
            "candidate flat binding differs")
    require(candidate_receipt.get("flat_file", {}).get("bytes") == args.candidate_flat.stat().st_size,
            "candidate flat byte count differs")
    require(np.all((candidate_ids >= 0) & (candidate_ids < N)), "candidate id out of range")
    for qi in range(len(rows)):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        require(len(np.unique(ids)) == len(ids), f"candidate IDs are duplicated for query {qi}")

    root = args.native_receipt.parent
    files = native["files"]
    for role, entry in files.items():
        payload = resolve(root, entry["path"])
        require(payload.is_file() and payload.stat().st_size == int(entry["bytes"]),
                f"native payload shape differs: {role}")
        require(sha(payload) == entry["sha256"], f"native payload binding differs: {role}")
    thq = np.memmap(root / files["thq4_ordinal"]["path"], mode="r", dtype=np.uint8,
                    shape=(N, THQ_BYTES))
    thresholds = np.fromfile(root / files["thq4_thresholds"]["path"], dtype="<f4").reshape(D, 3)
    linear = np.memmap(root / files["int8_linear_codes"]["path"], mode="r", dtype=np.int8,
                       shape=(N, D))
    linear_scales = np.memmap(root / files["int8_linear_scales"]["path"], mode="r", dtype="<f4", shape=(N,))
    power = np.memmap(root / files["int8_power0625_codes"]["path"], mode="r", dtype=np.int8,
                      shape=(N, D))
    power_scales = np.memmap(root / files["int8_power0625_scales"]["path"], mode="r", dtype="<f4", shape=(N,))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(len(rows), D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(len(rows), 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(len(rows), 20))
    teachers = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(len(rows), 10))

    measurements = []
    for qi, row in enumerate(rows):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]].astype(np.int64, copy=False)
        query = np.asarray(queries[qi], dtype=np.float32)
        for name in ("direct_linear", "thq4_linear", "direct_power0625", "thq4_power0625"):
            start = time.perf_counter()
            if name == "direct_linear":
                exact_scores = score_int8(np.asarray(linear[ids]), np.asarray(linear_scales[ids]), query, False)
                selected = top_ids(exact_scores, ids, 10, ascending=False)
                logical_bytes = 388 * len(ids)
                pages = page_count(ids, 384) + page_count(ids, 4)
            elif name == "direct_power0625":
                exact_scores = score_int8(np.asarray(power[ids]), np.asarray(power_scales[ids]), query, True)
                selected = top_ids(exact_scores, ids, 10, ascending=False)
                logical_bytes = 388 * len(ids)
                pages = page_count(ids, 384) + page_count(ids, 4)
            elif name == "thq4_linear":
                thq_scores = score_thq_byte_lut(np.asarray(thq[ids]), thresholds, query)
                thq_top = top_ids(thq_scores, ids, min(K, len(ids)), ascending=True)
                rerank_scores = score_int8(np.asarray(linear[thq_top]), np.asarray(linear_scales[thq_top]), query, False)
                selected = top_ids(rerank_scores, thq_top, 10, ascending=False)
                logical_bytes = THQ_BYTES * len(ids) + 388 * len(thq_top)
                pages = page_count(ids, THQ_BYTES) + page_count(thq_top, 384) + page_count(thq_top, 4)
            else:
                thq_scores = score_thq_byte_lut(np.asarray(thq[ids]), thresholds, query)
                thq_top = top_ids(thq_scores, ids, min(K, len(ids)), ascending=True)
                rerank_scores = score_int8(np.asarray(power[thq_top]), np.asarray(power_scales[thq_top]), query, True)
                selected = top_ids(rerank_scores, thq_top, 10, ascending=False)
                logical_bytes = THQ_BYTES * len(ids) + 388 * len(thq_top)
                pages = page_count(ids, THQ_BYTES) + page_count(thq_top, 384) + page_count(thq_top, 4)
            elapsed = (time.perf_counter() - start) * 1000.0
            measurements.append({"query": qi, "arm": name, "top10_ids": selected.astype(int).tolist(),
                                 "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                                 "teacher_overlap": float(np.isin(teachers[qi], selected).sum() / 10.0),
                                 "candidate_count": int(len(ids)), "logical_payload_bytes": int(logical_bytes),
                                 "unique_4k_pages": int(pages),
                                 "latency_ms": elapsed,
                                 "timing_scope": "python_reference_candidate_scoring_and_selection"})

    result = {
        "schema_version": 1,
        "family": "native_full_corpus_candidate_gate_v2",
        "status": "EXECUTED",
        "candidate_stream_sha256": sha(args.candidate_flat),
        "candidate_raw_sha256": sha(args.candidate_raw),
        "candidate_receipt_sha256": sha(args.candidate_receipt),
        "native_receipt_sha256": sha(args.native_receipt),
        "manifest_sha256": sha(args.manifest),
        "quality_inputs": {
            "queries_sha256": sha(args.queries),
            "qrel_ids_sha256": sha(args.qrel_ids),
            "qrel_scores_sha256": sha(args.qrel_scores),
            "teacher_ids_sha256": sha(args.teacher_ids),
            "query_count": len(rows),
        },
        "timing_scope": "python_reference_candidate_scoring_and_selection; not a native-kernel or OS-page latency claim",
        "rows": measurements,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
