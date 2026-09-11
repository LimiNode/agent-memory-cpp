#!/usr/bin/env python3
"""Measure progressive ADC over a packed vertical/AoSoA layout."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def query_lut(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lut = np.empty((len(query), 4), dtype=np.float32)
    for col, value in enumerate(query):
        t1, t2, t3 = thresholds[col]
        lut[col] = (
            max(value - t1, 0.0),
            max(t1 - value, 0.0) if value < t1 else max(value - t2, 0.0),
            max(t2 - value, 0.0) if value < t2 else max(value - t3, 0.0),
            max(t3 - value, 0.0),
        ) ** 2
    return lut


def unpack_block(raw: np.ndarray, documents: int, width: int) -> np.ndarray:
    packed_width = (width + 3) // 4
    packed = raw.reshape(documents, packed_width)
    levels = np.empty((documents, width), dtype=np.uint8)
    for coordinate in range(width):
        levels[:, coordinate] = (packed[:, coordinate // 4] >> (2 * (coordinate % 4))) & 3
    return levels


def topk_merge(best_scores: np.ndarray, best_ids: np.ndarray,
               scores: np.ndarray, ids: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    if not len(scores):
        return best_scores, best_ids
    all_scores = np.concatenate((best_scores, scores))
    all_ids = np.concatenate((best_ids, ids))
    order = np.lexsort((all_ids, all_scores))[:k]
    return all_scores[order], all_ids[order]


def scan(layout: dict, query: np.ndarray, thresholds: np.ndarray,
         warmup: int, k: int, check_parity: bool = False) -> dict:
    width = int(layout["coords_per_block"])
    n, d = int(layout["documents"]), int(layout["dimension"])
    blocks = {(int(row["tile"]), int(row["block"])): row for row in layout["blocks"]}
    tiles = sorted({tile for tile, _ in blocks})
    block_count = d // width
    lut = query_lut(query, thresholds)
    best_scores = np.full(k, np.inf, dtype=np.float32)
    best_ids = np.full(k, n + 1, dtype=np.int64)
    bytes_read = 0
    blocks_read = 0
    docs_scored = 0
    active_by_block: list[float] = []
    seen_docs = 0
    started = time.perf_counter()

    for tile in tiles:
        first = blocks[(tile, 0)]
        tile_docs = int(first["document_count"])
        tile_start = int(first["document_start"])
        active = np.ones(tile_docs, dtype=bool)
        partial = np.zeros(tile_docs, dtype=np.float32)
        allow_prune = seen_docs >= warmup
        for block in range(block_count):
            row = blocks[(tile, block)]
            path = Path(row["path"])
            if path.stat().st_size != int(row["bytes"]) or hash_file(path) != row["sha256"]:
                raise ValueError(f"layout block hash/size mismatch: {path}")
            raw = np.fromfile(path, dtype=np.uint8)
            levels = unpack_block(raw, tile_docs, width)
            lo = block * width
            contribution = np.zeros(tile_docs, dtype=np.float32)
            if active.any():
                contribution[active] = np.sum(lut[lo:lo + width][np.arange(width), levels[active].T].T, axis=1)
            partial += contribution
            bytes_read += int(path.stat().st_size)
            blocks_read += 1
            active_by_block.append(float(active.mean()))
            if allow_prune:
                active &= partial <= best_scores[-1]
            if not active.any():
                break
        if active.any():
            ids = np.arange(tile_start, tile_start + tile_docs, dtype=np.int64)[active]
            best_scores, best_ids = topk_merge(best_scores, best_ids, partial[active], ids, k)
            docs_scored += int(active.sum())
        seen_docs += tile_docs

    result = {
        "bytes_read": bytes_read,
        "blocks_read": blocks_read,
        "docs_scored": docs_scored,
        "active_fraction_mean": float(np.mean(active_by_block)) if active_by_block else 0.0,
        "active_fraction_last": active_by_block[-1] if active_by_block else 0.0,
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        "top_ids": best_ids.tolist(),
    }
    if check_parity:
        # A no-pruning replay is the exhaustive physical-layout control.
        exhaustive = scan(layout, query, thresholds, n + 1, k, False)
        result["exact_top256_parity"] = bool(np.array_equal(best_ids, exhaustive["top_ids"]))
    else:
        result["exact_top256_parity"] = None
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout-manifest", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--warmup", type=int, default=4096)
    parser.add_argument("--check-parity", action="store_true")
    args = parser.parse_args()
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    if layout.get("documents") != n or layout.get("dimension") != d:
        raise ValueError("layout and frozen manifest dimensions differ")
    q = min(int(manifest["queries"]), args.query_limit)
    refs, outputs = manifest["references"], manifest["outputs"]
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d))
    thresholds = np.memmap(outputs["thq4_thresholds"]["path"], mode="r", dtype="<f4", shape=(d, 3))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    rows = []
    for qi in range(q):
        row = scan(layout, np.asarray(queries[qi]), np.asarray(thresholds), args.warmup, 256, args.check_parity)
        row.update({"query": qi, "warmup": args.warmup, "teacher_survival_256": float(np.isin(teachers[qi], row["top_ids"]).sum()) / 10.0})
        rows.append(row)
    result = {
        "schema_version": 1,
        "family": "progressive_thq_aosoa_physical_scan_v1",
        "documents": n,
        "queries": q,
        "dimension": d,
        "warmup": args.warmup,
        "rows": rows,
        "layout_manifest": str(args.layout_manifest),
        "execution_status": "EXECUTED" if rows else "PENDING",
        "production_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
