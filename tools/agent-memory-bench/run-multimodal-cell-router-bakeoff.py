#!/usr/bin/env python3
"""Compare learned multimodal PCA12 cell routers on a frozen partition.

This is a routing-ceiling follow-up to the teacher-derived multi-anchor oracle.
It evaluates direct multi-label cell prediction, hierarchical region prediction,
and a train-query kNN cell-set baseline.  Every policy is replayed with exact
E5 scoring at fixed unique-document budgets; no result is a product claim.
"""

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
            "cache family differs")
    root = path.parent
    result = {name: np.load(root / row["path"], mmap_mode="r",
                            allow_pickle=False)
              for name, row in manifest["outputs"].items()}
    source = manifest["source"]
    result["documents"] = np.memmap(
        Path(source["document_vectors"]), mode="r", dtype="<f4",
        shape=(int(source["document_count"]), int(source["dimension"])))
    return result


def partition(documents: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                               np.ndarray, np.ndarray]:
    mean, projection = shared.pca_init(documents[::4], 12)
    projected = (documents - mean) @ projection.T
    cuts = np.median(projected[::4], axis=0).astype(np.float32)
    bits = projected > cuts
    powers = np.left_shift(np.uint16(1), np.arange(12, dtype=np.uint16))
    cells = (bits.astype(np.uint16) * powers).sum(axis=1, dtype=np.uint16)
    return mean, projection, cuts, projected, cells


def cell_costs(point: np.ndarray, cuts: np.ndarray,
               states: np.ndarray) -> np.ndarray:
    primary = point > cuts
    return (states != primary[None, :]) @ np.abs(point - cuts).astype(np.float32)


def states_12() -> np.ndarray:
    values = np.arange(4096, dtype=np.uint16)
    return ((values[:, None] >> np.arange(12, dtype=np.uint16)) & 1).astype(np.uint8)


def query_order(point: np.ndarray, cuts: np.ndarray,
                states: np.ndarray) -> np.ndarray:
    costs = cell_costs(point, cuts, states)
    return np.lexsort((np.arange(len(states), dtype=np.int32), costs)).astype(np.int32)


def build_postings(cells: np.ndarray) -> dict[int, np.ndarray]:
    values: dict[int, list[int]] = {}
    for doc, cell in enumerate(cells):
        values.setdefault(int(cell), []).append(doc)
    return {key: np.asarray(value, dtype=np.int32) for key, value in values.items()}


def fill_cells(seed_cells: list[int], order: np.ndarray,
               postings: dict[int, np.ndarray], budget: int) -> tuple[list[int], int]:
    selected: list[int] = []
    seen: set[int] = set()
    used = 0
    proposals = 0
    for cell in list(seed_cells) + [int(value) for value in order]:
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


def multi_hot(cell_lists: list[list[int]], width: int) -> np.ndarray:
    target = np.zeros((len(cell_lists), width), dtype=np.float32)
    for row, cells in enumerate(cell_lists):
        target[row, np.asarray(cells, dtype=np.int64)] = 1.0
    return target


def train_head(queries: np.ndarray, cell_lists: list[list[int]], width: int,
               seed: int, epochs: int) -> dict[str, np.ndarray]:
    import torch

    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    q = torch.from_numpy(np.asarray(queries, dtype=np.float32))
    target = torch.from_numpy(multi_hot(cell_lists, width))
    model = torch.nn.Sequential(torch.nn.Linear(q.shape[1], 128),
                                torch.nn.GELU(approximate="tanh"),
                                torch.nn.Linear(128, width))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=1.0e-4)
    positives = target.sum(dim=1).clamp_min(1.0)
    # The ranking term directly optimizes top-cell selection while BCE keeps
    # logits calibrated enough for the hierarchical policy.
    for _ in range(epochs):
        logits = model(q)
        pos_mean = (logits * target).sum(dim=1) / positives
        negative_logits = logits.masked_fill(target.bool(), -1.0e9)
        hard_negative = torch.topk(negative_logits, k=min(128, width - 1), dim=1).values
        ranking = torch.nn.functional.softplus(hard_negative - pos_mean[:, None] + 0.25).mean()
        pos_weight = (width - positives.mean()) / positives.mean().clamp_min(1.0)
        bce = torch.nn.functional.binary_cross_entropy_with_logits(
            logits, target, pos_weight=pos_weight)
        loss = ranking + 0.1 * bce
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
    return {"weight1": model[0].weight.detach().numpy().astype(np.float32),
            "bias1": model[0].bias.detach().numpy().astype(np.float32),
            "weight2": model[2].weight.detach().numpy().astype(np.float32),
            "bias2": model[2].bias.detach().numpy().astype(np.float32)}


