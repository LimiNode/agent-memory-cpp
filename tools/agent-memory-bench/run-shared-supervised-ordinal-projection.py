#!/usr/bin/env python3
"""Measure retrieval-supervised shared ordinal projections.

This experiment keeps quantile thresholds and R=1 fixed while changing the
document/query geometry.  Unlike the historical Binary12 replay, documents
and queries share one learned linear projection.  The objective is a
continuous pairwise L1 ranking loss with occupancy and PCA-anchor
regularisation; hard quantisation is applied only after training.
"""

from __future__ import annotations

import argparse
import heapq
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_ordinal() -> Any:
    path = Path(__file__).with_name("run-neuroute-ordinal-lattice.py")
    spec = importlib.util.spec_from_file_location("ordinal_lattice", path)
    require(spec is not None and spec.loader is not None,
            "ordinal lattice helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ordinal = load_ordinal()


def load_cache(path: Path) -> dict[str, np.ndarray]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_ordinal_lattice_input",
            "shared ordinal cache family differs")
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


def pca_init(sample: np.ndarray, axes: int) -> tuple[np.ndarray, np.ndarray]:
    mean = sample.mean(axis=0, dtype=np.float64).astype(np.float32)
    _, _, right = np.linalg.svd(np.asarray(sample - mean, dtype=np.float64),
                                full_matrices=False)
    return mean, right[:axes].astype(np.float32)


def train_shared(queries: np.ndarray, teacher_ids: np.ndarray,
                 documents: np.ndarray, mean: np.ndarray, w0: np.ndarray,
                 seed: int, epochs: int, negatives: int) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for shared projection training") from error
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    rng = np.random.default_rng(seed + 17)
    positives = teacher_ids[:, :10]
    query_rows = np.repeat(queries, positives.shape[1], axis=0)
    positive_rows = np.asarray(documents[positives.reshape(-1)], dtype=np.float32)
    total = len(query_rows)
    if teacher_ids.shape[1] > 10:
        hard_pool = teacher_ids[:, 10:]
        query_index = np.repeat(np.arange(len(queries)), positives.shape[1])
        choices = rng.integers(0, hard_pool.shape[1],
                               size=(total, negatives), dtype=np.int64)
        negative_ids = hard_pool[query_index[:, None], choices]
    else:
        negative_ids = rng.integers(0, len(documents), size=(total, negatives),
                                    dtype=np.int64)
    negative_rows = np.asarray(documents[negative_ids.reshape(-1)],
                               dtype=np.float32).reshape(total, negatives, -1)
    q = torch.from_numpy((query_rows - mean).astype(np.float32))
    p = torch.from_numpy((positive_rows - mean).astype(np.float32))
    n = torch.from_numpy((negative_rows - mean).astype(np.float32))
    parameter = torch.nn.Parameter(torch.from_numpy(w0.copy()))
    optimizer = torch.optim.AdamW([parameter], lr=0.01, weight_decay=1.0e-4)
    generator = torch.Generator().manual_seed(seed + 1)
    occupancy_sample = torch.from_numpy(
        np.asarray(documents[::128] - mean, dtype=np.float32))
    initial_std = torch.sqrt(torch.mean((occupancy_sample @
                                         torch.from_numpy(w0.T)) ** 2,
                                        dim=0) + 1.0e-6)
    losses: list[float] = []
    started = time.perf_counter()
    batch = 512
    for _ in range(epochs):
        order = torch.randperm(total, generator=generator)
        total_loss = 0.0
        for start in range(0, total, batch):
            selected = order[start:start + batch]
            current = parameter
            zq = q[selected] @ current.T
            zp = p[selected] @ current.T
            zn = n[selected] @ current.T
            positive_distance = torch.abs(zq - zp).sum(dim=1)
            negative_distance = torch.abs(zq[:, None, :] - zn).sum(dim=2)
            ranking = torch.nn.functional.softplus(
                positive_distance[:, None] - negative_distance + 0.25).mean()
            std = torch.sqrt(torch.mean((occupancy_sample @ current.T) ** 2,
                                        dim=0) + 1.0e-6)
            occupancy = torch.mean((std / (initial_std + 1.0e-6) - 1.0) ** 2)
            anchor = torch.mean((current - torch.from_numpy(w0)) ** 2)
            loss = ranking + 0.05 * occupancy + 0.01 * anchor
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * selected.numel()
        losses.append(total_loss / total)
    elapsed = time.perf_counter() - started
    return parameter.detach().numpy().astype(np.float32), {
        "seed": seed, "epochs": epochs, "negatives_per_positive": negatives,
        "initial_loss": losses[0], "final_loss": losses[-1],
        "training_seconds": elapsed, "objective": "softplus_l1_margin_plus_occupancy_anchor",
        "negative_source": "e5_ranks_11_1000" if teacher_ids.shape[1] > 10
                           else "uniform_documents",
    }


