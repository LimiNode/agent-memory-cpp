#!/usr/bin/env python3
"""Exact progressive ADC oracle with a runtime (non-privileged) cutoff."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
from numba import njit

@njit
def scan_dynamic(levels, query_levels, lut, order, warmup, k, checkpoints):
    n, d = levels.shape; best = np.full(k, np.inf); active = np.zeros(len(checkpoints), np.int64); evaluated = np.zeros(len(checkpoints), np.int64); cutoff = np.inf
    for row in range(n):
        if row < warmup:
            score = 0.0
            for col in range(d): score += lut[col, levels[row, col]]
            if score < best[-1]:
                best[-1] = score; best.sort(); cutoff = best[-1]
            evaluated[:] += 1; active[:] += 1; continue
        partial = 0.0; checkpoint = 0; alive = True
        for pos in range(d):
            partial += lut[order[pos], levels[row, order[pos]]]
            if partial > cutoff: alive = False; break
            if checkpoint < len(checkpoints) and pos + 1 == checkpoints[checkpoint]:
                active[checkpoint] += 1; checkpoint += 1
        if alive:
            evaluated[:] += 1
            if partial < best[-1]: best[-1] = partial; best.sort(); cutoff = best[-1]
        while checkpoint < len(checkpoints): checkpoint += 1
    return active, evaluated, cutoff

def main():
    p = argparse.ArgumentParser(); p.add_argument("--thq-manifest", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--query-limit", type=int, default=152); p.add_argument("--warmup", type=int, default=4096); p.add_argument("--order", choices=("fixed", "variance"), default="variance"); a = p.parse_args()
    m = json.loads(a.thq_manifest.read_text(encoding="utf-8")); n, d = int(m["documents"]), int(m["dimension"]); q = min(int(m["queries"]), a.query_limit); o = m["outputs"]; levels = np.unpackbits(np.asarray(np.memmap(o["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))), axis=1, bitorder="little")[:, :d * 3].reshape(n, d, 3).sum(2).astype(np.uint8); qlevels = np.unpackbits(np.asarray(np.memmap(o["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144))), axis=1, bitorder="little")[:, :d * 3].reshape(q, d, 3).sum(2).astype(np.uint8); thresholds = np.asarray(np.memmap(o["thq4_thresholds"]["path"], mode="r", dtype="<f4", shape=(d, 3))); queries = np.asarray(np.memmap(m["references"]["queries"]["path"], mode="r", dtype="<f4", shape=(q, d)))
    rows = []; checkpoints = np.array((32, 64, 96, 128, 160, 192, 256, 320, 384), dtype=np.int64)
    for qi in range(q):
        lut = np.empty((d, 4), dtype=np.float32)
        for col, value in enumerate(queries[qi]):
            t1, t2, t3 = thresholds[col]; lut[col] = (max(value-t1, 0), max(t1-value, 0) if value < t1 else (max(value-t2, 0) if value >= t2 else 0), max(t2-value, 0) if value < t2 else (max(value-t3, 0) if value >= t3 else 0), max(t3-value, 0))
        order = np.arange(d, dtype=np.int64) if a.order == "fixed" else np.argsort(-np.var(levels[:min(n, 100000)].astype(np.float32), axis=0)).astype(np.int64); active, evaluated, cutoff = scan_dynamic(levels, qlevels[qi], lut * lut, order, min(a.warmup, n), 256, checkpoints); rows.append({"query": qi, "order": a.order, "warmup": min(a.warmup, n), "cutoff": float(cutoff), "active_fraction": (active / n).tolist(), "evaluated_fraction": (evaluated / n).tolist()})
    result = {"schema_version": 1, "family": "progressive_thq_dynamic_cutoff_oracle_v1", "documents": n, "queries": q, "dimension": d, "checkpoints": checkpoints.tolist(), "rows": rows, "interpretation_status": "EXACT_RUNTIME_CUTOFF_ORACLE_PENDING_EXTERNAL_DE1M_REPLAY", "production_activation": False}; a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
