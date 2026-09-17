#!/usr/bin/env python3
"""Fail-closed native THQ parity against an independent coordinate scorer."""
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


def coordinate_costs(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    coordinate = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            lo = -np.inf if level == 0 else thresholds[d, level - 1]
            hi = np.inf if level == 3 else thresholds[d, level]
            delta = lo - query[d] if query[d] < lo else query[d] - hi if query[d] > hi else 0.0
            coordinate[d, level] = delta * delta
    return coordinate


def coordinate_scores(codes: np.ndarray, thresholds: np.ndarray,
                      query: np.ndarray, chunk_rows: int) -> np.ndarray:
    """Accumulate 384 coordinate costs without constructing a byte LUT."""
    coordinate = coordinate_costs(thresholds, query)
    scores = np.empty(codes.shape[0], dtype=np.float32)
    for begin in range(0, codes.shape[0], chunk_rows):
        end = min(begin + chunk_rows, codes.shape[0])
        packed_rows = np.asarray(codes[begin:end])
        total = np.zeros(end - begin, dtype=np.float32)
        for byte in range(BYTES):
            packed = packed_rows[:, byte]
            for lane in range(4):
                dimension = byte * 4 + lane
                levels = (packed >> (lane * 2)) & 3
                total += coordinate[dimension, levels]
        scores[begin:end] = total
    return scores


def ordered_ids(scores: np.ndarray, limit: int) -> list[int]:
    ids = np.arange(scores.shape[0], dtype=np.int64)
    order = np.lexsort((ids, scores))[:limit]
    return ids[order].astype(int).tolist()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-jsonl", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int)
    parser.add_argument("--chunk-rows", type=int, default=65_536)
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
    require(args.chunk_rows > 0, "chunk row count must be positive")
    set_mismatches = []
    order_differences = []
    diagnostics = []
    score_bound_failures = []
    relative_score_bound = 5.0e-5
    for qi, row in enumerate(native_rows):
        require(int(row.get("query", -1)) == qi, f"native query index differs at {qi}")
        scores = coordinate_scores(thq, thresholds,
                                   np.asarray(queries[qi], dtype=np.float32),
                                   args.chunk_rows)
        expected_shell = ordered_ids(scores, K + 1)
        expected = expected_shell[:K]
        observed = [int(value) for value in row.get("cascade_thq_top128_ids", [])]
        observed_scores = np.asarray(row.get("cascade_thq_top128_scores", []),
                                     dtype=np.float32)
        require(len(observed) == K, f"native top-128 ID count differs at {qi}")
        require(len(set(observed)) == K, f"native top-128 IDs are duplicated at {qi}")
        require(all(0 <= value < N for value in observed),
                f"native top-128 ID is out of range at {qi}")
        require(observed_scores.shape == (K,),
                f"native top-128 score count differs at {qi}")
        if set(observed) != set(expected):
            set_mismatches.append({"query": qi, "expected": expected, "observed": observed})
        elif observed != expected:
            order_differences.append({"query": qi, "expected": expected, "observed": observed})
        reference_observed_scores = scores[np.asarray(observed, dtype=np.int64)]
        absolute_error = np.abs(observed_scores - reference_observed_scores)
        allowed_error = np.maximum(np.float32(1.0e-6),
                                   np.abs(reference_observed_scores) * relative_score_bound)
        if np.any(absolute_error > allowed_error):
            score_bound_failures.append({
                "query": qi,
                "max_absolute_error": float(np.max(absolute_error)),
                "max_allowed_error": float(np.max(allowed_error)),
            })
        cutoff_score = float(scores[expected_shell[K - 1]])
        next_score = float(scores[expected_shell[K]])
        diagnostics.append({
            "query": qi,
            "ordered_top128_parity": observed == expected,
            "set_top128_parity": set(observed) == set(expected),
            "max_absolute_score_error": float(np.max(absolute_error)),
            "cutoff_score": cutoff_score,
            "rank129_score": next_score,
            "cutoff_gap": next_score - cutoff_score,
            "cutoff_exact_tie_count": int(np.count_nonzero(scores == np.float32(cutoff_score))),
        })
    failed = bool(set_mismatches or score_bound_failures)
    result = {
        "schema_version": 2,
        "family": "native_full_corpus_thq_coordinate_parity_v2",
        "status": "PASS" if not failed else "FAIL",
        "query_count": q,
        "native_jsonl_sha256": sha(args.native_jsonl),
        "thq_sha256": sha(args.thq),
        "thresholds_sha256": sha(args.thresholds),
        "queries_sha256": sha(args.queries),
        "candidate_set_top128_exact_parity": q - len(set_mismatches),
        "ordered_top128_exact_parity": q - len(set_mismatches) - len(order_differences),
        "set_mismatch_count": len(set_mismatches),
        "order_difference_count": len(order_differences),
        "score_bound_failure_count": len(score_bound_failures),
        "acceptance_contract": (
            "exact top-128 candidate-set parity plus bounded score error; "
            "internal candidate order is diagnostic because all 128 documents are reranked"
        ),
        "coordinate_reference": {
            "metric": "interval_squared",
            "accumulation": "384 sequential float32 coordinate additions",
            "byte_lut_used": False,
            "chunk_rows": args.chunk_rows,
            "relative_score_error_bound": relative_score_bound,
            "absolute_score_error_floor": 1.0e-6,
        },
        "max_absolute_score_error": max(
            item["max_absolute_score_error"] for item in diagnostics
        ),
        "queries_with_exact_cutoff_ties": sum(
            item["cutoff_exact_tie_count"] > 1 for item in diagnostics
        ),
        "diagnostics": diagnostics,
    }
    if set_mismatches:
        result["set_mismatches"] = set_mismatches[:3]
    if order_differences:
        result["order_differences"] = order_differences[:3]
    if score_bound_failures:
        result["score_bound_failures"] = score_bound_failures[:3]
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    if failed:
        raise SystemExit("native THQ coordinate-reference parity differs")


if __name__ == "__main__":
    main()