def infer_head(queries: np.ndarray, artifact: dict[str, np.ndarray]) -> np.ndarray:
    hidden = queries @ artifact["weight1"].T + artifact["bias1"]
    hidden = 0.5 * hidden * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) *
                                           (hidden + 0.044715 * hidden ** 3)))
    return hidden @ artifact["weight2"].T + artifact["bias2"]


def teacher_cells(ids: np.ndarray, document_cells: np.ndarray) -> list[list[int]]:
    return [sorted({int(document_cells[int(doc)]) for doc in row}) for row in ids]


def evaluate_policy(policy: str, seed: int, query_index: int,
                    partition_name: str,
                    query: np.ndarray, teacher_ids: np.ndarray,
                    qrel_ids: np.ndarray, qrel_scores: np.ndarray,
                    documents: np.ndarray, postings: dict[int, np.ndarray],
                    query_point: np.ndarray, order: np.ndarray,
                    seed_cells: list[int], budgets: list[int],
                    full_scores: np.ndarray, teacher_cell_set: set[int],
                    model_bytes: int) -> list[dict[str, Any]]:
    rows = []
    for budget in budgets:
        started = time.perf_counter()
        cells, proposals = fill_cells(seed_cells, order, postings, budget)
        parts = [postings[cell] for cell in cells if cell in postings]
        candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
        scores = full_scores[candidates] if len(candidates) else np.empty(0, dtype=np.float32)
        selected = candidates[shared.ordinal.top(scores, 10)]
        teacher = set(map(int, teacher_ids))
        overlap = float(len(set(map(int, selected)) & teacher)) / 10.0
        rows.append({"policy": policy, "seed": seed, "partition": partition_name,
                     "query": query_index,
                     "candidate_budget": budget, "overlap": overlap,
                     "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids, qrel_scores),
                     "unique_candidates": int(len(candidates)),
                     "raw_postings": int(sum(len(part) for part in parts)),
                     "cells_opened": len(cells), "proposals": proposals,
                     "teacher_cells_recovered": len(set(cells) & teacher_cell_set),
                     "model_bytes": model_bytes,
                     "route_ms": (time.perf_counter() - started) * 1000.0})
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("policy", "candidate_budget", "partition")
    output = []
    for key in sorted({tuple(row[name] for name in keys) for row in rows}):
        current = [row for row in rows if tuple(row[name] for name in keys) == key]
        output.append({**dict(zip(keys, key)), "query_count": len(current),
                       "seed_count": len({r["seed"] for r in current}),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "mean_cells_opened": float(np.mean([r["cells_opened"] for r in current])),
                       "mean_teacher_cells_recovered": float(np.mean([r["teacher_cells_recovered"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "model_bytes": int(max(r["model_bytes"] for r in current))})
    return output


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    train_queries = np.asarray(data["train_queries"], dtype=np.float32)
    eval_queries = np.asarray(data["eval_queries"], dtype=np.float32)
    train_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    eval_ids = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    eval_qrel_ids = np.asarray(data["eval_qrel_ids"], dtype=np.int64)
    eval_qrel_scores = np.asarray(data["eval_qrel_scores"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    mean, projection, cuts, projected, document_cells = partition(documents)
    postings = build_postings(document_cells)
    states = states_12()
    train_cell_lists = teacher_cells(train_ids, document_cells)
    eval_cell_lists = teacher_cells(eval_ids, document_cells)
    train_normalized = train_queries - mean
    eval_normalized = eval_queries - mean
    direct_artifacts = {seed: train_head(train_normalized, train_cell_lists, 4096,
                                          seed, args.epochs)
                        for seed in args.seeds}
    group_artifacts: dict[int, dict[int, dict[str, np.ndarray]]] = {}
    group_labels: dict[int, list[list[int]]] = {}
    for groups in args.groups:
        shift = 12 - int(np.log2(groups))
        labels = [[int(cell) >> shift for cell in cells] for cells in train_cell_lists]
        group_labels[groups] = labels
        group_artifacts[groups] = {seed: train_head(train_normalized, labels, groups,
                                                     seed, args.epochs)
                                   for seed in args.seeds}
    # Compute the exact E5 score matrix as one GEMM.  The previous per-query
    # GEMV path was mathematically identical but needlessly serialized BLAS
    # work; this keeps the replay tractable while retaining exact scores.
    all_scores = documents @ eval_queries.T
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(eval_queries):
        point = (query - mean) @ projection.T
        order = query_order(point, cuts, states)
        full_scores = all_scores[:, index]
        teacher = eval_cell_lists[index]
        # kNN baselines reuse only train-query teacher cells; they never inspect
        # the held-out teacher labels during routing.
        similarities = train_normalized @ (query - mean)
        for k in args.knn:
            neighbours = np.argsort(-similarities, kind="stable")[:k]
            seeds = sorted({cell for neighbour in neighbours for cell in train_cell_lists[int(neighbour)]})
            rows.extend(evaluate_policy(f"knn{k}", 0, index, str(partitions[index]), query, eval_ids[index],
                                        eval_qrel_ids[index], eval_qrel_scores[index],
                                        documents, postings, point, order, seeds,
                                        args.budgets, full_scores, set(teacher), 0))
        for seed, artifact in direct_artifacts.items():
            logits = infer_head(eval_normalized[index:index + 1], artifact)[0]
            for count in args.direct_cells:
                seeds = np.argsort(-logits, kind="stable")[:count].astype(np.int32).tolist()
                rows.extend(evaluate_policy(f"direct4096_top{count}", seed, index, str(partitions[index]), query,
                                            eval_ids[index], eval_qrel_ids[index],
                                            eval_qrel_scores[index], documents, postings,
                                            point, order, seeds, args.budgets, full_scores,
                                            set(teacher), sum(value.nbytes for value in artifact.values())))
        for groups, artifacts in group_artifacts.items():
            shift = 12 - int(np.log2(groups))
            group_cells = {group: [cell for cell in range(4096)
                                   if (cell >> shift) == group] for group in range(groups)}
            for seed, artifact in artifacts.items():
                group_logits = infer_head(eval_normalized[index:index + 1], artifact)[0]
                for top_groups in args.top_groups:
                    selected_groups = np.argsort(-group_logits, kind="stable")[:top_groups]
                    # Within predicted regions use the exact local lattice cost;
                    # the model only chooses the multimodal coarse regions.
                    local = []
                    costs = cell_costs(point, cuts, states)
                    for group in selected_groups:
                        local.extend(sorted(group_cells[int(group)], key=lambda cell: (costs[cell], cell)))
                    rows.extend(evaluate_policy(f"hier{groups}_top{top_groups}", seed, index,
                                                str(partitions[index]), query, eval_ids[index], eval_qrel_ids[index],
                                                eval_qrel_scores[index], documents, postings,
                                                point, order, local, args.budgets, full_scores,
                                                set(teacher), sum(value.nbytes for value in artifact.values())))
    return {"schema_version": 1, "family": "multimodal_cell_router_bakeoff",
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"partition": "pca12_documents_stride4_svd_median",
                          "candidate_budgets": args.budgets,
                          "direct_cell_counts": args.direct_cells,
                          "hierarchical_groups": args.groups,
                          "hierarchical_top_groups": args.top_groups,
                          "knn_values": args.knn, "seeds": args.seeds,
                          "epochs": args.epochs, "final_scoring": "exact_e5_fp32"},
            "product_claim": False,
            "limitations": ["single DE-1M split and 153 train queries",
                             "teacher cells used only as training labels",
                             "Python timing is directional",
                             "hierarchical groups are fixed PCA-bit prefixes"]}


def self_test() -> None:
    require(states_12().shape == (4096, 12), "state enumeration differs")
    target = multi_hot([[0, 3], [2]], 4)
    require(target.tolist() == [[1., 0., 0., 1.], [0., 0., 1., 0.]],
            "multi-label target differs")
    print("multimodal cell router self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-budgets", default="32000,64000,128000")
    parser.add_argument("--direct-cells", default="32,64,128")
    parser.add_argument("--groups", default="64,128")
    parser.add_argument("--top-groups", default="4,8,16")
    parser.add_argument("--knn", default="1,4,8")
    parser.add_argument("--seeds", default="13,37,101")
    parser.add_argument("--epochs", type=int, default=180)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None,
            "--cache and --output are required unless --self-test is used")
    args.budgets = [int(value) for value in args.candidate_budgets.split(",")]
    args.direct_cells = [int(value) for value in args.direct_cells.split(",")]
    args.groups = [int(value) for value in args.groups.split(",")]
    args.top_groups = [int(value) for value in args.top_groups.split(",")]
    args.knn = [int(value) for value in args.knn.split(",")]
    args.seeds = [int(value) for value in args.seeds.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
