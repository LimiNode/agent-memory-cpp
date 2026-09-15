#!/usr/bin/env python3
"""Fail-closed parity check for native byte-LUT THQ ordering."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384
BYTES = 96
K = 128


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def byte_lut_scores(codes: np.ndarray, thresholds: np.ndarray,
                    query: np.ndarray) -> np.ndarray:
    coordinate = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            lo = -np.inf if level == 0 else thresholds[d, level - 1]
            hi = np.inf if level == 3 else thresholds[d, level]
            delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
            coordinate[d, level] = delta * delta
    lut = np.empty((BYTES, 256), dtype=np.float32)
    for byte in range(BYTES):
        for packed in range(256):
            lut[byte, packed] = sum(
                coordinate[byte * 4 + lane, (packed >> (lane * 2)) & 3]
                for lane in range(4)
            )
    scores = np.sum(lut[np.arange(BYTES)[None, :], codes], axis=1,
                   dtype=np.float32)
    return scores


def top128(scores: np.ndarray) -> list[int]:
    ids = np.arange(scores.shape[0], dtype=np.int64)
    order = np.lexsort((ids, scores))[:K]
    return ids[order].astype(int).tolist()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-jsonl", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    native_rows = [json.loads(line) for line in
                   args.native_jsonl.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)
    require(native_rows, "native JSONL is empty")
    q = len(native_rows)
    query_count = args.query_count or q
    require(query_count >= q and args.queries.stat().st_size >= query_count * D * 4,
            "query payload size differs")
    require(args.thq.stat().st_size == N * BYTES, "THQ payload size differs")
    thresholds = np.fromfile(args.thresholds, dtype="<f4")
    require(thresholds.size == D * 3, "threshold payload size differs")
    thresholds = thresholds.reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, BYTES))
    mismatches = []
    for qi, row in enumerate(native_rows):
        require(int(row.get("query", -1)) == qi, f"native query index differs at {qi}")
        expected = top128(byte_lut_scores(np.asarray(thq), thresholds,
                                          np.asarray(queries[qi], dtype=np.float32)))
        observed = [int(value) for value in row.get("cascade_thq_top128_ids", [])]
        if observed != expected:
            mismatches.append({"query": qi, "expected": expected, "observed": observed})
    result = {
        "schema_version": 1,
        "family": "native_full_corpus_thq_top128_parity_v1",
        "status": "PASS" if not mismatches else "FAIL",
        "query_count": q,
        "native_jsonl_sha256": sha(args.native_jsonl),
        "thq_sha256": sha(args.thq),
        "thresholds_sha256": sha(args.thresholds),
        "queries_sha256": sha(args.queries),
        "ordered_top128_exact_parity": q - len(mismatches),
        "mismatch_count": len(mismatches),
    }
    if mismatches:
        result["mismatches"] = mismatches[:3]
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    if mismatches:
        raise SystemExit("native THQ top128 parity differs")


if __name__ == "__main__":
    main()
