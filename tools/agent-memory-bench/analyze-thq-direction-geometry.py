#!/usr/bin/env python3
"""Geometry oracle for signed/ordinal THQ transition concentration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    m = json.loads(args.manifest.read_text(encoding="utf-8")); out = m["outputs"]; refs = m["references"]
    n, q, d = int(m["documents"]), int(m["queries"]), int(m["dimension"])
    docs = np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4", shape=(n, d))
    queries = np.fromfile(refs["queries"]["path"], dtype="<f4").reshape(q, d)
    teacher = np.fromfile(refs["teacher_ids"]["path"], dtype="<i8").reshape(q, 10)
    thresholds = np.fromfile(out["thq4_thresholds"]["path"], dtype="<f4").reshape(d, 3)
    # A THQ4 level is the number of crossed thresholds (0..3).
    query_levels = (queries[:, :, None] > thresholds[None, :, :]).sum(axis=2).astype(np.int8)
    levels = (np.asarray(docs[teacher.reshape(-1)], dtype=np.float32)[:, :, None] > thresholds[None, :, :]).sum(axis=2).astype(np.int8).reshape(q, 10, d)
    delta = levels - query_levels[:, None, :]
    margins = np.abs(queries[:, :, None] - thresholds[None, :, :]) / np.maximum(np.ptp(thresholds, axis=1)[None, :, None], 1e-8)
    # Query-only baseline: coordinates nearest to any threshold first.
    uncertainty = margins.min(axis=2)
    widths = [8, 16, 24, 32, 48, 64, 96, 128, 192, 384]
    rows = []
    for qi in range(q):
        for rank in range(10):
            changed = np.flatnonzero(delta[qi, rank] != 0)
            if changed.size == 0:
                continue
            direction = np.abs(docs[int(teacher[qi, rank])] - queries[qi])
            directional_order = np.argsort(-direction, kind="stable")
            uncertainty_order = np.argsort(uncertainty[qi], kind="stable")
            for width in widths:
                rows.append({"query": qi, "rank": rank, "width": width,
                             "changed_coordinates": int(changed.size),
                             "direction_capture": float(np.isin(changed, directional_order[:width]).sum() / changed.size),
                             "uncertainty_capture": float(np.isin(changed, uncertainty_order[:width]).sum() / changed.size),
                             "max_transition": int(np.max(np.abs(delta[qi, rank])))})
    # Aggregate teacher transition concentration and per-query worst relevant row.
    result_rows = []
    for width in widths:
        subset = [row for row in rows if row["width"] == width]
        result_rows.append({"width": width,
                            "mean_direction_capture": float(np.mean([x["direction_capture"] for x in subset])),
                            "p05_direction_capture": float(np.percentile([x["direction_capture"] for x in subset], 5)),
                            "mean_uncertainty_capture": float(np.mean([x["uncertainty_capture"] for x in subset])),
                            "p05_uncertainty_capture": float(np.percentile([x["uncertainty_capture"] for x in subset], 5)),
                            "fraction_all_10_captured": float(np.mean([x["direction_capture"] >= 1.0 for x in subset]))})
    result = {"schema_version": 1, "family": "thq_direction_geometry_oracle_v1",
              "manifest": str(args.manifest.resolve()), "queries": q, "documents": n,
              "thresholds": "THQ4 quantile", "widths": widths, "aggregate": result_rows,
              "transition_rows": rows}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"queries": q, "aggregate": result_rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
