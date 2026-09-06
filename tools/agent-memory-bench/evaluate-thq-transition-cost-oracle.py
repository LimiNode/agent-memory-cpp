#!/usr/bin/env python3
"""Teacher-direction ordinal transition-cost oracle for THQ4.

This is deliberately an upper-bound diagnostic.  It uses the first teacher
document as a semantic anchor, scores a continuous prefilter, then ranks the
prefilter by signed ordinal transition cost.  No index or probe generator is
claimed here.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import numpy as np


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--queries", type=int, default=8)
    p.add_argument("--query-start", type=int, default=0)
    p.add_argument("--prefilter", type=int, default=50000)
    args = p.parse_args()
    m = json.loads(args.manifest.read_text(encoding="utf-8")); out = m["outputs"]; refs = m["references"]
    n, q, d = int(m["documents"]), int(m["queries"]), int(m["dimension"])
    docs = np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4", shape=(n, d))
    codes = np.memmap(out["thq4_document_codes"]["path"], mode="r", dtype=np.uint8,
                      shape=tuple(out["thq4_document_codes"]["shape"]))
    queries = np.fromfile(refs["queries"]["path"], dtype="<f4").reshape(q, d)
    teacher = np.fromfile(refs["teacher_ids"]["path"], dtype="<i8").reshape(q, 10)
    query_codes = np.fromfile(out["thq4_query_codes"]["path"], dtype=np.uint8).reshape(q, 144)
    thresholds = np.fromfile(out["thq4_thresholds"]["path"], dtype="<f4").reshape(d, 3)
    qstart = max(0, min(args.query_start, q))
    qcount = min(args.queries, q - qstart); lut = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(axis=1)
    rows = []; started_all = time.perf_counter()
    for qi in range(qstart, qstart + qcount):
        # Establish the actual THQ top-256 reference for this query.
        distance = np.zeros(n, dtype=np.uint16)
        query_code = query_codes[qi]
        for begin in range(0, n, 100000):
            end = min(n, begin + 100000)
            distance[begin:end] = lut[np.bitwise_xor(codes[begin:end], query_code)].sum(axis=1)
        thq_top = np.argpartition(distance, 256)[:256]
        anchor = np.asarray(docs[int(teacher[qi, 0])], dtype=np.float32)
        direction = anchor - queries[qi]
        norm = float(np.dot(direction, direction))
        projection = np.asarray(docs @ direction if n < 200000 else np.empty(n, dtype=np.float32))
        if n >= 200000:
            projection = np.empty(n, dtype=np.float32)
            for begin in range(0, n, 100000):
                end = min(n, begin + 100000)
                projection[begin:end] = np.asarray(docs[begin:end]) @ direction
        pre = np.argpartition(projection, -min(args.prefilter, n))[-min(args.prefilter, n):]
        values = np.asarray(docs[pre], dtype=np.float32)
        levels = (values[:, :, None] > thresholds[None, :, :]).sum(axis=2).astype(np.int8)
        qlevel = (queries[qi, :, None] > thresholds).sum(axis=1).astype(np.int8)
        delta = levels - qlevel[None, :]
        aligned = np.sign(direction)[None, :] * delta > 0
        step_cost = 1.0 / np.maximum(np.abs(direction), 1.0e-4)
        costs = np.where(aligned, np.abs(delta) * step_cost[None, :],
                         np.abs(delta) * (1.0 + step_cost[None, :]))
        costs = costs.sum(axis=1)
        order = np.argsort(costs, kind="stable")
        ranked = pre[order]
        values_out = {}
        for k in (256, 512, 1024, 2048, 5000, 10000):
            selected = ranked[:k]
            values_out[str(k)] = {"thq_top256_recall": float(np.isin(thq_top, selected).sum() / 256.0),
                                  "teacher_top10_survival": float(np.isin(teacher[qi], selected).sum() / 10.0)}
        rows.append({"query": qi, "prefilter": int(len(pre)), "anchor": int(teacher[qi, 0]),
                     "direction_norm": norm, "mean_changed_levels": float(np.mean(np.count_nonzero(delta, axis=1))),
                     "ranked": values_out})
    result = {"schema_version": 1, "family": "thq_transition_cost_oracle_v1",
              "manifest": str(args.manifest.resolve()), "queries": qcount, "prefilter": args.prefilter,
              "query_start": qstart,
              "anchor": "teacher_top1", "rows": rows,
              "elapsed_seconds": time.perf_counter() - started_all}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    aggregate = {k: float(np.mean([r["ranked"][k]["thq_top256_recall"] for r in rows])) for k in ("256", "512", "1024", "2048", "5000", "10000")}
    print(json.dumps({"queries": qcount, "mean_thq_top256_recall": aggregate}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
