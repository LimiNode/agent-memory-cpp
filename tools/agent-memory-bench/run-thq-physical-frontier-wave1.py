#!/usr/bin/env python3
"""Run the first THQ physical-frontier controls on the frozen fixture.

This runner deliberately keeps the four questions separate: Block-Min
metadata, a deterministic code-order surrogate, tile bitmap/range selection,
and a coarse-tile -> exact THQ-ADC cascade.  The code-order surrogate is not
called R4; it is only a reproducible physical-locality control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lut_for(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    out = np.empty((query.size, 4), dtype=np.float32)
    for i, value in enumerate(query):
        t1, t2, t3 = thresholds[i]
        d = np.asarray((max(value - t1, 0.0),
                        max(t1 - value, 0.0) if value < t1 else max(value - t2, 0.0),
                        max(t2 - value, 0.0) if value < t2 else max(value - t3, 0.0),
                        max(t3 - value, 0.0)), dtype=np.float32)
        out[i] = d * d
    return out


def unpack(raw: np.ndarray, docs: int, width: int) -> np.ndarray:
    packed = raw.reshape(docs, (width + 3) // 4)
    levels = np.empty((docs, width), dtype=np.uint8)
    for c in range(width):
        levels[:, c] = (packed[:, c // 4] >> (2 * (c % 4))) & 3
    return levels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout-manifest", type=Path, required=True)
    ap.add_argument("--summary-manifest", type=Path, required=True)
    ap.add_argument("--thq-manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--query-limit", type=int, default=8)
    ap.add_argument("--budgets", default="1000,5000,20000,50000")
    args = ap.parse_args()
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    summary = json.loads(args.summary_manifest.read_text(encoding="utf-8"))
    if summary["source_layout_manifest_sha256"] != sha256(args.layout_manifest):
        raise ValueError("summary/layout binding mismatch")
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    qn = min(args.query_limit, int(manifest["queries"]))
    dim = int(manifest["dimension"])
    tile_docs = int(layout["tile_docs"])
    rows = {(int(r["tile"]), int(r["block"])): r for r in layout["blocks"]}
    tile_ids = sorted({t for t, _ in rows})
    width = int(layout["coords_per_block"])
    summaries = {}
    for tr in summary["tiles"]:
        summaries[int(tr["tile"])] = np.fromfile(Path(tr["path"]), dtype=np.uint8).reshape(
            int(tr["blocks"]), width)
    queries = np.memmap(manifest["references"]["queries"]["path"], mode="r", dtype="<f4",
                        shape=(qn, dim))
    thresholds = np.memmap(manifest["outputs"]["thq4_thresholds"]["path"], mode="r",
                           dtype="<f4", shape=(dim, 3))
    teachers = np.memmap(manifest["references"]["teacher_ids"]["path"], mode="r",
                         dtype="<i8", shape=(qn, 10))
    # Deterministic physical-locality surrogate: sort by the first eight THQ
    # bytes.  It is intentionally query-independent and is not an R4 mapping.
    code_info = manifest["outputs"]["thq4_document_codes"]
    code_bytes = np.memmap(code_info["path"], mode="r", dtype=np.uint8,
                           shape=(int(manifest["documents"]), int(code_info["shape"][1])))
    prefix = np.asarray(code_bytes[:, :8], dtype=np.uint64)
    prefix = prefix[:, 0] | (prefix[:, 1] << 8) | (prefix[:, 2] << 16) | (prefix[:, 3] << 24) | (prefix[:, 4] << 32) | (prefix[:, 5] << 40) | (prefix[:, 6] << 48) | (prefix[:, 7] << 56)
    reorder = np.argsort(prefix, kind="stable")
    reordered_pos = np.empty_like(reorder)
    reordered_pos[reorder] = np.arange(reorder.size)
    budgets = [int(x) for x in args.budgets.split(",")]
    rows_out = []
    for qi in range(qn):
        lut = lut_for(np.asarray(queries[qi]), np.asarray(thresholds))
        tile_bounds = []
        for tile in tile_ids:
            masks = summaries[tile]
            b = 0.0
            for bi in range(masks.shape[0]):
                present = masks[bi]
                for local in range(width):
                    levels = [l for l in range(4) if (int(present[local]) >> l) & 1]
                    b += float(np.min(lut[bi * width + local, levels]))
            tile_bounds.append((b, tile))
        tile_bounds.sort()
        budget_rows = []
        for budget in budgets:
            take = max(1, min(len(tile_ids), (budget + tile_docs - 1) // tile_docs))
            selected = [t for _, t in tile_bounds[:take]]
            candidate_ids = np.concatenate([
                np.arange(int(rows[(t, 0)]["document_start"]),
                          int(rows[(t, 0)]["document_start"]) + int(rows[(t, 0)]["document_count"]))
                for t in selected])
            teacher_recall = float(np.isin(teachers[qi], candidate_ids).sum()) / 10.0
            # Exact rerank inside selected tiles (cascade stage).
            scores = []
            ids = []
            for t in selected:
                doc_count = int(rows[(t, 0)]["document_count"])
                partial = np.zeros(doc_count, dtype=np.float32)
                for bi in range(len([k for k in rows if k[0] == t])):
                    r = rows[(t, bi)]
                    levels = unpack(np.fromfile(Path(r["path"]), dtype=np.uint8),
                                    doc_count, width)
                    partial += lut[bi * width:(bi + 1) * width][
                        np.arange(width)[:, None], levels.T].sum(axis=0)
                scores.append(partial)
                ids.append(candidate_ids[np.sum([int(rows[(u, 0)]["document_count"]) for u in selected[:selected.index(t)]], dtype=np.int64):
                                             np.sum([int(rows[(u, 0)]["document_count"]) for u in selected[:selected.index(t) + 1]], dtype=np.int64)])
            all_scores = np.concatenate(scores)
            all_ids = np.concatenate(ids)
            order = np.lexsort((all_ids, all_scores))[:256]
            rerank_ids = all_ids[order]
            budget_rows.append({"budget_docs": budget, "tiles_selected": take,
                                "candidate_docs": int(candidate_ids.size),
                                "teacher_recall": teacher_recall,
                                "cascade_teacher_survival_256": float(np.isin(teachers[qi], rerank_ids).sum()) / 10.0})
        # Physical locality control: a deterministic key from code bytes.  It
        # is intentionally not labelled R4 or semantic truth.
        original_tiles = np.unique(teachers[qi] // tile_docs).size
        surrogate_tiles = np.unique(reordered_pos[np.asarray(teachers[qi])] // tile_docs).size
        rows_out.append({"query": qi, "block_min_tile_skips": 0,
                         "bitmap_range": budget_rows,
                         "document_id_teacher_tile_span": int(original_tiles),
                         "code_prefix_control_teacher_tile_span": int(surrogate_tiles),
                         "code_prefix_control": "packed-code-prefix-order; not R4"})
    result = {"schema_version": 1, "family": "thq_physical_frontier_wave1_v1",
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "layout_manifest_sha256": sha256(args.layout_manifest),
              "summary_manifest_sha256": sha256(args.summary_manifest),
              "runner_sha256": sha256(Path(__file__)), "queries": qn,
              "rows": rows_out, "execution_status": "EXECUTED_SMOKE" if qn < 152 else "EXECUTED",
              "physical_bytes_semantics": "logical payload bytes; no OS/MDBX page counters",
              "production_activation": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
