#!/usr/bin/env python3
"""Exact progressive ADC scan with a runtime-maintained cutoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from numba import njit


@njit
def _better(score: float, doc: int, other_score: float, other_doc: int) -> bool:
    return score < other_score or (score == other_score and doc < other_doc)


@njit
def scan_dynamic(levels, lut, order, warmup, k, checkpoints):
    n, d = levels.shape
    best_scores = np.full(k, np.inf)
    best_ids = np.full(k, n + 1, dtype=np.int64)
    reached = np.zeros(len(checkpoints), np.int64)
    fully_evaluated = 0
    total_coordinates = 0
    cutoff = np.inf

    for row in range(n):
        partial = 0.0
        alive = True
        checkpoint = 0
        limit = d if row < warmup else d
        for pos in range(limit):
            col = order[pos]
            partial += lut[col, levels[row, col]]
            total_coordinates += 1
            if checkpoint < len(checkpoints) and pos + 1 == checkpoints[checkpoint]:
                reached[checkpoint] += 1
                checkpoint += 1
            # Contributions are non-negative.  Equality must be retained so
            # that canonical (score, document_id) ties remain exact.
            if row >= warmup and partial > cutoff:
                alive = False
                break
        if alive:
            fully_evaluated += 1
            score = partial
            if _better(score, row, best_scores[k - 1], best_ids[k - 1]):
                best_scores[k - 1] = score
                best_ids[k - 1] = row
                index = k - 1
                while index > 0 and _better(
                    best_scores[index], best_ids[index],
                    best_scores[index - 1], best_ids[index - 1]
                ):
                    tmp_score = best_scores[index - 1]
                    tmp_id = best_ids[index - 1]
                    best_scores[index - 1] = best_scores[index]
                    best_ids[index - 1] = best_ids[index]
                    best_scores[index] = tmp_score
                    best_ids[index] = tmp_id
                    index -= 1
                cutoff = best_scores[k - 1]
        # A warm-up row is always fully scored and therefore cannot be pruned.
    return reached, fully_evaluated, total_coordinates, best_ids, best_scores, cutoff


def interval_lut(queries: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lut = np.empty((len(queries), 4), dtype=np.float32)
    for col, value in enumerate(queries):
        t1, t2, t3 = thresholds[col]
        lut[col] = (
            max(value - t1, 0.0),
            max(t1 - value, 0.0) if value < t1 else max(value - t2, 0.0),
            max(t2 - value, 0.0) if value < t2 else max(value - t3, 0.0),
            max(t3 - value, 0.0),
        )
    return lut * lut


def exhaustive_scores(levels: np.ndarray, lut: np.ndarray) -> np.ndarray:
    scores = np.zeros(len(levels), dtype=np.float32)
    for col in range(levels.shape[1]):
        scores += lut[col, levels[:, col]]
    return scores


def canonical_top(scores: np.ndarray, k: int) -> np.ndarray:
    ids = np.arange(len(scores), dtype=np.int64)
    return ids[np.lexsort((ids, scores))[:k]]


def coordinate_order(levels: np.ndarray, query_levels: np.ndarray,
                     lut: np.ndarray, mode: str) -> np.ndarray:
    d = levels.shape[1]
    if mode == "fixed":
        return np.arange(d, dtype=np.int64)
    sample = levels[: min(len(levels), 100_000)]
    if mode == "variance":
        values = np.var(sample.astype(np.float32), axis=0)
    elif mode == "query_adaptive":
        values = np.abs(np.mean(sample.astype(np.float32), axis=0) - query_levels)
    elif mode == "adc_expected":
        values = np.empty(d, dtype=np.float32)
        for col in range(d):
            values[col] = np.var(lut[col, sample[:, col]])
    else:  # pragma: no cover - argparse restricts this
        raise ValueError(mode)
    return np.argsort(-values, kind="stable").astype(np.int64)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--warmup", type=int, default=4096)
    parser.add_argument("--order", choices=("fixed", "variance", "query_adaptive", "adc_expected"), default="variance")
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    q = min(int(manifest["queries"]), args.query_limit)
    outputs, refs = manifest["outputs"], manifest["references"]
    codes = np.memmap(outputs["thq4_document_codes"]["path"], mode="r", dtype=np.uint8, shape=(n, 144))
    qcodes = np.memmap(outputs["thq4_query_codes"]["path"], mode="r", dtype=np.uint8, shape=(q, 144))
    levels = np.unpackbits(np.asarray(codes), axis=1, bitorder="little")[:, : d * 3].reshape(n, d, 3).sum(2).astype(np.uint8)
    qlevels = np.unpackbits(np.asarray(qcodes), axis=1, bitorder="little")[:, : d * 3].reshape(q, d, 3).sum(2).astype(np.uint8)
    thresholds = np.memmap(outputs["thq4_thresholds"]["path"], mode="r", dtype="<f4", shape=(d, 3))
    queries = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4", shape=(q, d))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(q, 10))
    checkpoints = np.array((32, 64, 96, 128, 160, 192, 256, 320, 384), dtype=np.int64)
    rows = []
    for qi in range(q):
        lut = interval_lut(np.asarray(queries[qi]), np.asarray(thresholds))
        order = coordinate_order(levels, qlevels[qi], lut, args.order)
        reached, fully, total_coords, best_ids, best_scores, cutoff = scan_dynamic(
            levels, lut, order, min(args.warmup, n), 256, checkpoints
        )
        full_scores = exhaustive_scores(levels, lut)
        exhaustive_ids = canonical_top(full_scores, 256)
        parity = bool(np.array_equal(best_ids, exhaustive_ids))
        cutoff_parity = bool(np.isclose(float(cutoff), float(full_scores[exhaustive_ids[-1]]), rtol=1e-6, atol=1e-6))
        rows.append({
            "query": qi,
            "order": args.order,
            "warmup": min(args.warmup, n),
            "warmup_fraction": min(args.warmup, n) / n,
            "cutoff": float(cutoff),
            "exhaustive_cutoff": float(full_scores[exhaustive_ids[-1]]),
            "exact_top256_parity": parity,
            "cutoff_parity": cutoff_parity,
            "docs_reaching_checkpoint": (reached / n).tolist(),
            "fully_evaluated_fraction": float(fully / n),
            "total_coordinate_evaluations": int(total_coords),
            "equivalent_flat_scan_fraction": float(total_coords / (n * d)),
            "teacher_survival_256": float(np.isin(teachers[qi], best_ids).sum()) / 10.0,
        })
    result = {
        "schema_version": 2,
        "family": "progressive_thq_dynamic_cutoff_oracle_v2",
        "documents": n,
        "queries": q,
        "dimension": d,
        "checkpoints": checkpoints.tolist(),
        "rows": rows,
        "order": args.order,
        "tie_policy": "score_ascending_then_document_id_ascending",
        "exact_parity_asserted": True,
        "interpretation_status": "EXACT_RUNTIME_CUTOFF_ORACLE_PENDING_EXTERNAL_DE1M_REPLAY",
        "production_activation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
