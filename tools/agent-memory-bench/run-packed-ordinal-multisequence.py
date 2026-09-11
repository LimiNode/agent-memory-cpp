#!/usr/bin/env python3
"""Packed ordinal-subvector multi-sequence candidate-generation oracle."""
from __future__ import annotations

import argparse
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
    tie = np.sort(ids[dist == cutoff])[: max(0, k - int(lower.sum()))]
    chosen = np.concatenate((ids[lower], tie))
    chosen_dist = np.concatenate((dist[lower], np.full(len(tie), cutoff, dtype=dist.dtype)))
    return chosen[np.lexsort((chosen, chosen_dist))]


def pack(levels: np.ndarray) -> np.ndarray:
    width = levels.shape[1]
    result = np.zeros(levels.shape[0], dtype=np.uint64)
    for col in range(width):
        result |= levels[:, col].astype(np.uint64) << np.uint64(2 * col)
    return result


def summarize(rows: list[dict], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {"mean": float(values.mean()), "p50": float(np.quantile(values, .5)),
            "p95": float(np.quantile(values, .95)), "min": float(values.min()), "max": float(values.max())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--block-widths", default="8,12,16")
    parser.add_argument("--radii", default="0,1")
    parser.add_argument("--chunk", type=int, default=50000)
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    q = min(int(manifest["queries"]), args.query_limit)
    refs, outputs = manifest["references"], manifest["outputs"]
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))
    qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    levels = np.empty((n, d), dtype=np.uint8)
    for first in range(0, n, args.chunk):
        stop = min(n, first + args.chunk)
        bits = np.unpackbits(np.asarray(codes[first:stop]), axis=1, bitorder="little")[:, :d * 3]
        levels[first:stop] = bits.reshape(stop - first, d, 3).sum(axis=2)
    qlevels = np.unpackbits(np.asarray(qcodes), axis=1, bitorder="little")[:, :d * 3].reshape(q, d, 3).sum(axis=2).astype(np.uint8)
    ids = np.arange(n, dtype=np.int64)
    budgets = (256, 512, 1000, 2000, 5000, 10000, 20000, 50000)
    systems: dict[str, dict] = {}
    for width in (int(v) for v in args.block_widths.split(",") if int(v) > 0):
        if d % width:
            raise ValueError(f"block width {width} does not divide {d}")
        blocks = d // width
        doc_orders: list[np.ndarray] = []
        doc_keys: list[np.ndarray] = []
        for block in range(blocks):
            lo, hi = block * width, (block + 1) * width
            keys = pack(levels[:, lo:hi])
            order = np.argsort(keys, kind="stable")
            doc_orders.append(order.astype(np.int64, copy=False))
            doc_keys.append(keys[order])
        for radius in (int(v) for v in args.radii.split(",") if int(v) >= 0):
            rows = []
            for qi in range(q):
                started = time.perf_counter()
                pieces: list[np.ndarray] = []
                probe_count = 0
                for block in range(blocks):
                    lo, hi = block * width, (block + 1) * width
                    base = qlevels[qi, lo:hi]
                    probes = [base]
                    if radius >= 1:
                        for col in range(width):
                            if base[col] > 0:
                                p = base.copy(); p[col] -= 1; probes.append(p)
                            if base[col] < 3:
                                p = base.copy(); p[col] += 1; probes.append(p)
                    for probe in probes:
                        key = int(pack(probe[None, :])[0]); probe_count += 1
                        keys = doc_keys[block]
                        left = int(np.searchsorted(keys, key, side="left")); right = int(np.searchsorted(keys, key, side="right"))
                        if right > left:
                            pieces.append(doc_orders[block][left:right])
                candidates = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype=np.int64)
                generation_ms = (time.perf_counter() - started) * 1000.0
                if len(candidates):
                    dist = np.abs(levels[candidates].astype(np.int16) - qlevels[qi].astype(np.int16)).sum(axis=1, dtype=np.uint16)
                else:
                    dist = np.empty(0, dtype=np.uint16)
                row = {"query": qi, "probe_count": probe_count, "candidate_count": int(len(candidates)),
                       "postings_touched": int(sum(len(v) for v in pieces)), "posting_bytes": int(sum(len(v) for v in pieces) * 4),
                       "code_bytes": int(len(candidates) * 144), "generation_ms": generation_ms}
                for budget in budgets:
                    shortlist = stable_top(dist, candidates, budget)
                    row[f"survival_{budget}"] = float(np.isin(teachers[qi], shortlist).sum()) / 10.0
                rows.append(row)
            name = f"packed_width{width}_r{radius}"
            systems[name] = {"block_width": width, "block_count": blocks, "radius": radius, "rows": rows,
                             "summary": {key: summarize(rows, key) for key in ["probe_count", "candidate_count", "postings_touched", "posting_bytes", "code_bytes", "generation_ms"] + [f"survival_{b}" for b in budgets]}}
    result = {"schema_version": 1, "family": "packed_ordinal_multisequence_oracle_v1", "documents": n, "queries": q, "dimension": d,
              "representation": {"name": "THQ4-384", "levels": 4, "bits": 1152, "bytes_per_document": 144, "metric": "ordinal_l1", "tie_policy": "distance_then_document_id"},
              "budgets": list(budgets), "production_activation": False, "systems": systems}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: value["summary"] for name, value in systems.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
