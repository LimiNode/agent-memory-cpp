#!/usr/bin/env python3
"""Exact THQ-ADC page-summary (Block-Min) oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lut_for(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    result = np.empty((len(query), 4), dtype=np.float64)
    for col, value in enumerate(query):
        t1, t2, t3 = thresholds[col]
        distances = np.asarray((
            max(value - t1, 0.0),
            max(t1 - value, 0.0) if value < t1 else max(value - t2, 0.0),
            max(t2 - value, 0.0) if value < t2 else max(value - t3, 0.0),
            max(t3 - value, 0.0),
        ), dtype=np.float64)
        result[col] = distances ** 2
    return result


def unpack(raw: np.ndarray, docs: int, width: int) -> np.ndarray:
    packed = raw.reshape(docs, (width + 3) // 4)
    levels = np.empty((docs, width), dtype=np.uint8)
    for col in range(width):
        levels[:, col] = (packed[:, col // 4] >> (2 * (col % 4))) & 3
    return levels


def min_bound(masks: np.ndarray, lut: np.ndarray, lo: int) -> float:
    total = 0.0
    for local, mask in enumerate(masks):
        present = np.flatnonzero([(int(mask) >> level) & 1 for level in range(4)])
        total += float(np.min(lut[lo + local, present]))
    return total


def topk_merge(scores: np.ndarray, ids: np.ndarray, new_scores: np.ndarray,
               new_ids: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    all_scores = np.concatenate((scores, new_scores))
    all_ids = np.concatenate((ids, new_ids))
    order = np.lexsort((all_ids, all_scores))[:k]
    return all_scores[order], all_ids[order]


def ids_sha256(ids: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(ids, dtype='<i8').tobytes()).hexdigest()


def scan(layout: dict, summaries: dict, query: np.ndarray,
         thresholds: np.ndarray, warmup: int, k: int,
         check_parity: bool) -> dict:
    width = int(layout["coords_per_block"])
    n = int(layout["documents"])
    lut = lut_for(query, thresholds)
    rows = {(int(row["tile"]), int(row["block"])): row
            for row in layout["blocks"]}
    tile_ids = sorted({tile for tile, _ in rows})
    summary_rows = {int(row["tile"]): row for row in summaries["tiles"]}
    best_scores = np.full(k, np.inf, dtype=np.float64)
    best_ids = np.full(k, n + 1, dtype=np.int64)
    payload_bytes = summary_bytes = 0
    tiles_skipped = blocks_read = blocks_skipped = 0
    for tile in tile_ids:
        srow = summary_rows[tile]
        summary_path = Path(srow["path"])
        masks = np.fromfile(summary_path, dtype=np.uint8).reshape(
            int(srow["blocks"]), width)
        summary_bytes += int(summary_path.stat().st_size)
        bounds = np.asarray([
            min_bound(masks[block], lut, block * width)
            for block in range(masks.shape[0])
        ])
        if bounds.sum() > best_scores[-1]:
            tiles_skipped += 1
            continue
        first = rows[(tile, 0)]
        docs = int(first["document_count"])
        start = int(first["document_start"])
        active = np.ones(docs, dtype=bool)
        partial = np.zeros(docs, dtype=np.float64)
        ids = np.arange(start, start + docs)
        for block in range(masks.shape[0]):
            row = rows[(tile, block)]
            path = Path(row["path"])
            # A block can be skipped before payload I/O only when every active
            # document's current partial score plus the block lower bound is
            # already worse than the exact kth threshold.  This is the
            # Block-Min analogue of a WAND upper-bound test.
            if active.any() and np.min(partial[active]) + bounds[block] > best_scores[-1]:
                blocks_skipped += 1
                active[:] = False
                break
            levels = unpack(np.fromfile(path, dtype=np.uint8), docs, width)
            contribution = lut[block * width:(block + 1) * width][
                np.arange(width)[:, None], levels.T].sum(axis=0)
            partial[active] += contribution[active]
            payload_bytes += int(path.stat().st_size)
            blocks_read += 1
            can_prune = (ids >= warmup) & active
            active[can_prune] &= partial[can_prune] <= best_scores[-1]
        if active.any():
            best_scores, best_ids = topk_merge(
                best_scores, best_ids, partial[active], ids[active], k)
    result = {
        "logical_payload_bytes_read": payload_bytes,
        "summary_bytes_read": summary_bytes,
        "tiles_skipped_by_block_min": tiles_skipped,
        "blocks_read": blocks_read,
        "blocks_skipped_by_block_min": blocks_skipped,
        "top_ids": best_ids.tolist(),
        "top256_sha256": ids_sha256(best_ids),
    }
    if check_parity:
        exhaustive = scan(layout, summaries, query, thresholds, n + 1, k, False)
        result["exact_top256_parity"] = bool(
            np.array_equal(best_ids, np.asarray(exhaustive["top_ids"])))
        if not result["exact_top256_parity"]:
            raise AssertionError("Block-Min page-skipping parity failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout-manifest", type=Path, required=True)
    parser.add_argument("--summary-manifest", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--warmup", type=int, default=4096)
    parser.add_argument("--check-parity", action="store_true")
    args = parser.parse_args()
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    summaries = json.loads(args.summary_manifest.read_text(encoding="utf-8"))
    if summaries["source_layout_manifest_sha256"] != sha256(args.layout_manifest):
        raise ValueError("summary/layout binding mismatch")
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    q = min(int(manifest["queries"]), args.query_limit)
    refs, outputs = manifest["references"], manifest["outputs"]
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4",
                        shape=(q, int(manifest["dimension"])))
    thresholds = np.memmap(outputs["thq4_thresholds"]["path"], mode="r",
                           dtype="<f4", shape=(int(manifest["dimension"]), 3))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8",
                         shape=(q, 10))
    rows = []
    for qi in range(q):
        row = scan(layout, summaries, np.asarray(queries[qi]),
                   np.asarray(thresholds), args.warmup, 256,
                   args.check_parity)
        row.update({"query": qi, "teacher_survival_256": float(
            np.isin(teachers[qi], row["top_ids"]).sum()) / 10.0})
        row.pop("top_ids", None)
        rows.append(row)
    result = {"schema_version": 1, "family": "thq_block_min_oracle_v1",
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "layout_manifest_sha256": sha256(args.layout_manifest),
              "summary_manifest_sha256": sha256(args.summary_manifest),
              "runner_sha256": sha256(Path(__file__)),
              "documents": int(manifest["documents"]), "queries": q,
              "rows": rows, "warmup": args.warmup,
              "execution_status": "EXECUTED_SMOKE" if q < 152 else "EXECUTED",
              "production_activation": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
