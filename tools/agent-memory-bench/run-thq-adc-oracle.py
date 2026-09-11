#!/usr/bin/env python3
"""Evaluate asymmetric interval-distance controls for the THQ4 representation.

This is an exhaustive ranking oracle.  It asks whether retaining the query's
continuous FP32 value (rather than only its ordinal level) improves ranking;
it is deliberately not an ANN-index or production benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np


def stable_top(dist: np.ndarray, ids: np.ndarray, k: int) -> np.ndarray:
    k = min(k, len(ids))
    if k == 0:
        return ids[:0]
    if len(ids) <= k:
        return ids[np.lexsort((ids, dist))]
    cutoff = np.partition(dist, k - 1)[k - 1]
    lower = dist < cutoff
    lower_ids = ids[lower]
    ties = np.sort(ids[dist == cutoff])
    selected = np.concatenate((lower_ids, ties[: max(0, k - len(lower_ids))]))
    selected_dist = np.concatenate((dist[lower], np.full(len(selected) - len(lower_ids), cutoff, dtype=dist.dtype)))
    return selected[np.lexsort((selected, selected_dist))]


def summary(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "p05": float(np.quantile(values, .05)),
            "p50": float(np.quantile(values, .5)), "p95": float(np.quantile(values, .95)),
            "min": float(values.min()), "max": float(values.max())}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def interval_costs(thresholds: np.ndarray, queries: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return L1, squared L1, and IQR-normalized L1 costs [query, dim, level]."""
    q = queries[:, :, None]
    t1, t2, t3 = (thresholds[:, i][None, :] for i in range(3))
    l1 = np.stack((np.maximum(q[..., 0] - t1, 0.0),
                   np.maximum(t1 - q[..., 0], 0.0) + np.maximum(q[..., 0] - t2, 0.0),
                   np.maximum(t2 - q[..., 0], 0.0) + np.maximum(q[..., 0] - t3, 0.0),
                   np.maximum(t3 - q[..., 0], 0.0)), axis=2)
    # The middle intervals are contiguous; only one side can be non-zero.
    l1[:, :, 1] = np.where(q[..., 0] < t1, t1 - q[..., 0], np.where(q[..., 0] >= t2, q[..., 0] - t2, 0.0))
    l1[:, :, 2] = np.where(q[..., 0] < t2, t2 - q[..., 0], np.where(q[..., 0] >= t3, q[..., 0] - t3, 0.0))
    squared = l1 * l1
    scale = np.maximum(thresholds[:, 2] - thresholds[:, 0], 1e-6)[None, :]
    return l1.astype(np.float32), squared.astype(np.float32), (l1 / scale[:, :, None]).astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--chunk", type=int, default=20000)
    parser.add_argument("--query-batch", type=int, default=8)
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    refs, outputs = manifest["references"], manifest["outputs"]
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    q = min(int(manifest["queries"]), args.query_limit)
    docs = np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4", shape=(n, d))
    queries = np.asarray(np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d)))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))
    thresholds = np.asarray(np.memmap(outputs["thq4_thresholds"]["path"], mode="r", dtype="<f4", shape=(d, 3)))
    levels = np.empty((n, d), dtype=np.uint8)
    for first in range(0, n, args.chunk):
        stop = min(n, first + args.chunk)
        bits = np.unpackbits(np.asarray(codes[first:stop]), axis=1, bitorder="little")[:, :d * 3]
        levels[first:stop] = bits.reshape(stop - first, d, 3).sum(axis=2)
    qbits = np.packbits((queries[:, :, None] > thresholds[None, :, :]).reshape(q, -1), axis=1, bitorder="little")
    qlevels = np.unpackbits(qbits, axis=1, bitorder="little")[:, :d * 3].reshape(q, d, 3).sum(axis=2).astype(np.uint8)
    l1_cost, sq_cost, norm_cost = interval_costs(thresholds, queries)
    ids = np.arange(n, dtype=np.int64)
    budgets = (64, 128, 256, 512, 1000, 2000)
    systems = {"plain_hamming": [], "interval_l1": [], "interval_squared": [], "interval_iqr_normalized": []}
    for batch_start in range(0, q, max(1, args.query_batch)):
        batch_stop = min(q, batch_start + max(1, args.query_batch))
        batch_size = batch_stop - batch_start
        started = time.perf_counter()
        scores = {name: np.empty((batch_size, n), dtype=np.float32) for name in systems}
        for first in range(0, n, args.chunk):
            stop = min(n, first + args.chunk)
            block = levels[first:stop]
            delta = np.abs(block[None, :, :].astype(np.int16) - qlevels[batch_start:batch_stop, None, :].astype(np.int16))
            scores["plain_hamming"][:, first:stop] = delta.sum(axis=2, dtype=np.uint16)
            for local, qi in enumerate(range(batch_start, batch_stop)):
                for name, costs in (("interval_l1", l1_cost[qi]), ("interval_squared", sq_cost[qi]), ("interval_iqr_normalized", norm_cost[qi])):
                    scores[name][local, first:stop] = costs[np.arange(d), block].sum(axis=1, dtype=np.float32)
        elapsed = (time.perf_counter() - started) * 1000.0 / batch_size
        for local, qi in enumerate(range(batch_start, batch_stop)):
            for name in systems:
                score = scores[name][local]
                row = {"query": qi, "scan_ms": elapsed}
                for budget in budgets:
                    selected = stable_top(score, ids, budget)
                    row[f"survival_{budget}"] = float(np.isin(teachers[qi], selected).sum()) / 10.0
                order = stable_top(score, ids, min(10000, n))
                ranks = np.full(10, n + 1, dtype=np.int64)
                positions = {int(doc): rank for rank, doc in enumerate(order)}
                for index, doc in enumerate(teachers[qi]):
                    if int(doc) in positions:
                        ranks[index] = positions[int(doc)] + 1
                row.update({"teacher_rank_p50": float(np.quantile(ranks, .5)), "teacher_rank_p95": float(np.quantile(ranks, .95)), "teacher_rank_max": int(ranks.max())})
                systems[name].append(row)
    result = {"schema_version": 1, "family": "thq_adc_oracle_v1", "documents": n, "queries": q, "dimension": d,
              "representation": {"name": "THQ4-384", "levels": 4, "bits": 1152, "bytes_per_document": 144,
                                  "metric": "ordinal_l1_equivalent_plain_hamming", "tie_policy": "score_ascending_then_document_id_ascending"},
              "calibration": {"threshold_source": "first_100k_training_documents_from_manifest", "normalization": "per-coordinate training IQR (t3-t1), floor 1e-6"},
              "budgets": list(budgets), "production_activation": False, "systems": {name: {"rows": rows, "summary": {key: summary(rows, key) for key in [f"survival_{b}" for b in budgets] + ["teacher_rank_p50", "teacher_rank_p95", "teacher_rank_max", "scan_ms"]}} for name, rows in systems.items()},
              "source_sha256": {"script": sha256(Path(__file__))}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: value["summary"] for name, value in result["systems"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
