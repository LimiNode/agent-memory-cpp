#!/usr/bin/env python3
"""Benchmark precomputed PCA/E5 centroids for frozen PCA12 buckets."""

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


def load_shared() -> Any:
    path = Path(__file__).with_name("run-shared-supervised-ordinal-projection.py")
    spec = importlib.util.spec_from_file_location("shared_ordinal", path)
    require(spec is not None and spec.loader is not None, "helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()


def load_cache(path: Path) -> dict[str, np.ndarray]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_ordinal_lattice_input", "cache family differs")
    root = path.parent
    result = {name: np.load(root / row["path"], mmap_mode="r", allow_pickle=False)
              for name, row in manifest["outputs"].items()}
    source = manifest["source"]
    result["documents"] = np.memmap(Path(source["document_vectors"]), mode="r",
                                     dtype="<f4", shape=(int(source["document_count"]),
                                                           int(source["dimension"])))
    return result


def frozen_partition(documents: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                                       np.ndarray, np.ndarray]:
    mean, projection = shared.pca_init(documents[::4], 12)
    projected = (documents - mean) @ projection.T
    cuts = np.median(projected[::4], axis=0).astype(np.float32)
    bits = projected > cuts
    powers = np.left_shift(np.uint16(1), np.arange(12, dtype=np.uint16))
    cells = (bits.astype(np.uint16) * powers).sum(axis=1, dtype=np.uint16)
    return mean, projection, cuts, projected, cells


def states_12() -> np.ndarray:
    values = np.arange(4096, dtype=np.uint16)
    return ((values[:, None] >> np.arange(12, dtype=np.uint16)) & 1).astype(np.uint8)


def threshold_order(point: np.ndarray, cuts: np.ndarray, states: np.ndarray) -> np.ndarray:
    primary = point > cuts
    costs = (states != primary[None, :]) @ np.abs(point - cuts).astype(np.float32)
    return np.lexsort((np.arange(4096, dtype=np.int32), costs)).astype(np.int32)


def build_postings(cells: np.ndarray) -> dict[int, np.ndarray]:
    result: dict[int, list[int]] = {}
    for doc, cell in enumerate(cells):
        result.setdefault(int(cell), []).append(doc)
    return {cell: np.asarray(ids, dtype=np.int32) for cell, ids in result.items()}


def kmeans_bucket(ids: np.ndarray, projected: np.ndarray, count: int,
                  iterations: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Small deterministic Lloyd clustering in the 12D PCA coordinates."""
    points = projected[ids]
    count = min(count, len(points))
    if count == 1:
        return points.mean(axis=0, keepdims=True), np.zeros(len(points), dtype=np.int32)
    axis = int(np.argmax(np.var(points, axis=0)))
    seeds = np.quantile(points[:, axis], np.linspace(0.0, 1.0, count),
                        method="linear").astype(np.float32)
    centers = np.repeat(points.mean(axis=0, keepdims=True), count, axis=0)
    centers[:, axis] = seeds
    labels = np.zeros(len(points), dtype=np.int32)
    for _ in range(iterations):
        distances = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = np.argmin(distances, axis=1).astype(np.int32)
        for cluster in range(count):
            members = points[labels == cluster]
            if len(members):
                centers[cluster] = members.mean(axis=0)
    return centers.astype(np.float32), labels


def make_centroids(documents: np.ndarray, projected: np.ndarray,
                   cells: np.ndarray, postings: dict[int, np.ndarray],
                   count: int) -> tuple[np.ndarray, np.ndarray]:
    pca_centers: list[np.ndarray] = []
    e5_centers: list[np.ndarray] = []
    for cell in range(4096):
        ids = postings.get(cell)
        if ids is None:
            pca_centers.extend([np.zeros(12, dtype=np.float32)] * count)
            e5_centers.extend([np.zeros(documents.shape[1], dtype=np.float32)] * count)
            continue
        centers, labels = kmeans_bucket(ids, projected, count)
        actual = len(centers)
        for cluster in range(count):
            if cluster < actual:
                members = ids[labels == cluster]
                if len(members) == 0:
                    # Lloyd can leave a tiny bucket cluster empty.  Use the
                    # nearest point as a deterministic singleton rather than
                    # emitting NaN centroids.
                    bucket_points = projected[ids]
                    nearest = int(np.argmin(((bucket_points - centers[cluster]) ** 2).sum(axis=1)))
                    members = ids[nearest:nearest + 1]
                pca_centers.append(centers[cluster])
                e5_centers.append(np.asarray(documents[members], dtype=np.float32).mean(axis=0))
            else:
                pca_centers.append(centers[-1])
                e5_centers.append(e5_centers[-1])
    return np.asarray(pca_centers, dtype=np.float32).reshape(4096, count, 12), \
        np.asarray(e5_centers, dtype=np.float32).reshape(4096, count, documents.shape[1])


def fill_cells(order: np.ndarray, postings: dict[int, np.ndarray], budget: int) -> tuple[list[int], int]:
    selected: list[int] = []
    seen: set[int] = set()
    used = 0
    proposals = 0
    for value in order:
        cell = int(value)
        proposals += 1
        if cell in seen:
            continue
        seen.add(cell)
        size = len(postings.get(cell, ()))
        if size == 0 or used + size > budget:
            continue
        selected.append(cell)
        used += size
        if used == budget:
            break
    return selected, proposals


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("policy", "candidate_budget", "partition")
    output = []
    for key in sorted({tuple(row[name] for name in keys) for row in rows}):
        current = [row for row in rows if tuple(row[name] for name in keys) == key]
        output.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "mean_cells_opened": float(np.mean([r["cells_opened"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "model_bytes": int(max(r["model_bytes"] for r in current)),
                       "query_ops": int(max(r["query_ops"] for r in current))})
    return output


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    teacher_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = frozen_partition(documents)
    postings = build_postings(document_cells)
    states = states_12()
    all_scores = documents @ queries.T
    models: dict[str, tuple[np.ndarray, np.ndarray, int, int]] = {}
    for count in args.centroids:
        pca, e5 = make_centroids(documents, projected, document_cells, postings, count)
        models[f"pca_centroid_k{count}"] = (pca, e5, int(pca.nbytes), 4096 * count * 12)
        models[f"e5_centroid_k{count}"] = (pca, e5, int(e5.nbytes), 4096 * count * 384)
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        point = (query - mean) @ projection.T
        base_order = threshold_order(point, cuts, states)
        teacher = set(map(int, teacher_ids[index]))
        teacher_cells = {int(document_cells[doc]) for doc in teacher}
        for policy, (pca_centers, e5_centers, storage_bytes, ops) in models.items():
            if policy.startswith("pca_"):
                distances = ((pca_centers - point[None, None, :]) ** 2).sum(axis=2)
                scores = -distances.min(axis=1)
            else:
                scores = np.max(e5_centers @ query, axis=1)
            centroid_order = np.lexsort((np.arange(4096, dtype=np.int32), -scores)).astype(np.int32)
            for budget in args.budgets:
                started = time.perf_counter()
                cells, proposals = fill_cells(centroid_order, postings, budget)
                parts = [postings[cell] for cell in cells]
                candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                selected = candidates[shared.ordinal.top(all_scores[candidates, index], 10)]
                rows.append({"policy": policy, "partition": str(partitions[index]),
                             "query": index, "candidate_budget": budget,
                             "overlap": float(len(set(map(int, selected)) & teacher)) / 10.0,
                             "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                             "unique_candidates": int(len(candidates)),
                             "raw_postings": int(sum(len(part) for part in parts)),
                             "cells_opened": len(cells), "proposals": proposals,
                             "teacher_cells_recovered": len(set(cells) & teacher_cells),
                             "model_bytes": storage_bytes, "query_ops": ops,
                             "route_ms": (time.perf_counter() - started) * 1000.0})
        # The original PCA threshold scheduler is retained as a direct control.
        for budget in args.budgets:
            started = time.perf_counter()
            cells, proposals = fill_cells(base_order, postings, budget)
            parts = [postings[cell] for cell in cells]
            candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
            selected = candidates[shared.ordinal.top(all_scores[candidates, index], 10)]
            rows.append({"policy": "pca_threshold_scheduler", "partition": str(partitions[index]),
                         "query": index, "candidate_budget": budget,
                         "overlap": float(len(set(map(int, selected)) & teacher)) / 10.0,
                         "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                         "unique_candidates": int(len(candidates)),
                         "raw_postings": int(sum(len(part) for part in parts)),
                         "cells_opened": len(cells), "proposals": proposals,
                         "teacher_cells_recovered": len(set(cells) & teacher_cells),
                         "model_bytes": int(mean.nbytes + projection.nbytes + cuts.nbytes),
                         "query_ops": 12 * 384 + 4096 * 12,
                         "route_ms": (time.perf_counter() - started) * 1000.0})
    return {"schema_version": 1, "family": "pca12_bucket_centroid_bakeoff",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"partition": "pca12_documents_stride4_svd_median",
                          "centroid_counts": args.centroids,
                          "candidate_budgets": args.budgets,
                          "centroid_training": "deterministic_5_iter_bucket_kmeans_in_pca12",
                          "final_scoring": "exact_e5_fp32"},
            "product_claim": False,
            "limitations": ["single DE-1M split", "Python timing is directional",
                             "centroids are frozen diagnostic controls",
                             "subcentroid clustering is a small deterministic Lloyd fit"]}


def self_test() -> None:
    require(states_12().shape == (4096, 12), "state enumeration differs")
    points = np.asarray([[0., 0.], [0., 1.], [9., 9.]], dtype=np.float32)
    centers, labels = kmeans_bucket(np.arange(3), points, 2)
    require(centers.shape == (2, 2) and labels.shape == (3,), "kmeans smoke failed")
    print("PCA12 bucket centroid self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--centroids", default="1,2,4,8")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None,
            "--cache and --output are required unless --self-test is used")
    args.budgets = [int(value) for value in args.candidate_budgets.split(",")]
    args.centroids = [int(value) for value in args.centroids.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
