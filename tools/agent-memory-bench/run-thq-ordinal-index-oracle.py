#!/usr/bin/env python3
"""Matched-budget oracle for flat THQ, bit-MIH, and ordinal block postings.

This is intentionally an algorithmic oracle.  It uses exact in-memory posting
lookups and reports candidate-generation work; it does not claim a persistent
or production index.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

POPCOUNT = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)


def stable_top(dist: np.ndarray, ids: np.ndarray, k: int) -> np.ndarray:
    k = min(k, len(ids))
    if k == 0:
        return ids[:0]
    if len(ids) > k:
        chosen = np.argpartition(dist, k - 1)[:k]
        dist, ids = dist[chosen], ids[chosen]
    return ids[np.lexsort((ids, dist))[:k]]


def summarize(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "p50": float(np.quantile(values, .5)),
            "p95": float(np.quantile(values, .95)), "max": float(values.max())}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--query-limit", type=int, default=152)
    p.add_argument("--block-counts", default="8,12,16,24,32")
    p.add_argument("--budgets", default="256,512,1000,2000,5000,10000,20000,50000")
    p.add_argument("--chunk", type=int, default=20000)
    args = p.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    refs, outputs = manifest["references"], manifest["outputs"]
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    q = min(int(manifest["queries"]), args.query_limit)
    docs = np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4", shape=(n, d))
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))
    qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144))
    levels = np.empty((n, d), dtype=np.uint8)
    for first in range(0, n, args.chunk):
        stop = min(n, first + args.chunk)
        bits = np.unpackbits(np.asarray(codes[first:stop]), axis=1, bitorder="little")
        levels[first:stop] = bits.reshape(stop - first, d, 3).sum(axis=2)
    qlevels = np.unpackbits(np.asarray(qcodes), axis=1, bitorder="little").reshape(q, d, 3).sum(axis=2)
    ids_all = np.arange(n, dtype=np.int64)
    budgets = [int(v) for v in args.budgets.split(",") if int(v) > 0]
    block_counts = [int(v) for v in args.block_counts.split(",") if int(v) > 0]

    def distance(ids: np.ndarray, qc: np.ndarray) -> np.ndarray:
        return POPCOUNT[np.bitwise_xor(np.asarray(codes[ids]), qc)].sum(axis=1, dtype=np.uint16)

    result = {"schema_version": 1, "family": "thq_ordinal_index_oracle_v1",
              "documents": n, "queries": q, "dimension": d, "representation": {
                  "name": "THQ4-384", "levels": 4, "bits": 1152,
                  "metric": "ordinal_l1_equivalent_plain_hamming",
                  "tie_policy": "distance_ascending_then_document_id_ascending"},
              "budgets": budgets, "systems": {}}

    # Flat reference: one exhaustive distance pass, then matched top-K cuts.
    flat_rows = []
    for qi in range(q):
        started = time.perf_counter()
        all_dist = np.empty(n, dtype=np.uint16)
        for first in range(0, n, args.chunk):
            stop = min(n, first + args.chunk)
            all_dist[first:stop] = distance(np.arange(first, stop), qcodes[qi])
        elapsed = (time.perf_counter() - started) * 1000.0
        row = {"query": qi, "candidate_count": n, "postings_touched": n,
               "generation_ms": elapsed}
        for budget in budgets:
            top = stable_top(all_dist, ids_all, budget)
            row[f"survival_{budget}"] = float(np.isin(teachers[qi], top).sum()) / 10.0
        flat_rows.append(row)
    result["systems"]["flat"] = {"rows": flat_rows,
        "summary": {k: summarize(flat_rows, k) for k in ["candidate_count", "generation_ms"] + [f"survival_{b}" for b in budgets]}}

    for m in block_counts:
        boundaries = np.linspace(0, d, m + 1, dtype=np.int32)
        block_sums = np.empty((n, m), dtype=np.uint16)
        query_sums = np.empty((q, m), dtype=np.uint16)
        for block in range(m):
            lo, hi = int(boundaries[block]), int(boundaries[block + 1])
            block_sums[:, block] = levels[:, lo:hi].sum(axis=1, dtype=np.uint16)
            query_sums[:, block] = qlevels[:, lo:hi].sum(axis=1, dtype=np.uint16)
        # Materialize sorted posting ranges once; query lookup is then a pair
        # of binary searches rather than a full-corpus mask for every query.
        posting_orders = []
        posting_keys = []
        for block in range(m):
            order = np.argsort(block_sums[:, block], kind="stable")
            posting_orders.append(order.astype(np.int64, copy=False))
            posting_keys.append(block_sums[order, block])
        rows = []
        for qi in range(q):
            started = time.perf_counter()
            pieces = []
            for block in range(m):
                keys = posting_keys[block]
                lo = int(np.searchsorted(keys, query_sums[qi, block], side="left"))
                hi = int(np.searchsorted(keys, query_sums[qi, block], side="right"))
                pieces.append(posting_orders[block][lo:hi])
            nonempty = [piece for piece in pieces if len(piece)]
            candidates = np.unique(np.concatenate(nonempty)) if nonempty else np.empty(0, dtype=np.int64)
            gen_ms = (time.perf_counter() - started) * 1000.0
            distances = distance(candidates, qcodes[qi]) if len(candidates) else np.empty(0, dtype=np.uint16)
            row = {"query": qi, "candidate_count": int(len(candidates)),
                   "postings_touched": int(sum(len(piece) for piece in pieces)),
                   "generation_ms": gen_ms}
            for budget in budgets:
                shortlist = stable_top(distances, candidates, budget)
                row[f"survival_{budget}"] = float(np.isin(teachers[qi], shortlist).sum()) / 10.0
            rows.append(row)
        name = f"ordinal_sum_m{m}"
        result["systems"][name] = {"block_count": m, "block_key": "exact_ordinal_level_sum",
            "rows": rows, "summary": {k: summarize(rows, k) for k in ["candidate_count", "postings_touched", "generation_ms"] + [f"survival_{b}" for b in budgets]}}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: value["summary"] for name, value in result["systems"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