def ordinal_codes(vectors: np.ndarray, mean: np.ndarray, projection: np.ndarray,
                  cuts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    projected = (vectors - mean) @ projection.T
    codes = np.sum(projected[:, :, None] > cuts[None, :, :], axis=2,
                   dtype=np.uint8)
    return projected, codes


def mixed_id(code: np.ndarray, levels: int) -> int:
    value = 0
    multiplier = 1
    for level in code:
        value += int(level) * multiplier
        multiplier *= levels
    return value


def build_postings(codes: np.ndarray, projected: np.ndarray, cuts: np.ndarray,
                   levels: int) -> dict[int, np.ndarray]:
    lists: dict[int, list[int]] = {}
    for position, code in enumerate(codes):
        lists.setdefault(mixed_id(code, levels), []).append(position)
    return {key: np.asarray(value, dtype=np.int32)
            for key, value in lists.items()}


def probe(code: np.ndarray, projected: np.ndarray, cuts: np.ndarray,
          levels: int, count: int) -> list[int]:
    start = mixed_id(code, levels)
    queue: list[tuple[float, int, tuple[int, ...]]] = [(0.0, start,
                                                         tuple(map(int, code)))]
    seen = {start}
    result: list[int] = []
    while queue and len(result) < count:
        cost, cell, state = heapq.heappop(queue)
        result.append(cell)
        for axis, current in enumerate(state):
            for direction in (-1, 1):
                neighbour = current + direction
                if not 0 <= neighbour < levels:
                    continue
                next_state = list(state)
                next_state[axis] = neighbour
                next_cell = mixed_id(np.asarray(next_state, dtype=np.uint8), levels)
                if next_cell in seen:
                    continue
                seen.add(next_cell)
                boundary = min(current, neighbour)
                edge = abs(float(projected[axis]) - float(cuts[axis, boundary]))
                heapq.heappush(queue, (cost + edge, next_cell,
                                       tuple(next_state)))
    return result


def qrels_ndcg(selected: np.ndarray, ids: np.ndarray, scores: np.ndarray) -> float:
    valid = ids >= 0
    relevance = {int(doc): float(score) for doc, score in zip(ids[valid], scores[valid])}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0
                        for doc in selected[:10]], dtype=np.float64)
    ideal = np.sort(scores[valid].astype(np.float64))[::-1][:10]
    ideal_gains = 2.0 ** ideal - 1.0
    discount = 1.0 / np.log2(np.arange(2, len(gains) + 2))
    ideal_discount = 1.0 / np.log2(np.arange(2, len(ideal_gains) + 2))
    denominator = float(np.sum(ideal_gains * ideal_discount))
    return float(np.sum(gains * discount) / denominator) if denominator else 0.0


