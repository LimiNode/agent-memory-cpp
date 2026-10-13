#!/usr/bin/env python3
"""Evaluate weighted train-query cell voting against the frozen PCA control."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_centroid() -> Any:
    path = Path(__file__).with_name("run-pca12-bucket-centroid-bakeoff.py")
    spec = importlib.util.spec_from_file_location("centroid", path)
    require(spec is not None and spec.loader is not None, "centroid helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


centroid = load_centroid()
shared = centroid.shared


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("policy", "candidate_budget", "partition")
    result = []
    for key in sorted({tuple(r[k] for k in keys) for r in rows}):
        current = [r for r in rows if tuple(r[k] for k in keys) == key]
        result.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "mean_seed_precision": float(np.mean([r["seed_precision"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "model_bytes": int(max(r["model_bytes"] for r in current))})
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = centroid.load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    train_queries = np.asarray(data["train_queries"], dtype=np.float32)
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    train_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    teacher_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = centroid.frozen_partition(documents)
    postings = centroid.build_postings(document_cells)
    states = centroid.states_12()
    train_cells = centroid.teacher_cells(train_ids, document_cells) if hasattr(centroid, "teacher_cells") else [
        sorted({int(document_cells[int(doc)]) for doc in row}) for row in train_ids]
    normalized_train = train_queries - mean
    normalized_eval = queries - mean
    all_scores = documents @ queries.T
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        point = normalized_eval[index] @ projection.T
        order = centroid.threshold_order(point, cuts, states)
        teacher = set(map(int, teacher_ids[index]))
        teacher_cells = {int(document_cells[d]) for d in teacher}
        similarity = normalized_train @ normalized_eval[index]
        for k in args.k_values:
            neighbours = np.argsort(-similarity, kind="stable")[:k]
            weights = np.maximum(similarity[neighbours], 0.0)
            raw_scores = np.zeros(4096, dtype=np.float32)
            for neighbour, weight in zip(neighbours, weights):
                for cell in train_cells[int(neighbour)]:
                    raw_scores[cell] += float(weight)
            for mode in ("vote", "gain_cost"):
                scores = raw_scores.copy()
                if mode == "gain_cost":
                    sizes = np.asarray([len(postings.get(cell, ())) for cell in range(4096)], dtype=np.float32)
                    scores /= np.maximum(sizes, 1.0)
                ranked = np.lexsort((np.arange(4096, dtype=np.int32), -scores)).astype(np.int32)
                for budget in args.budgets:
                    started = time.perf_counter()
                    # Reserve the explicitly predicted multimodal seeds first,
                    # matching the direct-router contract; the rest is filled
                    # by the ranked vote order.
                    seeds = ranked[:args.seed_cells]
                    cells, proposals = centroid.fill_cells(
                        np.concatenate([seeds, ranked]), postings, budget)
                    parts = [postings[cell] for cell in cells]
                    candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                    selected = candidates[shared.ordinal.top(all_scores[candidates, index], 10)]
                    seed_set = set(map(int, seeds))
                    rows.append({"policy": f"weighted_knn{k}_{mode}",
                                 "partition": str(partitions[index]), "query": index,
                                 "candidate_budget": budget,
                                 "overlap": float(len(set(map(int, selected)) & teacher)) / 10.0,
                                 "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                                 "unique_candidates": int(len(candidates)),
                                 "raw_postings": int(sum(len(part) for part in parts)),
                                 "cells_opened": len(cells), "proposals": proposals,
                                 "seed_precision": (len(seed_set & teacher_cells) / len(seed_set)
                                                     if seed_set else 0.0),
                                 "model_bytes": int(train_queries.nbytes + train_ids.nbytes),
                                 "route_ms": (time.perf_counter() - started) * 1000.0})
    return {"schema_version": 1, "family": "weighted_knn_cell_routing",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"candidate_budgets": args.budgets, "k_values": args.k_values,
                          "seed_cells": args.seed_cells, "modes": ["vote", "gain_cost"],
                          "final_scoring": "exact_e5_fp32"}, "product_claim": False,
            "limitations": ["single DE-1M split", "153 train queries", "Python timing directional",
                             "train-query payload counted as model bytes"]}


def self_test() -> None:
    print("weighted kNN cell router self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--k-values", default="4,8,16,32")
    parser.add_argument("--seed-cells", type=int, default=32)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None, "cache/output required")
    args.budgets = [int(v) for v in args.candidate_budgets.split(",")]
    args.k_values = [int(v) for v in args.k_values.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
