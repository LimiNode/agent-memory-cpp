#!/usr/bin/env python3
"""Evaluate routing-cell-aware objectives for ordinal document routers.

The runner first trains a query-only head against a frozen PCA12 partition,
then performs alternating shared-projection rounds.  Cell assignments are
remined after every round; training uses the same threshold-crossing cost as
inference and reports teacher-cell rank diagnostics.
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


def load_shared() -> Any:
    path = Path(__file__).with_name("run-shared-supervised-ordinal-projection.py")
    spec = importlib.util.spec_from_file_location("shared_ordinal", path)
    require(spec is not None and spec.loader is not None,
            "shared ordinal helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()


def all_states(axes: int, levels: int) -> np.ndarray:
    states = np.zeros((levels ** axes, axes), dtype=np.uint8)
    for index in range(len(states)):
        value = index
        for axis in range(axes):
            states[index, axis] = value % levels
            value //= levels
    return states


def cell_costs_np(projected: np.ndarray, cuts: np.ndarray,
                  states: np.ndarray, levels: int) -> np.ndarray:
    primary = np.sum(projected[:, None] > cuts, axis=1, dtype=np.int16)
    costs = np.zeros(len(states), dtype=np.float32)
    for axis in range(len(projected)):
        for boundary in range(levels - 1):
            crossed = ((states[:, axis] <= boundary) & (primary[axis] > boundary)) | \
                      ((states[:, axis] > boundary) & (primary[axis] <= boundary))
            costs += crossed.astype(np.float32) * abs(float(projected[axis]) -
                                                       float(cuts[axis, boundary]))
    return costs


def cell_costs_torch(projected: Any, cuts: Any, states: Any,
                     levels: int) -> Any:
    import torch
    primary = torch.sum(projected[:, :, None] > cuts[None, :, :], dim=2)
    result = torch.zeros((projected.shape[0], states.shape[0]),
                         dtype=projected.dtype)
    for axis in range(projected.shape[1]):
        for boundary in range(levels - 1):
            crossed = ((states[None, :, axis] <= boundary) & (primary[:, axis, None] > boundary)) | \
                      ((states[None, :, axis] > boundary) & (primary[:, axis, None] <= boundary))
            result = result + crossed.to(projected.dtype) * torch.abs(
                projected[:, axis, None] - cuts[axis, boundary])
    return result


def cell_id(state: np.ndarray, levels: int) -> int:
    value = 0
    multiplier = 1
    for level in state:
        value += int(level) * multiplier
        multiplier *= levels
    return value


def mine_cells(projected_queries: np.ndarray, teacher_ids: np.ndarray,
               document_codes: np.ndarray, cuts: np.ndarray, levels: int,
               states: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    positive: list[list[int]] = []
    negative: list[list[int]] = []
    ranks: list[np.ndarray] = []
    for query_index, projected in enumerate(projected_queries):
        costs = cell_costs_np(projected, cuts, states, levels)
        order = np.lexsort((np.arange(len(states), dtype=np.int64), costs))
        positive_ids = sorted({cell_id(document_codes[int(doc)], levels)
                               for doc in teacher_ids[query_index]})
        positive.append(positive_ids)
        wrong = [int(value) for value in order if int(value) not in positive_ids]
        negative.append(wrong[:count])
        position = {int(value): rank for rank, value in enumerate(order)}
        ranks.append(np.asarray([position[cell_id(document_codes[int(doc)], levels)]
                                for doc in teacher_ids[query_index]], dtype=np.int32))
    width = max(len(value) for value in positive)
    positive_array = np.full((len(positive), width), -1, dtype=np.int64)
    for index, values in enumerate(positive):
        positive_array[index, :len(values)] = values
    negative_array = np.asarray(negative, dtype=np.int64)
    return positive_array, negative_array, np.asarray(ranks, dtype=np.int32)


def train_query_head(queries: np.ndarray, positive: np.ndarray,
                     negative: np.ndarray, mean: np.ndarray, projection: np.ndarray,
                     cuts: np.ndarray, levels: int, states: np.ndarray,
                     seed: int, rounds: int) -> np.ndarray:
    import torch
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    q = torch.from_numpy((queries - mean).astype(np.float32))
    initial = torch.from_numpy(projection.astype(np.float32))
    model = torch.nn.Sequential(torch.nn.Linear(384, 128),
                                torch.nn.GELU(approximate="tanh"),
                                torch.nn.Linear(128, projection.shape[0]))
    with torch.no_grad():
        model[2].weight.zero_()
        model[2].bias.zero_()
    # Add the PCA coordinate as a fixed skip connection; the MLP learns a
    # query-specific correction without losing the baseline coordinate scale.
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01,
                                  weight_decay=1.0e-4)
    pos = torch.from_numpy(np.maximum(positive, 0).astype(np.int64))
    neg = torch.from_numpy(negative.astype(np.int64))
    docs = torch.from_numpy((np.asarray(states, dtype=np.int64)))
    cut_tensor = torch.from_numpy(cuts.astype(np.float32))
    for _ in range(rounds):
        correction = model(q)
        projected = q @ initial.T + correction
        costs = cell_costs_torch(projected, cut_tensor, docs, levels)
        pos_cost = torch.gather(costs, 1, pos)
        neg_cost = torch.gather(costs, 1, neg)
        valid = positive >= 0
        pos_cost = pos_cost.masked_fill(~torch.from_numpy(valid), 0.0)
        positive_mean = pos_cost.sum(dim=1) / torch.from_numpy(valid.sum(axis=1).clip(min=1)).float()
        ranking = torch.nn.functional.softplus(
            positive_mean[:, None] - neg_cost + 0.25).mean()
        anchor = torch.mean(correction ** 2)
        loss = ranking + 0.01 * anchor
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
    return {"weight1": model[0].weight.detach().numpy().astype(np.float32),
            "bias1": model[0].bias.detach().numpy().astype(np.float32),
            "weight2": model[2].weight.detach().numpy().astype(np.float32),
            "bias2": model[2].bias.detach().numpy().astype(np.float32)}


def infer_query_head(queries: np.ndarray, mean: np.ndarray,
                     projection: np.ndarray, artifact: dict[str, np.ndarray]) -> np.ndarray:
    normalized = queries - mean
    hidden = normalized @ artifact["weight1"].T + artifact["bias1"]
    hidden = 0.5 * hidden * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) *
                                           (hidden + 0.044715 * hidden ** 3)))
    return normalized @ projection.T + hidden @ artifact["weight2"].T + artifact["bias2"]


def train_shared_round(queries: np.ndarray, positive: np.ndarray,
                       negative: np.ndarray, documents: np.ndarray,
                       mean: np.ndarray, projection: np.ndarray, cuts: np.ndarray,
                       levels: int, states: np.ndarray, seed: int, epochs: int) -> np.ndarray:
    import torch
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    q = torch.from_numpy((queries - mean).astype(np.float32))
    parameter = torch.nn.Parameter(torch.from_numpy(projection.copy()))
    optimizer = torch.optim.AdamW([parameter], lr=0.003, weight_decay=1.0e-4)
    pos = torch.from_numpy(np.maximum(positive, 0).astype(np.int64))
    neg = torch.from_numpy(negative.astype(np.int64))
    cells = torch.from_numpy(states.astype(np.int64))
    cut_tensor = torch.from_numpy(cuts.astype(np.float32))
    valid = torch.from_numpy((positive >= 0))
    for _ in range(epochs):
        projected = q @ parameter.T
        costs = cell_costs_torch(projected, cut_tensor, cells, levels)
        pos_cost = torch.gather(costs, 1, pos).masked_fill(~valid, 0.0)
        positive_mean = pos_cost.sum(dim=1) / valid.sum(axis=1).clip(min=1).float()
        negative_cost = torch.gather(costs, 1, neg)
        ranking = torch.nn.functional.softplus(
            positive_mean[:, None] - negative_cost + 0.25).mean()
        orth = torch.mean((parameter @ parameter.T - torch.eye(parameter.shape[0])) ** 2)
        anchor = torch.mean((parameter - torch.from_numpy(projection)) ** 2)
        loss = ranking + 0.01 * orth + 0.05 * anchor
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
    return parameter.detach().numpy().astype(np.float32)


def evaluate_variant(name: str, documents: np.ndarray, queries: np.ndarray,
                     teacher_ids: np.ndarray, qrel_ids: np.ndarray,
                     qrel_scores: np.ndarray, partitions: np.ndarray,
                     mean: np.ndarray, projection: np.ndarray, cuts: np.ndarray,
                     levels: int, cell_budgets: list[int], document_budget: int,
                     states: np.ndarray,
                     query_projected_override: np.ndarray | None = None) -> tuple[list[dict[str, Any]], dict[str, float]]:
    doc_projected, doc_codes = shared.ordinal_codes(documents, mean, projection,
                                                    cuts)
    query_projected, query_codes = shared.ordinal_codes(queries, mean, projection,
                                                        cuts)
    if query_projected_override is not None:
        query_projected = query_projected_override
        query_codes = np.sum(query_projected[:, :, None] > cuts[None, :, :],
                             axis=2, dtype=np.uint8)
    postings = shared.build_postings(doc_codes, doc_projected, cuts, levels)
    positive, _, teacher_ranks = mine_cells(query_projected, teacher_ids, doc_codes,
                                            cuts, levels, states, 256)
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        for budget in cell_budgets:
            started = time.perf_counter()
            cells = shared.probe(query_codes[index], query_projected[index], cuts,
                                 levels, budget)
            parts = [postings[cell] for cell in cells if cell in postings]
            candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
            scores = documents[candidates] @ query if len(candidates) else np.empty(0, dtype=np.float32)
            selected = candidates[shared.ordinal.top(scores, document_budget)]
            overlap = float(len(set(map(int, selected[:10])) & set(map(int, teacher_ids[index])))) / 10.0
            rows.append({"router": name, "partition": str(partitions[index]),
                         "query": index, "cell_budget": budget,
                         "document_budget": document_budget, "overlap": overlap,
                         "qrels_ndcg": shared.qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                         "raw_postings": int(sum(len(part) for part in parts)),
                         "unique_candidates": int(len(candidates)),
                         "route_ms": (time.perf_counter() - started) * 1000.0,
                         "payload_bytes": max(1, int(np.ceil(np.log2(levels ** projection.shape[0]) / 8.0))),
                         "model_bytes": int(mean.nbytes + projection.nbytes + cuts.nbytes)})
    diagnostics = {"mean_teacher_cell_rank": float(np.mean(teacher_ranks)),
                   "p90_teacher_cell_rank": float(np.quantile(teacher_ranks, .9)),
                   "p95_teacher_cell_rank": float(np.quantile(teacher_ranks, .95)),
                   "teacher_cell_survival_p32": float(np.mean(teacher_ranks <= 32)),
                   "teacher_cell_survival_p64": float(np.mean(teacher_ranks <= 64)),
                   "teacher_cell_survival_p128": float(np.mean(teacher_ranks <= 128)),
                   "teacher_cell_survival_p256": float(np.mean(teacher_ranks <= 256))}
    return rows, diagnostics


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("router", "partition", "cell_budget", "document_budget")
    output = []
    for key in sorted({tuple(row[value] for value in keys) for row in rows}):
        current = [row for row in rows if tuple(row[value] for value in keys) == key]
        output.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "payload_bytes": current[0]["payload_bytes"],
                       "model_bytes": current[0]["model_bytes"]})
    return output


def load_cache(path: Path) -> dict[str, np.ndarray]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    result = {name: np.load(root / row["path"], mmap_mode="r", allow_pickle=False)
              for name, row in manifest["outputs"].items()
              if name != "train_document_positions"}
    source = manifest["source"]
    result["documents"] = np.memmap(Path(source["document_vectors"]), mode="r",
                                     dtype="<f4", shape=(int(source["document_count"]),
                                                          int(source["dimension"])))
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    train_queries = np.asarray(data["train_queries"], dtype=np.float32)
    teacher_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    eval_queries = np.asarray(data["eval_queries"], dtype=np.float32)
    eval_teacher = np.asarray(data["eval_teacher_ids"], dtype=np.int64)
    partitions = np.asarray(data["eval_partition"])
    sample = documents[::4]
    mean, base_rows = shared.pca_init(sample, 12)
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    training: dict[str, Any] = {}
    for label, axes, levels in (("pca12_query_cell_rank", 12, 2),
                                ("shared12x2_alternating", 12, 2),
                                ("shared8x3_alternating", 8, 3),
                                ("shared6x4_alternating", 6, 4)):
        projection = base_rows[:axes].copy()
        projected_sample = (sample - mean) @ projection.T
        cuts = np.quantile(projected_sample, np.arange(1, levels) / levels,
                           axis=0).T.astype(np.float32)
        states = all_states(axes, levels)
        if label.startswith("pca"):
            query_projected, _ = shared.ordinal_codes(train_queries, mean,
                                                       projection, cuts)
            _, train_codes = shared.ordinal_codes(documents, mean, projection, cuts)
            positive, negative, _ = mine_cells(query_projected, teacher_ids,
                                               train_codes, cuts, levels, states, 256)
            baseline_rows, baseline_diagnostics = evaluate_variant(
                "pca12_corrected_baseline", documents, eval_queries, eval_teacher,
                np.asarray(data["eval_qrel_ids"]), np.asarray(data["eval_qrel_scores"]),
                partitions, mean, projection, cuts, levels, args.cell_budgets,
                args.document_budget, states)
            rows.extend(baseline_rows)
            diagnostics["pca12_corrected_baseline"] = baseline_diagnostics
            query_projected_eval, _ = shared.ordinal_codes(eval_queries, mean,
                                                            projection, cuts)
            # Query-only head starts at PCA coordinates and is trained on the
            # exact same cell-cost objective used by the evaluator.
            query_artifact = train_query_head(train_queries, positive, negative,
                                              mean, projection, cuts, levels,
                                              states, args.seed, args.epochs)
            training[label] = {"kind": "frozen_pca_cell_mining", "epochs": args.epochs,
                               "query_model_bytes": int(sum(value.nbytes for value in query_artifact.values()))}
            eval_query_projected = infer_query_head(eval_queries, mean, projection,
                                                     query_artifact)
            eval_projection = projection
        else:
            eval_projection = projection
            for round_index in range(args.alternating_rounds):
                doc_projected, doc_codes = shared.ordinal_codes(documents, mean,
                                                                 eval_projection, cuts)
                train_projected, _ = shared.ordinal_codes(train_queries, mean,
                                                          eval_projection, cuts)
                positive, negative, _ = mine_cells(train_projected, teacher_ids,
                                                   doc_codes, cuts, levels, states, 256)
                eval_projection = train_shared_round(train_queries, positive, negative,
                                                     documents, mean, eval_projection,
                                                     cuts, levels, states,
                                                     args.seed + round_index + axes,
                                                     args.epochs)
                projected_sample = (sample - mean) @ eval_projection.T
                cuts = np.quantile(projected_sample,
                                   np.arange(1, levels) / levels, axis=0).T.astype(np.float32)
            training[label] = {"kind": "alternating_shared_cell_ranking",
                               "rounds": args.alternating_rounds,
                               "epochs_per_round": args.epochs}
        if label.startswith("pca"):
            # evaluate_variant derives query coordinates from projection; use
            # the trained query-head coordinates for the frozen partition by
            # passing them through a temporary projection wrapper below.
            variant_rows, variant_diagnostics = evaluate_variant(
                label, documents, eval_queries, eval_teacher,
                np.asarray(data["eval_qrel_ids"]), np.asarray(data["eval_qrel_scores"]),
                partitions, mean, eval_projection, cuts, levels, args.cell_budgets,
                args.document_budget, states, query_projected_override=eval_query_projected)
        else:
            variant_rows, variant_diagnostics = evaluate_variant(
            label, documents, eval_queries, eval_teacher,
            np.asarray(data["eval_qrel_ids"]), np.asarray(data["eval_qrel_scores"]),
            partitions, mean, eval_projection, cuts, levels, args.cell_budgets,
            args.document_budget, states)
        rows.extend(variant_rows)
        diagnostics[label] = variant_diagnostics
    return {"schema_version": 1, "family": "cell_aware_ordinal_router",
            "rows": rows, "summaries": summarize(rows),
            "training": training, "diagnostics": diagnostics,
            "protocol": {"objective": "cell_cost_pairwise_softplus",
                          "probing": "query_specific_threshold_distance",
                          "thresholds": "train_document_quantiles",
                          "replication": 1, "document_budget": args.document_budget},
            "limitations": ["frozen query-head inference artifact is retained only in-process; PCA control is the exact frozen baseline", "single seed and 153 training queries", "routing ceiling only; no downstream codec"]}


def self_test() -> None:
    rng = np.random.default_rng(41)
    vectors = rng.normal(size=(48, 12)).astype(np.float32)
    mean, rows = shared.pca_init(vectors, 4)
    projected = (vectors - mean) @ rows.T
    cuts = np.quantile(projected, [.5], axis=0).T.astype(np.float32)
    states = all_states(4, 2)
    costs = cell_costs_np(projected[0], cuts, states, 2)
    primary = np.sum(projected[0][None, :, None] > cuts[None, :, :], axis=2,
                     dtype=np.uint8)[0]
    require(int(np.argmin(costs)) == shared.mixed_id(primary, 2),
            "cell cost primary state differs")
    print("cell-aware ordinal router self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cell-budgets", default="32,64,128,256")
    parser.add_argument("--document-budget", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--alternating-rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(args.cache is not None and args.output is not None,
            "--cache and --output are required unless --self-test is used")
    args.cell_budgets = [int(value) for value in args.cell_budgets.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n",
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