def evaluate(name: str, documents: np.ndarray, queries: np.ndarray,
             teacher_ids: np.ndarray, qrel_ids: np.ndarray,
             qrel_scores: np.ndarray, partitions: np.ndarray,
             query_projected: np.ndarray, query_codes: np.ndarray,
             postings: dict[int, np.ndarray], cuts: np.ndarray, levels: int,
             cell_budgets: list[int], document_budget: int,
             payload_bytes: int, model_bytes: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        for budget in cell_budgets:
            started = time.perf_counter()
            cells = probe(query_codes[index], query_projected[index], cuts,
                          levels, budget)
            parts = [postings[cell] for cell in cells if cell in postings]
            raw = sum(len(part) for part in parts)
            candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
            scores = documents[candidates] @ query if len(candidates) else np.empty(0, dtype=np.float32)
            selected = candidates[ordinal.top(scores, document_budget)]
            overlap = float(len(set(map(int, selected[:10])) & set(map(int, teacher_ids[index])))) / 10.0
            rows.append({"router": name, "partition": str(partitions[index]),
                         "query": index, "cell_budget": budget,
                         "document_budget": document_budget, "overlap": overlap,
                         "qrels_ndcg": qrels_ndcg(selected, qrel_ids[index], qrel_scores[index]),
                         "raw_postings": raw, "unique_candidates": int(len(candidates)),
                         "payload_bytes": payload_bytes, "model_bytes": model_bytes,
                         "route_ms": (time.perf_counter() - started) * 1000.0})
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("router", "partition", "cell_budget", "document_budget")
    result = []
    for key in sorted({tuple(row[name] for name in keys) for row in rows}):
        current = [row for row in rows if tuple(row[name] for name in keys) == key]
        result.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
                       "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
                       "mean_raw_postings": float(np.mean([r["raw_postings"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "payload_bytes": current[0]["payload_bytes"],
                       "model_bytes": current[0]["model_bytes"]})
    return result


def target_diagnostics(doc_codes: np.ndarray, teacher_ids: np.ndarray) -> dict[str, float]:
    unique_counts = []
    mode_masses = []
    bit_entropies = []
    for ids in teacher_ids:
        values = [mixed_id(doc_codes[int(doc)], 2) for doc in ids]
        counts = np.asarray([values.count(value) for value in set(values)], dtype=np.float64)
        unique_counts.append(float(len(counts)))
        mode_masses.append(float(np.max(counts) / len(values)))
        bits = np.asarray([doc_codes[int(doc)] for doc in ids], dtype=np.float64)
        probabilities = bits.mean(axis=0)
        entropy = -(probabilities * np.log2(np.maximum(probabilities, 1e-12)) +
                    (1.0 - probabilities) * np.log2(np.maximum(1.0 - probabilities, 1e-12)))
        bit_entropies.append(float(np.mean(entropy)))
    return {"mean_unique_teacher_addresses": float(np.mean(unique_counts)),
            "mean_teacher_address_mode_mass": float(np.mean(mode_masses)),
            "mean_bit_entropy": float(np.mean(bit_entropies))}


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    train_queries = np.asarray(data["train_queries"], dtype=np.float32)
    train_teacher_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    if args.train_teacher_ids is not None:
        train_teacher_ids = np.load(args.train_teacher_ids, allow_pickle=False)
    require(train_teacher_ids.shape[0] == len(train_queries),
            "hard-negative teacher query count differs")
    eval_queries = np.asarray(data["eval_queries"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    sample = documents[::4]
    rows: list[dict[str, Any]] = []
    training: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    specs = (("shared_8x3", 8, 3), ("shared_6x4", 6, 4),
             ("shared_12x2", 12, 2))
    base_mean, base_rows = pca_init(sample, 12)
    for name, axes, levels in specs:
        mean, pca_rows = base_mean, base_rows[:axes]
        pca_projected = (sample - mean) @ pca_rows.T
        cuts = np.quantile(pca_projected, np.arange(1, levels) / levels,
                           axis=0).T.astype(np.float32)
        if levels == 2:
            diagnostics["pca12_target"] = target_diagnostics(
                np.sum(((documents - mean) @ pca_rows.T)[:, :, None] >
                       cuts[None, :, :], axis=2, dtype=np.uint8),
                train_teacher_ids)
        # PCA control uses the identical query-specific threshold probing and
        # candidate evaluator as the learned shared projection below.
        pca_projected_documents, pca_codes = ordinal_codes(
            documents, mean, pca_rows, cuts)
        pca_projected_queries, pca_query_codes = ordinal_codes(
            eval_queries, mean, pca_rows, cuts)
        pca_postings = build_postings(pca_codes, pca_projected_documents,
                                      cuts, levels)
        payload_bytes = max(1, int(np.ceil(np.log2(levels ** axes) / 8.0)))
        model_bytes = int(mean.nbytes + pca_rows.nbytes + cuts.nbytes)
        pca_name = name.replace("shared_", "pca_")
        for partition in sorted(set(map(str, partitions))):
            positions = np.flatnonzero(partitions == partition)
            rows.extend(evaluate(
                pca_name, documents, eval_queries[positions],
                np.asarray(data["eval_teacher_ids"])[positions],
                np.asarray(data["eval_qrel_ids"])[positions],
                np.asarray(data["eval_qrel_scores"])[positions],
                np.asarray([partition] * len(positions)),
                pca_projected_queries[positions], pca_query_codes[positions],
                pca_postings, cuts, levels, args.cell_budgets,
                args.document_budget, payload_bytes, model_bytes))
        if name.startswith("shared"):
            learned_rows, metadata = train_shared(
                train_queries, train_teacher_ids, documents, mean, pca_rows,
                args.seed + axes, args.epochs, args.negatives)
            training[name] = metadata
            projected_documents, doc_codes = ordinal_codes(documents, mean,
                                                            learned_rows, cuts)
            projected_queries, query_codes = ordinal_codes(eval_queries, mean,
                                                            learned_rows, cuts)
            postings = build_postings(doc_codes, projected_documents, cuts,
                                      levels)
            for partition in sorted(set(map(str, partitions))):
                positions = np.flatnonzero(partitions == partition)
                rows.extend(evaluate(
                    name, documents, eval_queries[positions],
                    np.asarray(data["eval_teacher_ids"])[positions],
                    np.asarray(data["eval_qrel_ids"])[positions],
                    np.asarray(data["eval_qrel_scores"])[positions],
                    np.asarray([partition] * len(positions)),
                    projected_queries[positions], query_codes[positions],
                    postings, cuts, levels, args.cell_budgets,
                    args.document_budget, payload_bytes, model_bytes))
    return {"schema_version": 1,
            "family": "shared_supervised_ordinal_projection",
            "training": training, "target_diagnostics": diagnostics,
            "rows": rows, "summaries": summarize(rows),
            "protocol": {"thresholds": "train_document_quantiles",
                          "probing": "query_specific_threshold_distance",
                          "replication": 1, "document_budget": args.document_budget},
            "limitations": (["routing ceiling only; no downstream codec"] if train_teacher_ids.shape[1] > 10 else
                            ["train cache contains top-10 only; negatives are sampled uniformly, not E5 ranks 11-1000", "routing ceiling only; no downstream codec"])}


def self_test() -> None:
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(64, 12)).astype(np.float32)
    mean, projection = pca_init(vectors, 4)
    projected, codes = ordinal_codes(vectors, mean, projection,
                                     np.quantile((vectors - mean) @ projection.T,
                                                 [0.5], axis=0).T.astype(np.float32))
    postings = build_postings(codes, projected,
                              np.quantile((vectors - mean) @ projection.T,
                                          [0.5], axis=0).T.astype(np.float32), 2)
    require(postings and len(probe(codes[0], projected[0],
                                   np.quantile((vectors - mean) @ projection.T,
                                               [0.5], axis=0).T.astype(np.float32),
                                   2, 4)) == 4,
            "shared ordinal projection self-test differs")
    print("shared supervised ordinal projection self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cell-budgets", default="32,64,128,256")
    parser.add_argument("--document-budget", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--negatives", type=int, default=16)
    parser.add_argument("--train-teacher-ids", type=Path)
    parser.add_argument("--seed", type=int, default=20260906)
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
