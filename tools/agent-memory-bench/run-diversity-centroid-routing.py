#!/usr/bin/env python3
"""Measure diversity-aware seed selection over frozen bucket centroids."""

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


def load_module(name: str, filename: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


centroid = load_module("centroid", "run-pca12-bucket-centroid-bakeoff.py")
shared = centroid.shared


def mmr_order(scores: np.ndarray, representatives: np.ndarray, count: int,
              diversity: float) -> np.ndarray:
    """Select high-score cells while penalizing redundancy among top 256."""
    pool = np.argsort(-scores, kind="stable")[:min(256, len(scores))]
    vectors = representatives[pool]
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.maximum(norms, 1.0e-8)
    similarity = vectors @ vectors.T
    selected: list[int] = []
    remaining = set(range(len(pool)))
    for _ in range(min(count, len(pool))):
        best = max(remaining, key=lambda index: (
            float(scores[pool[index]]) - diversity * (max(
                (float(similarity[index, value]) for value in selected), default=0.0)),
            -int(pool[index])))
        selected.append(best)
        remaining.remove(best)
    return pool[np.asarray(selected, dtype=np.int32)]


def fill(order: np.ndarray, postings: dict[int, np.ndarray], budget: int,
         seeds: np.ndarray) -> tuple[list[int], int]:
    chosen: list[int] = []
    seen: set[int] = set()
    used = 0
    proposals = 0
    for value in list(map(int, seeds)) + list(map(int, order)):
        proposals += 1
        if value in seen:
            continue
        seen.add(value)
        size = len(postings.get(value, ()))
        if size == 0 or used + size > budget:
            continue
        chosen.append(value)
        used += size
        if used == budget:
            break
    return chosen, proposals


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
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    teacher_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = centroid.frozen_partition(documents)
    postings = centroid.build_postings(document_cells)
    states = centroid.states_12()
    pca, e5 = centroid.make_centroids(documents, projected, document_cells, postings, 1)
    all_scores = documents @ queries.T
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        point = (query - mean) @ projection.T
        order = centroid.threshold_order(point, cuts, states)
        teacher = set(map(int, teacher_ids[index]))
        for space, reps, storage, ops in (("pca", pca[:, 0], pca.nbytes, 4096 * 12),
                                           ("e5", e5[:, 0], e5.nbytes, 4096 * 384)):
            if space == "pca":
                scores = -((reps - point[None, :]) ** 2).sum(axis=1)
            else:
                scores = reps @ query
            ranked = np.lexsort((np.arange(4096, dtype=np.int32), -scores)).astype(np.int32)
            policies = [(f"{space}_centroid_top", ranked)]
            for seed_count in args.seed_counts:
                for diversity in args.diversities:
                    seeds = mmr_order(scores, reps, seed_count, diversity)
                    policies.append((f"{space}_mmr_s{seed_count}_d{diversity:g}",
                                     (seeds, ranked)))
            for policy, descriptor in policies:
                for budget in args.budgets:
                    started = time.perf_counter()
                    if isinstance(descriptor, tuple):
                        seeds, cell_order = descriptor
                    else:
                        seeds, cell_order = np.empty(0, dtype=np.int32), descriptor
                    cells, proposals = fill(cell_order, postings, budget, seeds)
                    parts = [postings[cell] for cell in cells]
                    candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                    selected = candidates[shared.ordinal.top(all_scores[candidates, index], 10)]
                    seed_set = set(map(int, seeds))
                    seed_precision = (len(seed_set & {int(document_cells[d]) for d in teacher}) /
                                      len(seed_set) if seed_set else 0.0)
                    rows.append({"policy": policy, "partition": str(partitions[index]),
                                 "query": index, "candidate_budget": budget,
                                 "overlap": float(len(set(map(int, selected)) & teacher)) / 10.0,
                                 "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                                 "unique_candidates": int(len(candidates)),
                                 "raw_postings": int(sum(len(part) for part in parts)),
                                 "cells_opened": len(cells), "proposals": proposals,
                                 "seed_precision": seed_precision,
                                 "model_bytes": int(storage), "query_ops": int(ops),
                                 "route_ms": (time.perf_counter() - started) * 1000.0})
    return {"schema_version": 1, "family": "pca12_diversity_centroid_routing",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"partition": "pca12_documents_stride4_svd_median",
                          "seed_counts": args.seed_counts, "diversities": args.diversities,
                          "candidate_budgets": args.budgets, "final_scoring": "exact_e5_fp32"},
            "product_claim": False,
            "limitations": ["single DE-1M split", "MMR pool is top-256 centroid cells",
                             "Python timing is directional", "routing ceiling only"]}


def self_test() -> None:
    scores = np.asarray([3., 2., 1.], dtype=np.float32)
    reps = np.asarray([[1., 0.], [1., 0.], [0., 1.]], dtype=np.float32)
    selected = mmr_order(scores, reps, 2, .5)
    require(len(selected) == 2 and int(selected[0]) == 0, "MMR smoke failed")
    print("diversity centroid self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--seed-counts", default="4,8,16,32")
    parser.add_argument("--diversities", default="0.2,0.5,0.8")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None,
            "--cache and --output are required unless --self-test is used")
    args.budgets = [int(v) for v in args.candidate_budgets.split(",")]
    args.seed_counts = [int(v) for v in args.seed_counts.split(",")]
    args.diversities = [float(v) for v in args.diversities.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
