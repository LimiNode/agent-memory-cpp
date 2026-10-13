#!/usr/bin/env python3
"""Evaluate centroid-prior residual heads and Hungarian set anchors."""

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


def cell_costs(point: np.ndarray, cuts: np.ndarray, states: np.ndarray) -> np.ndarray:
    primary = point > cuts
    return (states != primary[None, :]) @ np.abs(point - cuts).astype(np.float32)


def train_set_model(queries: np.ndarray, targets: list[np.ndarray], seed: int,
                    epochs: int, anchors: int) -> dict[str, np.ndarray]:
    import torch
    from scipy.optimize import linear_sum_assignment

    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    q = torch.from_numpy(np.asarray(queries, dtype=np.float32))
    model = torch.nn.Sequential(torch.nn.Linear(q.shape[1], 128),
                                torch.nn.GELU(approximate="tanh"),
                                torch.nn.Linear(128, anchors * 12))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=1.0e-4)
    target = [torch.from_numpy(x.astype(np.float32)) for x in targets]
    for _ in range(epochs):
        pred = model(q).reshape(len(q), anchors, 12)
        losses = []
        for row in range(len(q)):
            cost = torch.cdist(pred[row], target[row]) ** 2
            # Hungarian assignment is evaluated on detached costs only for the
            # permutation; gradients flow through the selected pairs.
            rr, cc = linear_sum_assignment(cost.detach().numpy())
            losses.append(cost[torch.from_numpy(rr), torch.from_numpy(cc)].mean())
        loss = torch.stack(losses).mean()
        diversity = torch.cdist(pred, pred).mean()
        loss = loss - 0.01 * diversity
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
    return {"weight1": model[0].weight.detach().numpy().astype(np.float32),
            "bias1": model[0].bias.detach().numpy().astype(np.float32),
            "weight2": model[2].weight.detach().numpy().astype(np.float32),
            "bias2": model[2].bias.detach().numpy().astype(np.float32)}


def infer_set(q: np.ndarray, artifact: dict[str, np.ndarray], anchors: int) -> np.ndarray:
    hidden = q @ artifact["weight1"].T + artifact["bias1"]
    hidden = 0.5 * hidden * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) *
                                           (hidden + 0.044715 * hidden ** 3)))
    return (hidden @ artifact["weight2"].T + artifact["bias2"]).reshape(-1, anchors, 12)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("policy", "candidate_budget", "partition")
    out = []
    for key in sorted({tuple(r[k] for k in keys) for r in rows}):
        cur = [r for r in rows if tuple(r[k] for k in keys) == key]
        out.append({**dict(zip(keys, key)), "query_count": len(cur),
                    "mean_overlap": float(np.mean([r["overlap"] for r in cur])),
                    "p05_overlap": float(np.quantile([r["overlap"] for r in cur], .05)),
                    "worst_overlap": float(np.min([r["overlap"] for r in cur])),
                    "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in cur])),
                    "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in cur])),
                    "p95_route_ms": float(np.quantile([r["route_ms"] for r in cur], .95)),
                    "model_bytes": int(max(r["model_bytes"] for r in cur))})
    return out


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = centroid.load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    tr_q = np.asarray(data["train_queries"], dtype=np.float32)
    tr_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    teacher_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = centroid.frozen_partition(documents)
    postings = centroid.build_postings(document_cells)
    states = centroid.states_12()
    pca_centers, _ = centroid.make_centroids(documents, projected, document_cells, postings, 1)
    pca_centers = pca_centers[:, 0]
    train_targets = [projected[tr_ids[row][:args.anchors]] for row in range(len(tr_q))]
    artifacts = {seed: train_set_model(tr_q - mean, train_targets, seed, args.epochs, args.anchors)
                 for seed in args.seeds}
    rows: list[dict[str, Any]] = []
    all_scores = documents @ queries.T
    for index, query in enumerate(queries):
        point = (query - mean) @ projection.T
        threshold = centroid.threshold_order(point, cuts, states)
        teacher = set(map(int, teacher_ids[index]))
        teacher_cells = {int(document_cells[d]) for d in teacher}
        base = -((pca_centers - point[None, :]) ** 2).sum(axis=1)
        for alpha in args.alphas:
            for seed, artifact in artifacts.items():
                # Hybrid uses a fixed centroid prior plus a learned score
                # residual.  To keep this study focused, residual logits are
                # initialized from the set-model's anchor distances below.
                anchors = infer_set((queries[index:index + 1] - mean), artifact, args.anchors)[0]
                residual = np.full(4096, -1.0e6, dtype=np.float32)
                for anchor in anchors:
                    cost = cell_costs(anchor, cuts, states)
                    residual = np.maximum(residual, -cost)
                hybrid = base + alpha * residual
                order = np.lexsort((np.arange(4096, dtype=np.int32), -hybrid)).astype(np.int32)
                for budget in args.budgets:
                    started = time.perf_counter()
                    cells, proposals = centroid.fill_cells(order, postings, budget)
                    parts = [postings[cell] for cell in cells]
                    candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                    selected = candidates[shared.ordinal.top(all_scores[candidates, index], 10)]
                    rows.append({"policy": f"set_hybrid_a{alpha:g}", "seed": seed,
                                 "partition": str(partitions[index]), "query": index,
                                 "candidate_budget": budget,
                                 "overlap": float(len(set(map(int, selected)) & teacher)) / 10.0,
                                 "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                                 "unique_candidates": int(len(candidates)),
                                 "raw_postings": int(sum(len(part) for part in parts)),
                                 "cells_opened": len(cells), "proposals": proposals,
                                 "teacher_cells_recovered": len(set(cells) & teacher_cells),
                                 "model_bytes": int(sum(v.nbytes for v in artifact.values())),
                                 "route_ms": (time.perf_counter() - started) * 1000.0})
    return {"schema_version": 1, "family": "hybrid_centroid_hungarian_set",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"anchors": args.anchors, "alphas": args.alphas,
                          "seeds": args.seeds, "epochs": args.epochs,
                          "candidate_budgets": args.budgets,
                          "set_targets": "first teacher top-M projected documents",
                          "final_scoring": "exact_e5_fp32"}, "product_claim": False,
            "limitations": ["single DE-1M split and 153 train queries",
                             "hybrid residual is set-anchor distance prior, not a trained cell residual",
                             "Python timing directional", "routing ceiling only"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--anchors", type=int, default=8)
    parser.add_argument("--alphas", default="0.1,0.25,0.5,1.0")
    parser.add_argument("--seeds", default="13,37,101")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    args = parser.parse_args()
    args.alphas = [float(v) for v in args.alphas.split(",")]
    args.seeds = [int(v) for v in args.seeds.split(",")]
    args.budgets = [int(v) for v in args.candidate_budgets.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
