#!/usr/bin/env python3
"""Directional MIH prototype for packed THQ4 flat-search codes.

The index uses 18 eight-byte bands over the 1152 thermometer bits.  Radius-one
neighbour keys are enumerated only for selected bands, ordered by query
confidence (distance from the corresponding quantile threshold).  This is a
research gate, not an MDBX serving implementation.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def load_manifest(path: Path):
    m = json.loads(path.read_text(encoding="utf-8"))
    if m.get("family") != "thq_full_scan_materialization_v2":
        raise ValueError("THQ v2 manifest required")
    out = m["outputs"]
    codes = np.memmap(out["thq4_document_codes"]["path"], mode="r", dtype=np.uint8,
                      shape=tuple(out["thq4_document_codes"]["shape"]))
    queries = np.fromfile(out["thq4_query_codes"]["path"], dtype=np.uint8).reshape(tuple(out["thq4_query_codes"]["shape"]))
    thresholds = np.fromfile(out["thq4_thresholds"]["path"], dtype=np.float32).reshape(tuple(out["thq4_thresholds"]["shape"]))
    refs = m["references"]
    qvec = np.fromfile(refs["queries"]["path"], dtype=np.float32).reshape((m["queries"], m["dimension"]))
    teacher = np.fromfile(refs["teacher_ids"]["path"], dtype=np.int64).reshape((m["queries"], 10))
    vectors = np.memmap(refs["document_vectors"]["path"], mode="r", dtype=np.float32,
                        shape=(m["documents"], m["dimension"]))
    return m, codes, queries, thresholds, qvec, teacher, vectors


def build_band_index(codes: np.ndarray, band_count: int):
    # THQ4 is exactly 1152 bits.  36 x 32-bit bands provide useful radius-one
    # neighbourhoods without the near-zero collision rate of 64-bit bands.
    width_bytes = 144 // band_count
    words = codes.reshape(codes.shape[0], band_count, width_bytes).view("<u4")[..., 0]
    result = []
    for band in range(band_count):
        order = np.argsort(words[:, band], kind="stable")
        result.append((words[order, band], order))
    return result


def lookup(index, key: int, band: int) -> np.ndarray:
    keys, ids = index[band]
    pos = np.searchsorted(keys, np.uint64(key))
    end = np.searchsorted(keys, np.uint64(key), side="right")
    return ids[pos:end]


def radius_candidates(index, query: np.ndarray, bands: list[int], band_count: int, radius: int) -> tuple[np.ndarray, int]:
    width_bytes = 144 // band_count
    qwords = query.reshape(band_count, width_bytes).view("<u4")[:, 0]
    pieces = []
    probes = 0
    for band in bands:
        key = int(qwords[band]); keys = [key]
        for bit in range(32):
            keys.append(key ^ (1 << bit))
        if radius >= 2:
            for first in range(32):
                for second in range(first + 1, 32):
                    keys.append(key ^ (1 << first) ^ (1 << second))
        for candidate_key in keys:
            pieces.append(lookup(index, candidate_key, band)); probes += 1
    if not pieces:
        return np.empty(0, dtype=np.int32), probes
    return np.unique(np.concatenate(pieces).astype(np.int32)), probes


def hamming_top256(codes: np.ndarray, query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    lut = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(axis=1)
    distances = lut[np.bitwise_xor(codes[candidates], query)].sum(axis=1)
    order = np.lexsort((candidates, distances))
    return candidates[order[:256]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=152)
    parser.add_argument("--bands", type=int, default=8)
    parser.add_argument("--order", choices=("high-confidence", "low-confidence"), default="high-confidence")
    parser.add_argument("--radius", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    m, codes, queries, thresholds, qvec, teacher, vectors = load_manifest(args.manifest)
    band_count = 36
    if not 1 <= args.bands <= band_count:
        raise ValueError(f"bands must be in 1..{band_count}")
    index_started = time.perf_counter(); index = build_band_index(codes, band_count); index_seconds = time.perf_counter() - index_started
    n = min(args.queries, m["queries"]); rows = []
    for row in range(n):
        # One confidence value per thermometer bit; high margin means a stable bit.
        margins = np.abs(qvec[row, :, None] - thresholds) / np.maximum(np.ptp(thresholds, axis=1)[:, None], 1e-8)
        bit_confidence = margins.reshape(-1)
        band_confidence = bit_confidence.reshape(band_count, 32).mean(axis=1)
        order = np.argsort(-band_confidence if args.order == "high-confidence" else band_confidence, kind="stable")
        selected_bands = order[:args.bands].tolist()
        started = time.perf_counter(); candidates, probes = radius_candidates(index, queries[row], selected_bands, band_count, args.radius)
        if candidates.size:
            top = hamming_top256(codes, queries[row], candidates)
        else:
            top = candidates
        elapsed = (time.perf_counter() - started) * 1000.0
        survival = float(np.isin(teacher[row], top).sum()) / 10.0
        rows.append({"query": row, "bands": selected_bands, "candidates": int(candidates.size),
                     "probes": probes, "top10_survival": survival, "elapsed_ms": elapsed,
                     "touched_bytes": int(candidates.size * 144)})
    result = {"schema_version": 1, "family": "thq_directional_mih_reference_v1",
              "manifest": str(args.manifest.resolve()), "documents": int(m["documents"]),
              "queries": n, "bands": args.bands, "band_count": band_count, "order": args.order, "radius": args.radius,
              "index_build_seconds": index_seconds, "mean_candidates": float(np.mean([r["candidates"] for r in rows])),
              "mean_top10_survival": float(np.mean([r["top10_survival"] for r in rows])),
              "p05_top10_survival": float(np.percentile([r["top10_survival"] for r in rows], 5)),
              "worst_top10_survival": float(np.min([r["top10_survival"] for r in rows])),
              "mean_elapsed_ms": float(np.mean([r["elapsed_ms"] for r in rows])),
              "p95_elapsed_ms": float(np.percentile([r["elapsed_ms"] for r in rows], 95)),
              "mean_touched_bytes": float(np.mean([r["touched_bytes"] for r in rows])), "rows": rows}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("bands", "queries", "mean_candidates", "mean_top10_survival", "p05_top10_survival", "worst_top10_survival", "p95_elapsed_ms", "mean_touched_bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
