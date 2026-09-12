#!/usr/bin/env python3
"""Measure progressive ADC over a packed vertical/AoSoA layout.

The runner reports logical block payload bytes.  It deliberately does not
claim OS/MDBX page faults or warm/cold latency unless a native backend is
added.
"""
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
        lut[col] = np.asarray((
            max(value - t1, 0.0),
            max(t1 - value, 0.0) if value < t1 else max(value - t2, 0.0),
            max(t2 - value, 0.0) if value < t2 else max(value - t3, 0.0),
            max(t3 - value, 0.0),
        ), dtype=np.float32)
        lut[col] *= lut[col]
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


def validate_layout(layout: dict) -> None:
    """Validate every immutable block once, before timed query scans."""
    for row in layout["blocks"]:
        path = Path(row["path"])
        if path.stat().st_size != int(row["bytes"]):
            raise ValueError(f"layout block size mismatch: {path}")
        if hash_file(path) != row["sha256"]:
            raise ValueError(f"layout block hash mismatch: {path}")


def _block_order(layout: dict, lut: np.ndarray, mode: str,
                 level_histogram: np.ndarray | None) -> list[int]:
    count = int(layout["dimension"]) // int(layout["coords_per_block"])
    if mode == "fixed":
        return list(range(count))
    width = int(layout["coords_per_block"])
    scores = []
    for block in range(count):
        lo = block * width
        values = lut[lo:lo + width]
        if mode in ("adc_expected", "adc_expected_cost", "adc_expected_variance"):
            if level_histogram is None:
                raise ValueError("adc_expected ordering requires level histogram")
            probabilities = np.asarray(level_histogram[lo:lo + width], dtype=np.float64)
            probabilities /= np.maximum(probabilities.sum(axis=1, keepdims=True), 1.0)
            means = np.sum(probabilities * values, axis=1)
            if mode == "adc_expected_cost":
                # Expected interval-squared ADC work: prioritize blocks whose
                # corpus-average contribution is largest.
                score = np.sum(means)
            else:
                # Variance is retained as a separate diagnostic; it measures
                # uncertainty, not expected cost.
                score = np.sum(probabilities * (values - means[:, None]) ** 2)
        else:  # adc_lut_variance: diagnostic, unweighted over four levels
            score = np.var(values)
        scores.append(float(score))
    return list(np.argsort(-np.asarray(scores), kind="stable"))


