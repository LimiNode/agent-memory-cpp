#!/usr/bin/env python3
"""Exact bucket/radius-one bit-MIH oracle on semantic THQ4 queries."""
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
    if len(ids) <= k:
        return ids[np.lexsort((ids, dist))]
    cutoff = np.partition(dist, k - 1)[k - 1]
    lower = dist < cutoff
    lower_ids = ids[lower]
    tie_ids = np.sort(ids[dist == cutoff])
    selected_ties = tie_ids[: max(0, k - len(lower_ids))]
    selected = np.concatenate((lower_ids, selected_ties))
    selected_dist = np.concatenate((dist[lower],
                                    np.full(len(selected_ties), cutoff, dtype=dist.dtype)))
    return selected[np.lexsort((selected, selected_dist))]


def summary(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "p50": float(np.quantile(values, .5)),
            "p95": float(np.quantile(values, .95)), "min": float(values.min()),
            "max": float(values.max())}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--query-limit", type=int, default=152)
    # Short bands are required for a meaningful MIH control at 1152 bits.
    # Long 72--144-bit bands have effectively no collisions on 1M records.
    p.add_argument("--band-counts", default="48,72,144")
    p.add_argument("--radii", default="0,1")
    p.add_argument("--budgets", default="256,512,1000,2000,5000,10000,20000,50000")
    p.add_argument("--chunk", type=int, default=20000)
    args = p.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    outputs, refs = manifest["outputs"], manifest["references"]
    n, d, q = int(manifest["documents"]), int(manifest["dimension"]), min(int(manifest["queries"]), args.query_limit)
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))
    qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    ids_all = np.arange(n, dtype=np.int64)
    budgets = [int(v) for v in args.budgets.split(",") if int(v) > 0]
    radii = [int(v) for v in args.radii.split(",") if int(v) >= 0]
    result = {"schema_version": 1, "family": "thq_bit_mih_oracle_v1", "documents": n,
              "queries": q, "dimension": d, "representation": {"name": "THQ4-384",
              "bits": 1152, "metric": "plain_hamming", "tie_policy": "distance_then_id"},
              "budgets": budgets, "systems": {}}

    for m in [int(v) for v in args.band_counts.split(",") if int(v) > 0]:
        if 144 % m:
            raise ValueError(f"band count {m} does not divide 144 bytes")
        width = 144 // m
        orders, keys = [], []
        for band in range(m):
            lo, hi = band * width, (band + 1) * width
            raw = np.asarray(codes[:, lo:hi]).copy().view(f"V{width}").reshape(n)
            order = np.argsort(raw, kind="stable")
            orders.append(order.astype(np.int64, copy=False)); keys.append(raw[order])
        for radius in radii:
            rows = []
            for qi in range(q):
                started = time.perf_counter()
                pieces = []
                for band in range(m):
                    lo, hi = band * width, (band + 1) * width
                    base = np.asarray(qcodes[qi, lo:hi]).copy()
                    probes = [base]
                    if radius >= 1:
                        for byte in range(width):
                            for bit in range(8):
                                probe = base.copy(); probe[byte] ^= np.uint8(1 << bit); probes.append(probe)
                    for probe in probes:
                        key = np.frombuffer(probe.tobytes(), dtype=f"V{width}")[0]
                        lo_i = int(np.searchsorted(keys[band], key, side="left"))
                        hi_i = int(np.searchsorted(keys[band], key, side="right"))
                        if hi_i > lo_i: pieces.append(orders[band][lo_i:hi_i])
                candidates = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype=np.int64)
                gen_ms = (time.perf_counter() - started) * 1000.0
                dist = POPCOUNT[np.bitwise_xor(np.asarray(codes[candidates]), qcodes[qi])].sum(axis=1, dtype=np.uint16) if len(candidates) else np.empty(0, dtype=np.uint16)
                row = {"query": qi, "candidate_count": int(len(candidates)), "postings_touched": int(sum(len(v) for v in pieces)), "generation_ms": gen_ms}
                for budget in budgets:
                    shortlist = stable_top(dist, candidates, budget)
                    row[f"survival_{budget}"] = float(np.isin(teachers[qi], shortlist).sum()) / 10.0
                rows.append(row)
            name = f"bit_mih_m{m}_r{radius}"
            result["systems"][name] = {"bands": m, "radius": radius, "rows": rows,
                "summary": {k: summary(rows, k) for k in ["candidate_count", "postings_touched", "generation_ms"] + [f"survival_{b}" for b in budgets]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: value["summary"] for name, value in result["systems"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
