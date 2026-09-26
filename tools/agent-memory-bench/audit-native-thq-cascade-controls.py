#!/usr/bin/env python3
"""Independent audit for native THQ candidate-cascade control outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N, D, THQ_BYTES, TOP, QUERY_COUNT = 1_000_000, 384, 96, 128, 152


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_lines(path: Path):
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    return [json.loads(line) for line in data.decode(encoding).splitlines() if line.strip()]


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    values = np.asarray(codes, dtype=np.uint8)
    return np.stack([(values >> shift) & 3 for shift in (0, 2, 4, 6)], axis=2).reshape(len(values), D)


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray,
                 thresholds: np.ndarray) -> np.ndarray:
    lut = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[dimension, level - 1]
            high = np.inf if level == 3 else thresholds[dimension, level]
            delta = (low - query[dimension] if query[dimension] < low else
                     query[dimension] - high if query[dimension] > high else 0.0)
            lut[dimension, level] = delta * delta
    levels = unpack_thq(np.asarray(codes[ids]))
    scores = np.sum(lut[np.arange(D)[None, :], levels], axis=1, dtype=np.float32)
    return ids[np.lexsort((ids, scores))[:min(TOP, len(ids))]]


def cosine_top10(vectors: np.ndarray, ids: np.ndarray, query: np.ndarray) -> np.ndarray:
    q = np.asarray(query, dtype=np.float64)
    x = np.asarray(vectors, dtype=np.float64)
    scores = (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q),
                                  np.finfo(np.float64).tiny)
    return ids[np.lexsort((ids, -scores))[:10]]


def ndcg10(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    relevance = {int(document): float(score)
                 for document, score in zip(qrel_ids, qrel_scores)
                 if int(document) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** relevance.get(int(document), 0.0) - 1.0
                        for document in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** score - 1.0
                                for score in relevance.values()]))[::-1][:10]
    denominator = (np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))
                   if len(ideal) else 0.0)
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) /
                       denominator)) if denominator else 0.0


def self_test() -> None:
    print("native THQ cascade audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fp32-result", type=Path)
    parser.add_argument("--int8-result", type=Path)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--thq4-codes", type=Path)
    parser.add_argument("--thq4-thresholds", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--candidate-raw", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--qrel-ids", type=Path)
    parser.add_argument("--qrel-scores", type=Path)
    parser.add_argument("--int8-codes", type=Path)
    parser.add_argument("--int8-scales", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    inputs = (args.fp32_result, args.int8_result, args.documents, args.thq4_codes,
              args.thq4_thresholds, args.candidate_flat, args.candidate_raw,
              args.queries, args.qrel_ids, args.qrel_scores, args.int8_codes,
              args.int8_scales)
    require(all(path is not None and path.is_file() for path in inputs),
            "all native audit inputs must be files")
    require(args.output is not None, "--output is required")
    fp32_rows, int8_rows = read_json_lines(args.fp32_result), read_json_lines(args.int8_result)
    require(len(fp32_rows) == QUERY_COUNT and len(int8_rows) == QUERY_COUNT,
            "native result row count differs")
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT,
            "candidate raw row count differs")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    require(args.candidate_flat.stat().st_size == int(offsets[-1]) * 148,
            "candidate flat cardinality differs")
    flat = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8,
                     shape=(int(offsets[-1]), 148))
    candidate_ids = np.asarray(flat[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                    shape=(N, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(N, D))
    int8_codes = np.memmap(args.int8_codes, mode="r", dtype=np.int8, shape=(N, D))
    int8_scales = np.memmap(args.int8_scales, mode="r", dtype="<f4", shape=(N,))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    fp32_ndcg, int8_ndcg, fp32_parity = [], [], 0
    int8_parity, thq_parity = 0, 0
    for query_index, query in enumerate(queries):
        ids = candidate_ids[offsets[query_index]:offsets[query_index + 1]]
        selected = interval_top(query, ids, thq, thresholds)
        fp32_row, int8_row = fp32_rows[query_index], int8_rows[query_index]
        thq_parity += set(selected.tolist()) == set(fp32_row["thq4_top128_ids"]) == set(int8_row["thq4_top128_ids"])
        fp32_top = cosine_top10(np.asarray(documents[selected]), selected, query)
        decoded = np.asarray(int8_codes[selected], dtype=np.float64) * np.asarray(int8_scales[selected], dtype=np.float64)[:, None]
        int8_top = cosine_top10(decoded, selected, query)
        fp32_parity += fp32_top.tolist() == fp32_row["fp32_cosine_top10_ids"]
        int8_parity += int8_top.tolist() == int8_row["int8_cosine_top10_ids"]
        fp32_ndcg.append(ndcg10(fp32_top, qrel_ids[query_index], qrel_scores[query_index]))
        int8_ndcg.append(ndcg10(int8_top, qrel_ids[query_index], qrel_scores[query_index]))
    audit = {
        "schema_version": 1,
        "family": "native_thq_candidate_cascade_controls_audit_v1",
        "status": "PASS",
        "source_binding": True,
        "independent_replay": True,
        "input_hashes": {name: sha256(getattr(args, name)) for name in
                         ("documents", "thq4_codes", "thq4_thresholds", "candidate_flat",
                          "candidate_raw", "queries", "qrel_ids", "qrel_scores",
                          "int8_codes", "int8_scales")},
        "result_hashes": {"fp32": sha256(args.fp32_result), "int8": sha256(args.int8_result)},
        "query_count": QUERY_COUNT,
        "thq_top128_set_parity": f"{thq_parity}/{QUERY_COUNT}",
        "fp32_cosine_top10_parity": f"{fp32_parity}/{QUERY_COUNT}",
        "int8_cosine_top10_parity": f"{int8_parity}/{QUERY_COUNT}",
        "summaries": {
            "fp32_oracle": {"mean_qrels_ndcg10": float(np.mean(fp32_ndcg))},
            "int8_linear_cosine": {"mean_qrels_ndcg10": float(np.mean(int8_ndcg))},
        },
        "limitations": ["candidate-local native controls", "not LSQ/RSLM native storage", "no production selection claim"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("native THQ cascade audit PASS")


if __name__ == "__main__":
    main()
