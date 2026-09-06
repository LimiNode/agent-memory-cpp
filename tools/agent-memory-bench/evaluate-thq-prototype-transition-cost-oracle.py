#!/usr/bin/env python3
"""Prototype-direction ordinal transition-cost oracle for THQ4.

This is an offline ceiling diagnostic.  It uses the exact teacher prototype
as a direction anchor, exhaustively builds the THQ4 prototype reference, then
orders a continuous projection prefilter by the same signed ordinal cost used
by the teacher-direction oracle.  It does not claim a serving index.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import numpy as np


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prototype-source", type=Path, required=True)
    p.add_argument("--prototype-targets", type=Path, required=True)
    p.add_argument("--thresholds", type=Path, required=True)
    p.add_argument("--prototype-codes", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--queries", type=int, default=8)
    p.add_argument("--query-start", type=int, default=0)
    p.add_argument("--prefilter", type=int, default=50000)
    p.add_argument("--block-size", type=int, default=100000)
    args = p.parse_args()

    with np.load(args.prototype_source, mmap_mode="r", allow_pickle=False) as z:
        prototypes = np.asarray(z["prototype_vectors"], dtype=np.float32)
        queries = np.asarray(z["queries"], dtype=np.float32)
    with np.load(args.prototype_targets, allow_pickle=False) as z:
        targets = np.asarray(z["prototype_targets"], dtype=np.int64)
    n, q, d = prototypes.shape[0], queries.shape[0], prototypes.shape[1]
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(384, 3)
    codes = (np.memmap(args.prototype_codes, mode="r", dtype=np.uint8,
                       shape=(n, 144)) if args.prototype_codes else None)
    if targets.shape[0] != q or d != 384:
        raise ValueError("prototype source/target dimensions differ")
    qstart = max(0, min(args.query_start, q))
    qcount = min(args.queries, q - qstart)
    rows = []
    started = time.perf_counter()
    for qi in range(qstart, qstart + qcount):
        anchor = int(targets[qi, 0])
        direction = prototypes[anchor] - queries[qi]
        projection = np.empty(n, dtype=np.float32)
        distance = np.empty(n, dtype=np.uint16)
        # The packed-code cache makes the exhaustive THQ reference cheap; the
        # continuous projection remains the only full float traversal.
        qlevels = (queries[qi, :, None] > thresholds).sum(axis=1).astype(np.int8)
        qbits = (queries[qi, :, None] > thresholds).reshape(1, -1)
        qpacked = np.packbits(qbits, axis=1, bitorder="little")[0]
        for begin in range(0, n, args.block_size):
            end = min(n, begin + args.block_size)
            block = np.asarray(prototypes[begin:end], dtype=np.float32)
            if codes is None:
                bits = (block[:, :, None] > thresholds[None, :, :]).reshape(end - begin, -1)
                packed = np.packbits(bits, axis=1, bitorder="little")
            else:
                packed = np.asarray(codes[begin:end])
            distance[begin:end] = np.unpackbits(
                np.bitwise_xor(packed, qpacked[None, :]), axis=1).sum(axis=1)
            projection[begin:end] = block @ direction
        top = np.argpartition(distance, 256)[:256]
        limit = min(args.prefilter, n)
        pre = np.argpartition(projection, -limit)[-limit:]
        values = np.asarray(prototypes[pre], dtype=np.float32)
        levels = (values[:, :, None] > thresholds[None, :, :]).sum(axis=2).astype(np.int8)
        delta = levels - qlevels[None, :]
        aligned = np.sign(direction)[None, :] * delta > 0
        step_cost = 1.0 / np.maximum(np.abs(direction), 1.0e-4)
        costs = np.where(aligned, np.abs(delta) * step_cost[None, :],
                         np.abs(delta) * (1.0 + step_cost[None, :])).sum(axis=1)
        ranked = pre[np.argsort(costs, kind="stable")]
        ranked_out = {}
        for k in (256, 512, 1024, 2048, 5000, 10000):
            selected = ranked[:k]
            ranked_out[str(k)] = {
                "thq_top256_recall": float(np.isin(top, selected).sum() / 256.0),
                "teacher_top10_survival": float(np.isin(targets[qi], selected).sum() / 10.0),
            }
        rows.append({"query": qi, "anchor": anchor, "prefilter": int(len(pre)),
                     "direction_norm": float(np.dot(direction, direction)),
                     "ranked": ranked_out})
    result = {"schema_version": 1, "family": "thq_prototype_transition_cost_oracle_v1",
              "prototype_source": str(args.prototype_source.resolve()),
              "prototype_targets": str(args.prototype_targets.resolve()),
              "queries": qcount, "query_start": qstart, "prefilter": args.prefilter,
              "rows": rows, "elapsed_seconds": time.perf_counter() - started}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    keys = ("256", "512", "1024", "2048", "5000", "10000")
    print(json.dumps({"queries": qcount, "mean_thq_top256_recall": {
        k: float(np.mean([r["ranked"][k]["thq_top256_recall"] for r in rows])) for k in keys}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
