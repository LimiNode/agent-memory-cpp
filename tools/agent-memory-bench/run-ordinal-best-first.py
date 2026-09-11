#!/usr/bin/env python3
"""PQTable-style best-first ordinal-L1 and interval-ADC oracle.

The harness intentionally keeps the physical index out of scope.  It measures
two independent state-generation metrics over the same occupied Cartesian
product and snapshots one traversal at every requested state budget.
"""
from __future__ import annotations

import argparse
import heapq
import json
import time
from pathlib import Path

import numpy as np


def pack(levels: np.ndarray) -> np.ndarray:
    result = np.zeros(len(levels), dtype=np.uint64)
    for col in range(levels.shape[1]):
        result |= levels[:, col].astype(np.uint64) << np.uint64(2 * col)
    return result


def stable_top(dist: np.ndarray, ids: np.ndarray, k: int) -> np.ndarray:
    k = min(k, len(ids))
    return ids[np.lexsort((ids, dist))[:k]] if k else ids[:0]


def interval_lut(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    result = np.zeros((len(query), 4), dtype=np.float32)
    for i, value in enumerate(query):
        t1, t2, t3 = thresholds[i]
        result[i] = (
            max(value - t1, 0.0),
            max(t1 - value, 0.0)
            if value < t1
            else (max(value - t2, 0.0) if value >= t2 else 0.0),
            max(t2 - value, 0.0)
            if value < t2
            else (max(value - t3, 0.0) if value >= t3 else 0.0),
            max(t3 - value, 0.0),
        )
    return result


def _union_pieces(pieces: list[np.ndarray]) -> np.ndarray:
    return (
        np.unique(np.concatenate(pieces)).astype(np.int64)
        if pieces
        else np.empty(0, dtype=np.int64)
    )


def enumerate_candidates(
    blocks: list[dict], budgets: list[int]
) -> dict[int, dict]:
    """Enumerate occupied Cartesian states once and snapshot at each budget."""
    requested = sorted(set(int(value) for value in budgets if int(value) > 0))
    if not requested:
        raise ValueError("at least one positive state budget is required")
    start = tuple(0 for _ in blocks)
    heap: list[tuple[float, tuple[int, ...]]] = [
        (sum(float(block["costs"][0]) for block in blocks), start)
    ]
    seen = {start}
    pieces: list[np.ndarray] = []
    visited = 0
    nonempty = 0
    intersections = 0
    snapshots: dict[int, dict] = {}

    while heap and visited < requested[-1]:
        score, state = heapq.heappop(heap)
        visited += 1
        posting = blocks[0]["postings"][state[0]]
        for block, index in zip(blocks[1:], state[1:]):
            intersections += 1
            posting = np.intersect1d(
                posting, block["postings"][index], assume_unique=True
            )
            if not len(posting):
                break
        if len(posting):
            pieces.append(posting)
            nonempty += 1

        for axis, block in enumerate(blocks):
            nxt = state[axis] + 1
            if nxt >= len(block["postings"]):
                continue
            successor = list(state)
            successor[axis] = nxt
            successor = tuple(successor)
            if successor not in seen:
                seen.add(successor)
                next_score = (
                    score
                    - float(block["costs"][state[axis]])
                    + float(block["costs"][nxt])
                )
                heapq.heappush(heap, (next_score, successor))

        for budget in requested:
            if budget not in snapshots and visited >= budget:
                candidates = _union_pieces(pieces)
                snapshots[budget] = {
                    "candidates": candidates,
                    "states_visited": visited,
                    "non_empty_tuples": nonempty,
                    "intersection_operations": intersections,
                    "heap_exhausted": not heap,
                }

    if snapshots and len(snapshots) < len(requested):
        candidates = _union_pieces(pieces)
        for budget in requested:
            snapshots.setdefault(
                budget,
                {
                    "candidates": candidates,
                    "states_visited": visited,
                    "non_empty_tuples": nonempty,
                    "intersection_operations": intersections,
                    "heap_exhausted": True,
                },
            )
    return snapshots


def _make_blocks(index: list[tuple[list[np.ndarray], np.ndarray]], lut: np.ndarray,
                 qlevels: np.ndarray, width: int, metric: str) -> list[dict]:
    blocks: list[dict] = []
    for block_number, (postings, states) in enumerate(index):
        lo = block_number * width
        local_query = qlevels[lo : lo + width]
        l1 = np.abs(states.astype(np.int16) - local_query.astype(np.int16)).sum(
            axis=1, dtype=np.uint16
        )
        # The LUT is global.  Block-local states must use their global offset;
        # otherwise every block after block zero is scored against coordinates
        # 0..width-1 instead of lo..lo+width-1.
        adc = np.asarray(
            [
                sum(lut[lo + local_col, int(level)] ** 2
                    for local_col, level in enumerate(state))
                for state in states
            ],
            dtype=np.float32,
        )
        costs = l1.astype(np.float32) if metric == "ordinal_l1" else adc
        order = np.lexsort((np.arange(len(costs)), costs))
        blocks.append(
            {
                "postings": [postings[int(i)] for i in order],
                "costs": costs[order],
            }
        )
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--state-budgets", default="1000,5000,20000,50000")
    parser.add_argument("--block-widths", default="8,12,16,24,32")
    args = parser.parse_args()
    budgets = [int(value) for value in args.state_budgets.split(",") if value]

    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    q = min(int(manifest["queries"]), args.query_limit)
    refs, outputs = manifest["references"], manifest["outputs"]
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r",
                      dtype=np.uint8, shape=(n, 144))
    qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r",
                       dtype=np.uint8, shape=(q, 144))
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4",
                        shape=(q, d))
    thresholds = np.memmap(outputs["thq4_thresholds"]["path"], mode="r",
                           dtype="<f4", shape=(d, 3))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8",
                         shape=(q, 10))
    levels = np.unpackbits(np.asarray(codes), axis=1, bitorder="little")[:, : d * 3]
    levels = levels.reshape(n, d, 3).sum(axis=2).astype(np.uint8)
    qlevels = np.unpackbits(np.asarray(qcodes), axis=1, bitorder="little")[:, : d * 3]
    qlevels = qlevels.reshape(q, d, 3).sum(axis=2).astype(np.uint8)

    rows = []
    for width in (int(value) for value in args.block_widths.split(",") if int(value) > 0):
        if d % width:
            raise ValueError(f"block width {width} does not divide dimension {d}")
        index = []
        for block in range(d // width):
            lo, hi = block * width, (block + 1) * width
            keys = pack(levels[:, lo:hi])
            unique, inverse = np.unique(keys, return_inverse=True)
            order = np.argsort(inverse, kind="stable")
            starts = np.searchsorted(inverse[order], np.arange(len(unique)), side="left")
            ends = np.searchsorted(inverse[order], np.arange(len(unique)), side="right")
            postings = [order[int(starts[i]):int(ends[i])].astype(np.int64)
                        for i in range(len(unique))]
            states = np.array(
                [[(int(key) >> (2 * col)) & 3 for col in range(width)]
                 for key in unique], dtype=np.uint8
            )
            index.append((postings, states))

        for qi in range(q):
            started = time.perf_counter()
            lut = interval_lut(np.asarray(queries[qi]), np.asarray(thresholds))
            for metric in ("ordinal_l1", "interval_squared_adc"):
                blocks = _make_blocks(index, lut, qlevels[qi], width, metric)
                snapshots = enumerate_candidates(blocks, budgets)
                for budget in sorted(snapshots):
                    snapshot = snapshots[budget]
                    candidates = snapshot["candidates"]
                    if metric == "ordinal_l1":
                        distance = np.abs(
                            levels[candidates].astype(np.int16)
                            - qlevels[qi].astype(np.int16)
                        ).sum(axis=1, dtype=np.uint16)
                    else:
                        distance = np.asarray(
                            [
                                sum(lut[col, int(level)] ** 2
                                    for col, level in enumerate(levels[doc]))
                                for doc in candidates
                            ],
                            dtype=np.float32,
                        )
                    selected = stable_top(distance, candidates, 256)
                    rows.append({
                        "query": qi,
                        "block_width": width,
                        "generator": metric,
                        "state_budget": budget,
                        "states_visited": snapshot["states_visited"],
                        "non_empty_tuples": snapshot["non_empty_tuples"],
                        "empty_tuple_fraction": (
                            1.0 - snapshot["non_empty_tuples"] / snapshot["states_visited"]
                            if snapshot["states_visited"] else 0.0
                        ),
                        "intersection_operations": snapshot["intersection_operations"],
                        "candidate_count": int(len(candidates)),
                        "generation_ms": (time.perf_counter() - started) * 1000.0,
                        "survival_256": float(np.isin(teachers[qi], selected).sum()) / 10.0,
                        "rerank_metric": metric,
                    })

    result = {
        "schema_version": 3,
        "family": "ordinal_pqtable_best_first_oracle_v3",
        "documents": n,
        "queries": q,
        "dimension": d,
        "state_budgets": sorted(set(budgets)),
        "rows": rows,
        "metrics": ["ordinal_l1", "interval_squared_adc"],
        "single_traversal_snapshots": True,
        "interpretation_status": "PQTABLE_STYLE_OCCUPIED_STATE_ORACLE_NOT_PHYSICAL_INDEX",
        "production_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