def scan(layout: dict, query: np.ndarray, thresholds: np.ndarray,
         warmup: int, k: int, order_mode: str, check_parity: bool = False,
         force_no_prune: bool = False) -> dict:
    width = int(layout["coords_per_block"])
    n, d = int(layout["documents"]), int(layout["dimension"])
    blocks = {(int(row["tile"]), int(row["block"])): row for row in layout["blocks"]}
    tiles = sorted({tile for tile, _ in blocks})
    lut = query_lut(query, thresholds)
    order = _block_order(layout, lut, order_mode, layout.get("level_histogram"))
    # Accumulate in float64 so block-order changes cannot perturb the
    # canonical score enough to alter top-k membership.
    best_scores = np.full(k, np.inf, dtype=np.float64)
    best_ids = np.full(k, n + 1, dtype=np.int64)
    logical_bytes = 0
    blocks_read = 0
    docs_fully_scored = 0
    coordinate_evaluations = 0
    doc_block_evaluations = 0
    docs_reaching = np.zeros(len(order), dtype=np.int64)
    tile_blocks_skipped = 0
    tiles_fully_pruned = 0
    started = time.perf_counter()

    for tile in tiles:
        first = blocks[(tile, 0)]
        tile_docs = int(first["document_count"])
        tile_start = int(first["document_start"])
        active = np.ones(tile_docs, dtype=bool)
        partial = np.zeros(tile_docs, dtype=np.float64)
        global_ids = np.arange(tile_start, tile_start + tile_docs)
        for stage, block in enumerate(order):
            row = blocks[(tile, block)]
            path = Path(row["path"])
            raw = np.fromfile(path, dtype=np.uint8)
            levels = unpack_block(raw, tile_docs, width)
            lo = block * width
            if active.any():
                lut_block = lut[lo:lo + width]
                contribution = lut_block[np.arange(width)[:, None], levels.T].sum(
                    axis=0, dtype=np.float64
                )
                partial[active] += contribution[active]
                coordinate_evaluations += int(active.sum()) * width
                doc_block_evaluations += int(active.sum())
                docs_reaching[stage] += int(active.sum())
            logical_bytes += int(path.stat().st_size)
            blocks_read += 1
            # Documents before warmup are never pruned; later documents may be.
            can_prune = (global_ids >= warmup) & active
            if not force_no_prune and can_prune.any():
                active[can_prune] &= partial[can_prune] <= best_scores[-1]
            if not active.any():
                tile_blocks_skipped += len(order) - stage - 1
                tiles_fully_pruned += 1
                break
        if active.any():
            ids = global_ids[active]
            best_scores, best_ids = topk_merge(best_scores, best_ids, partial[active], ids, k)
            docs_fully_scored += int(active.sum())

    result = {
        "logical_block_payload_bytes_read": logical_bytes,
        "blocks_read": blocks_read,
        "tile_blocks_skipped": tile_blocks_skipped,
        "tiles_fully_pruned": tiles_fully_pruned,
        "docs_fully_scored": docs_fully_scored,
        "doc_block_evaluations": doc_block_evaluations,
        "coordinate_evaluations": coordinate_evaluations,
        "logical_active_coordinate_fraction": float(coordinate_evaluations / max(1, n * d)),
        "docs_reaching_each_block": docs_reaching.tolist(),
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        "top_ids": best_ids.tolist(),
        "order_mode": order_mode,
    }
    if check_parity:
        exhaustive = scan(layout, query, thresholds, n + 1, k, "fixed", False, True)
        parity = bool(np.array_equal(best_ids, exhaustive["top_ids"]))
        result["exact_top256_parity"] = parity
        if not parity:
            raise AssertionError("progressive AoSoA top-k parity failed")
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
    parser.add_argument("--order", choices=("fixed", "adc_lut_variance", "adc_expected", "adc_expected_cost", "adc_expected_variance"), default="fixed")
    parser.add_argument("--check-parity", action="store_true")
    args = parser.parse_args()
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    if layout.get("documents") != n or layout.get("dimension") != d:
        raise ValueError("layout and frozen manifest dimensions differ")
    expected_manifest_sha = hash_file(args.thq_manifest)
    if layout.get("source_manifest_sha256") != expected_manifest_sha:
        raise ValueError("layout is not bound to the supplied frozen manifest")
    q = min(int(manifest["queries"]), args.query_limit)
    refs, outputs = manifest["references"], manifest["outputs"]
    document_codes_path = Path(outputs["thq4_document_codes"]["path"])
    document_codes_sha = hash_file(document_codes_path)
    if layout.get("source_document_codes_sha256") != document_codes_sha:
        raise ValueError("layout is not bound to the supplied document-code payload")
    declared_codes_sha = outputs["thq4_document_codes"].get("sha256")
    if declared_codes_sha and declared_codes_sha != document_codes_sha:
        raise ValueError("document-code payload hash differs from frozen manifest")
    validate_layout(layout)
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d))
    thresholds = np.memmap(outputs["thq4_thresholds"]["path"], mode="r", dtype="<f4", shape=(d, 3))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    rows = []
    for qi in range(q):
        try:
            row = scan(layout, np.asarray(queries[qi]), np.asarray(thresholds), args.warmup, 256, args.order, args.check_parity)
        except AssertionError as error:
            raise AssertionError(f"query {qi}: {error}") from error
        row.update({"query": qi, "warmup": args.warmup,
                    "teacher_survival_256": float(np.isin(teachers[qi], row["top_ids"]).sum()) / 10.0})
        rows.append(row)
    result = {
        "schema_version": 2,
        "family": "progressive_thq_aosoa_physical_scan_v2",
        "documents": n, "queries": q, "dimension": d,
        "warmup": args.warmup, "rows": rows,
        "layout_manifest": str(args.layout_manifest),
        "execution_status": "EXECUTED" if rows else "PENDING",
        "logical_bytes_only": True,
        "production_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
