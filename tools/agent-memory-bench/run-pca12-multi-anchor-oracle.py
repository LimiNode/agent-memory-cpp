#!/usr/bin/env python3
"""Measure multi-anchor and optimal-cell ceilings on frozen PCA12 routing."""

from __future__ import annotations

import argparse
import heapq
import importlib.util
import itertools
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_helper() -> Any:
    path = Path(__file__).with_name("run-shared-supervised-ordinal-projection.py")
    spec = importlib.util.spec_from_file_location("shared_ordinal", path)
    require(spec is not None and spec.loader is not None,
            "shared ordinal helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_helper()


def load_cache(path: Path) -> dict[str, np.ndarray]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_ordinal_lattice_input",
            "multi-anchor cache family differs")
    root = path.parent
    result = {name: np.load(root / row["path"], mmap_mode="r",
                            allow_pickle=False)
              for name, row in manifest["outputs"].items()
              if name != "train_document_positions"}
    source = manifest["source"]
    result["documents"] = np.memmap(
        Path(source["document_vectors"]), mode="r", dtype="<f4",
        shape=(int(source["document_count"]), int(source["dimension"])))
    return result


def states_12() -> np.ndarray:
    values = np.arange(1 << 12, dtype=np.uint16)
    return ((values[:, None] >> np.arange(12, dtype=np.uint16)) & 1).astype(np.uint8)


def cell_costs(point: np.ndarray, cut: np.ndarray,
               states: np.ndarray) -> np.ndarray:
    primary = point > cut
    crossed = states != primary[None, :]
    return crossed @ np.abs(point - cut).astype(np.float32)


