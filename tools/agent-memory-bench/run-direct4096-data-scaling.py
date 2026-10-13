#!/usr/bin/env python3
"""Learning curve for the supervised 4096-cell direct router."""

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


def load(name: str, filename: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


multi = load("multi", "run-multimodal-cell-router-bakeoff.py")
centroid = load("centroid", "run-pca12-bucket-centroid-bakeoff.py")
shared = multi.shared


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("train_count", "seed", "candidate_budget", "partition")
    result = []
    for key in sorted({tuple(r[k] for k in keys) for r in rows}):
        current = [r for r in rows if tuple(r[k] for k in keys) == key]
        result.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "model_bytes": int(max(r["model_bytes"] for r in current))})
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = centroid.load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    base_queries = np.asarray(data["train_queries"], dtype=np.float32)
    base_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    extra_queries = np.load(args.extra / "queries.npy", allow_pickle=False)
    extra_ids = np.load(args.extra / "teacher-ids.npy", allow_pickle=False)
    eval_queries = np.asarray(data["eval_queries"], dtype=np.float32)
    eval_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = centroid.frozen_partition(documents)
    postings = centroid.build_postings(document_cells)
    eval_cells = multi.teacher_cells(eval_ids, document_cells)
    all_scores = documents @ eval_queries.T
    rows: list[dict[str, Any]] = []
    for train_count in args.train_counts:
        require(train_count <= len(base_queries) + len(extra_queries), "train count exceeds cache")
        if train_count <= len(base_queries):
            train_queries = base_queries[:train_count]
            train_ids = base_ids[:train_count]
        else:
            extra_count = train_count - len(base_queries)
            train_queries = np.concatenate([base_queries, extra_queries[:extra_count]])
            train_ids = np.concatenate([base_ids, extra_ids[:extra_count]])
        train_cells = multi.teacher_cells(train_ids, document_cells)
        labels = train_cells
        for seed in args.seeds:
            artifact = multi.train_head(train_queries - mean, labels, 4096, seed, args.epochs)
            model_bytes = sum(value.nbytes for value in artifact.values())
            logits = multi.infer_head(eval_queries - mean, artifact)
            for index, query in enumerate(eval_queries):
                point = (query - mean) @ projection.T
                order = centroid.threshold_order(point, cuts, centroid.states_12())
                full_scores = all_scores[:, index]
                seed_cells = np.argsort(-logits[index], kind="stable")[:args.direct_cells].tolist()
                for budget in args.budgets:
                    started = time.perf_counter()
                    cells, proposals = multi.fill_cells(seed_cells, order, postings, budget)
                    parts = [postings[cell] for cell in cells]
                    candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                    selected = candidates[shared.ordinal.top(full_scores[candidates], 10)]
                    rows.append({"train_count": train_count, "seed": seed,
                                 "partition": str(partitions[index]), "query": index,
                                 "candidate_budget": budget,
                                 "overlap": float(len(set(map(int, selected)) & set(map(int, eval_ids[index])))) / 10.0,
                                 "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                                 "unique_candidates": int(len(candidates)),
                                 "raw_postings": int(sum(len(part) for part in parts)),
                                 "cells_opened": len(cells), "proposals": proposals,
                                 "model_bytes": model_bytes,
                                 "route_ms": (time.perf_counter() - started) * 1000.0})
    return {"schema_version": 1, "family": "direct4096_data_scaling",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"train_counts": args.train_counts, "seeds": args.seeds,
                          "direct_cells": args.direct_cells, "budgets": args.budgets,
                          "extra_cache": str(args.extra), "final_scoring": "exact_e5_fp32"},
            "product_claim": False,
            "limitations": ["extra queries are MIRACL vectors scored against DE-1M docs",
                             "single held-out split", "Python timing directional"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--extra", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-counts", default="153,500,1000")
    parser.add_argument("--seeds", default="13,37,101")
    parser.add_argument("--direct-cells", type=int, default=32)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--epochs", type=int, default=180)
    args = parser.parse_args()
    args.train_counts = [int(v) for v in args.train_counts.split(",")]
    args.seeds = [int(v) for v in args.seeds.split(",")]
    args.budgets = [int(v) for v in args.candidate_budgets.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