def ordered_cells(point: np.ndarray, cut: np.ndarray,
                  states: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    costs = cell_costs(point, cut, states)
    ids = np.arange(len(states), dtype=np.int32)
    order = np.lexsort((ids, costs))
    return order.astype(np.int32), costs[order]


def exact_medoids(points: np.ndarray, count: int) -> np.ndarray:
    count = min(count, len(points))
    distances = np.abs(points[:, None, :] - points[None, :, :]).sum(axis=2)
    best: tuple[float, tuple[int, ...]] | None = None
    for selected in itertools.combinations(range(len(points)), count):
        cost = float(np.min(distances[:, selected], axis=1).sum())
        key = (cost, selected)
        if best is None or key < best:
            best = key
    require(best is not None, "oracle medoid selection is empty")
    return points[np.asarray(best[1], dtype=np.int64)]


def select_single(order: np.ndarray, postings: dict[int, np.ndarray],
                  candidate_budget: int,
                  initially_selected: set[int] | None = None) -> tuple[list[int], int, int]:
    selected = list(sorted(initially_selected or set()))
    seen = set(selected)
    used = sum(len(postings.get(cell, ())) for cell in selected)
    proposals = 0
    for value in order:
        cell = int(value)
        proposals += 1
        if cell in seen:
            continue
        size = len(postings.get(cell, ()))
        if size == 0:
            continue
        if used + size > candidate_budget:
            continue
        selected.append(cell)
        seen.add(cell)
        used += size
        if used == candidate_budget:
            break
    return selected, proposals, 0


def select_equal(orders: list[np.ndarray], postings: dict[int, np.ndarray],
                 candidate_budget: int) -> tuple[list[int], int, int]:
    selected: list[int] = []
    seen: set[int] = set()
    used = 0
    proposals = 0
    duplicates = 0
    for rank in range(len(orders[0])):
        for order in orders:
            cell = int(order[rank])
            proposals += 1
            if cell in seen:
                duplicates += 1
                continue
            seen.add(cell)
            size = len(postings.get(cell, ()))
            if size == 0:
                continue
            if used + size > candidate_budget:
                continue
            selected.append(cell)
            used += size
            if used == candidate_budget:
                return selected, proposals, duplicates
    return selected, proposals, duplicates


def select_adaptive(orders: list[np.ndarray], ordered_costs: list[np.ndarray],
                    postings: dict[int, np.ndarray],
                    candidate_budget: int) -> tuple[list[int], int, int]:
    heap: list[tuple[float, int, int]] = []
    for anchor, costs in enumerate(ordered_costs):
        heapq.heappush(heap, (float(costs[0]), anchor, 0))
    selected: list[int] = []
    seen: set[int] = set()
    used = 0
    proposals = 0
    duplicates = 0
    while heap:
        _, anchor, rank = heapq.heappop(heap)
        cell = int(orders[anchor][rank])
        proposals += 1
        if cell not in seen:
            seen.add(cell)
            size = len(postings.get(cell, ()))
            if size and used + size <= candidate_budget:
                selected.append(cell)
                used += size
                if used == candidate_budget:
                    return selected, proposals, duplicates
        else:
            duplicates += 1
        next_rank = rank + 1
        if next_rank < len(orders[anchor]):
            heapq.heappush(heap, (float(ordered_costs[anchor][next_rank]),
                                  anchor, next_rank))
    return selected, proposals, duplicates


def optimal_teacher_cells(teacher_ids: np.ndarray, document_cells: np.ndarray,
                          teacher_scores: np.ndarray,
                          postings: dict[int, np.ndarray], budget: int) -> set[int]:
    cells = sorted({int(document_cells[int(doc)]) for doc in teacher_ids})
    best: tuple[int, float, int, tuple[int, ...]] | None = None
    best_selected: tuple[int, ...] = ()
    for mask in range(1 << len(cells)):
        selected = tuple(cells[index] for index in range(len(cells))
                         if mask & (1 << index))
        cost = sum(len(postings.get(cell, ())) for cell in selected)
        if cost > budget:
            continue
        recovered = [index for index, doc in enumerate(teacher_ids)
                     if int(document_cells[int(doc)]) in selected]
        utility = float(sum(float(teacher_scores[index]) for index in recovered))
        key = (len(recovered), utility, -cost, tuple(-cell for cell in selected))
        if best is None or key > best:
            best = key
            best_selected = selected
    return set(best_selected)


def evaluate_candidates(full_scores: np.ndarray,
                        teacher_ids: np.ndarray, qrel_ids: np.ndarray,
                        qrel_scores: np.ndarray, postings: dict[int, np.ndarray],
                        cells: list[int], teacher_cells: set[int],
                        proposals: int, duplicate_proposals: int,
                        policy: str, anchors: int,
                        budget: int, elapsed_ms: float) -> dict[str, Any]:
    parts = [postings[cell] for cell in cells if cell in postings]
    raw = sum(len(part) for part in parts)
    candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
    scores = full_scores[candidates] if len(candidates) else np.empty(0, dtype=np.float32)
    selected = candidates[shared.ordinal.top(scores, 10)]
    teacher = set(map(int, teacher_ids))
    overlap = float(len(set(map(int, selected)) & teacher)) / 10.0
    recovered_cells = len(set(cells) & teacher_cells)
    return {"policy": policy, "anchors": anchors, "candidate_budget": budget,
            "overlap": overlap,
            "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids, qrel_scores),
            "teacher_cells_recovered": recovered_cells,
            "teacher_cell_count": len(teacher_cells),
            "raw_postings": raw, "unique_candidates": int(len(candidates)),
            "cell_proposal_duplicate_ratio": ((duplicate_proposals / proposals)
                                                if proposals else 0.0),
            "document_duplicate_ratio": 0.0,
            "cells_opened": len(cells),
            "teacher_docs_per_opened_cell": (overlap * 10.0 / len(cells)) if cells else 0.0,
            "teacher_rescue_per_1000_candidates": (overlap * 10.0 * 1000.0 /
                                                    len(candidates)) if len(candidates) else 0.0,
            "route_ms": elapsed_ms}


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("policy", "anchors", "candidate_budget", "partition")
    result = []
    for key in sorted({tuple(row[name] for name in keys) for row in rows}):
        current = [row for row in rows if tuple(row[name] for name in keys) == key]
        result.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "mean_raw_postings": float(np.mean([r["raw_postings"] for r in current])),
                       "mean_cell_proposal_duplicate_ratio": float(np.mean(
                           [r["cell_proposal_duplicate_ratio"] for r in current])),
                       "mean_document_duplicate_ratio": 0.0,
                       "mean_cells_opened": float(np.mean([r["cells_opened"] for r in current])),
                       "mean_teacher_cells_recovered": float(np.mean([r["teacher_cells_recovered"] for r in current])),
                       "mean_teacher_docs_per_opened_cell": float(np.mean([r["teacher_docs_per_opened_cell"] for r in current])),
                       "mean_teacher_rescue_per_1000_candidates": float(np.mean([r["teacher_rescue_per_1000_candidates"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95))})
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    teacher_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    teacher_scores = np.asarray(data["eval_teacher_scores"], dtype=np.float32)
    mean, projection_rows = shared.pca_init(documents[::4], 12)
    document_projected = (documents - mean) @ projection_rows.T
    cut = np.median(document_projected[::4], axis=0).astype(np.float32)
    document_bits = document_projected > cut
    powers = np.left_shift(np.uint16(1), np.arange(12, dtype=np.uint16))
    document_cells = (document_bits.astype(np.uint16) * powers).sum(axis=1,
                                                                    dtype=np.uint16)
    postings_lists: dict[int, list[int]] = {}
    for doc, cell in enumerate(document_cells):
        postings_lists.setdefault(int(cell), []).append(doc)
    postings = {cell: np.asarray(values, dtype=np.int32)
                for cell, values in postings_lists.items()}
    states = states_12()
    rows: list[dict[str, Any]] = []
    partitions = np.asarray(data["eval_partition"])
    for index, query in enumerate(queries):
        query_point = (query - mean) @ projection_rows.T
        full_scores = documents @ query
        single_order, _ = ordered_cells(query_point, cut, states)
        teacher_points = document_projected[teacher_ids[index]]
        teacher_cells = {int(document_cells[int(doc)]) for doc in teacher_ids[index]}
        for budget in args.candidate_budgets:
            started = time.perf_counter()
            cells, proposals, duplicates = select_single(single_order, postings, budget)
            row = evaluate_candidates(full_scores, teacher_ids[index],
                np.asarray(data["eval_qrel_ids"])[index],
                np.asarray(data["eval_qrel_scores"])[index], postings, cells,
                teacher_cells, proposals, duplicates, "single_anchor", 1, budget,
                (time.perf_counter() - started) * 1000.0)
            rows.append({**row, "partition": str(partitions[index]), "query": index})
            optimal = optimal_teacher_cells(teacher_ids[index], document_cells,
                                            teacher_scores[index], postings, budget)
            started = time.perf_counter()
            optimal_cells, optimal_proposals, optimal_duplicates = select_single(
                single_order, postings, budget, optimal)
            row = evaluate_candidates(full_scores, teacher_ids[index],
                np.asarray(data["eval_qrel_ids"])[index],
                np.asarray(data["eval_qrel_scores"])[index], postings,
                optimal_cells, teacher_cells, optimal_proposals, optimal_duplicates,
                "optimal_teacher_cells_plus_single_fill", 0, budget,
                (time.perf_counter() - started) * 1000.0)
            rows.append({**row, "partition": str(partitions[index]), "query": index})
            for anchor_count in args.anchors:
                anchors = exact_medoids(teacher_points, anchor_count)
                ordered = [ordered_cells(anchor, cut, states) for anchor in anchors]
                orders = [value[0] for value in ordered]
                costs = [value[1] for value in ordered]
                for policy in ("multi_anchor_equal", "multi_anchor_adaptive"):
                    started = time.perf_counter()
                    if policy == "multi_anchor_equal":
                        chosen, proposals, duplicates = select_equal(orders, postings, budget)
                    else:
                        chosen, proposals, duplicates = select_adaptive(
                            orders, costs, postings, budget)
                    row = evaluate_candidates(full_scores, teacher_ids[index],
                        np.asarray(data["eval_qrel_ids"])[index],
                        np.asarray(data["eval_qrel_scores"])[index], postings,
                        chosen, teacher_cells, proposals, duplicates, policy, anchor_count,
                        budget, (time.perf_counter() - started) * 1000.0)
                    rows.append({**row, "partition": str(partitions[index]),
                                 "query": index})
    return {"schema_version": 1, "family": "pca12_multi_anchor_oracle",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"partition": "pca12_documents_stride4_svd_median",
                          "replication": 1,
                          "candidate_budgets": args.candidate_budgets,
                          "anchor_counts": args.anchors,
                          "multi_anchor_source": "exact_teacher_top10_document_medoids",
                          "optimal_cell_solver": "exact_subset_enumeration_then_single_anchor_fill",
                          "final_scoring": "exact_e5_fp32"},
            "product_claim": False,
            "limitations": ["teacher-derived anchors and cells are oracle-only", "single DE-1M split", "Python timing is directional"]}


def self_test() -> None:
    points = np.asarray([[0., 0.], [0., 1.], [9., 9.]], dtype=np.float32)
    medoids = exact_medoids(points, 2)
    require(medoids.tolist() == [[0., 0.], [9., 9.]],
            "multi-anchor medoid oracle differs")
    states = states_12()
    require(states.shape == (4096, 12) and int(states[3].sum()) == 2,
            "PCA12 state enumeration differs")
    print("PCA12 multi-anchor oracle self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--anchors", default="2,4,8")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None,
            "--cache and --output are required unless --self-test is used")
    args.candidate_budgets = [int(value) for value in args.candidate_budgets.split(",")]
    args.anchors = [int(value) for value in args.anchors.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
