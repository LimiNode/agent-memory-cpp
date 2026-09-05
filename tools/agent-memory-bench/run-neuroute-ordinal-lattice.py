#!/usr/bin/env python3
"""Replay Binary12 and learned ordinal lattice routers."""

from __future__ import annotations

import argparse
import heapq
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def top(scores: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(scores))
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    ids = np.argpartition(-scores, count - 1)[:count]
    return ids[np.lexsort((ids, -scores[ids]))]


def fit_projection(vectors: np.ndarray, axes: int) -> tuple[np.ndarray, np.ndarray]:
    mean = np.asarray(vectors.mean(axis=0), dtype=np.float32)
    _, _, vt = np.linalg.svd(np.asarray(vectors - mean, dtype=np.float32), full_matrices=False)
    require(axes <= vt.shape[0], "router axes exceed embedding dimensions")
    return mean, np.asarray(vt[:axes].T, dtype=np.float32)


def thresholds(values: np.ndarray, levels: int) -> np.ndarray:
    fractions = np.arange(1, levels, dtype=np.float32) / levels
    return np.quantile(values, fractions, axis=0).T.astype(np.float32)


def ordinal_values(vectors: np.ndarray, mean: np.ndarray,
                   projection: np.ndarray, cuts: np.ndarray) -> np.ndarray:
    projected = (vectors - mean) @ projection
    return np.sum(projected[:, :, None] > cuts[None, :, :], axis=2,
                  dtype=np.uint8)


def mixed_id(levels: np.ndarray, radix: int) -> int:
    value = 0
    multiplier = 1
    for level in levels:
        value += int(level) * multiplier
        multiplier *= radix
    return value


def build_postings(codes: np.ndarray, levels: int, replication: int,
                   projected: np.ndarray, cuts: np.ndarray
                   ) -> tuple[dict[int, np.ndarray], int]:
    postings: dict[int, list[int]] = {}
    for doc, code in enumerate(codes):
        ids = [mixed_id(code, levels)]
        if replication > 1:
            margins = []
            for axis, current in enumerate(code):
                boundaries = cuts[axis]
                value = float(projected[doc, axis])
                if current > 0:
                    margins.append((abs(value - float(boundaries[current - 1])), axis, -1))
                if current + 1 < levels:
                    margins.append((abs(value - float(boundaries[current])), axis, 1))
            margins.sort(key=lambda row: (row[0], row[1], row[2]))
            for _, axis, direction in margins[:replication - 1]:
                alternate = code.copy()
                alternate[axis] = np.uint8(int(alternate[axis]) + direction)
                ids.append(mixed_id(alternate, levels))
        for cell in ids:
            postings.setdefault(cell, []).append(doc)
    return {cell: np.asarray(documents, dtype=np.int64)
            for cell, documents in postings.items()}, sum(map(len, postings.values()))


def probe_cells(code: np.ndarray, levels: int, count: int,
                boundary_costs: np.ndarray) -> list[int]:
    start = mixed_id(code, levels)
    queue: list[tuple[float, int, tuple[int, ...]]] = [(0.0, start, tuple(map(int, code)))]
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
                next_tuple = tuple(next_state)
                next_cell = mixed_id(np.asarray(next_tuple, dtype=np.uint8), levels)
                if next_cell in seen:
                    continue
                seen.add(next_cell)
                edge = float(boundary_costs[axis, min(current, neighbour)])
                heapq.heappush(queue, (cost + edge, next_cell, next_tuple))
    return result


def binary_probe(code: np.ndarray, bits: int, count: int) -> list[int]:
    universe = 1 << bits
    require(universe <= 1 << 16, "binary router universe too large")
    values = np.arange(universe, dtype=np.uint32)
    distances = np.asarray([int(value ^ mixed_id(code, 2)).bit_count()
                            for value in values], dtype=np.int16)
    order = np.lexsort((values, distances))
    return [int(value) for value in order[:count]]


def evaluate_router(name: str, documents: np.ndarray, queries: np.ndarray,
                    teacher_ids: np.ndarray, teacher_scores: np.ndarray,
                    qrel_ids: np.ndarray | None, qrel_scores: np.ndarray | None,
                    partitions: np.ndarray, query_codes: np.ndarray,
                    postings: dict[int, np.ndarray], levels: int,
                    cell_budgets: list[int], document_budgets: list[int],
                    replication: int, payload_bytes: int, model_bytes: int,
                    binary: bool, boundary_costs: np.ndarray | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query_index, query in enumerate(queries):
        for cell_budget in cell_budgets:
            started = time.perf_counter()
            cells = (binary_probe(query_codes[query_index], 12, cell_budget)
                     if binary else probe_cells(query_codes[query_index], levels,
                                                cell_budget, boundary_costs))
            parts = [postings[cell] for cell in cells if cell in postings]
            raw_count = sum(len(part) for part in parts)
            candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int64)
            scores = documents[candidates] @ query if len(candidates) else np.empty(0, dtype=np.float32)
            for document_budget in document_budgets:
                selected = candidates[top(scores, document_budget)]
                target = teacher_ids[query_index]
                overlap = float(len(set(map(int, selected[:10])) & set(map(int, target)))) / 10.0
                relevance = {int(doc): float(score) for doc, score in zip(target, teacher_scores[query_index])}
                gains = np.asarray([relevance.get(int(doc), 0.0) for doc in selected[:10]], dtype=np.float64)
                ideal = np.sort(np.asarray(teacher_scores[query_index], dtype=np.float64))[::-1][:10]
                discount = 1.0 / np.log2(np.arange(2, len(gains) + 2))
                ideal_discount = 1.0 / np.log2(np.arange(2, len(ideal) + 2))
                denom = float(np.sum(ideal * ideal_discount))
                ndcg = float(np.sum(gains * discount) / denom) if denom else 0.0
                qrels_ndcg = None
                if qrel_ids is not None and qrel_scores is not None:
                    valid = qrel_ids[query_index] >= 0
                    relevance = {int(doc): float(score) for doc, score in
                                 zip(qrel_ids[query_index][valid],
                                     qrel_scores[query_index][valid])}
                    qrel_gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0
                                             for doc in selected[:10]], dtype=np.float64)
                    ideal_grades = np.sort(qrel_scores[query_index][valid].astype(np.float64))[::-1][:10]
                    ideal_gains = np.power(2.0, ideal_grades) - 1.0
                    ideal_qrel_discount = 1.0 / np.log2(np.arange(2, len(ideal_gains) + 2))
                    ideal_qrel = float(np.sum(ideal_gains * ideal_qrel_discount))
                    qrels_ndcg = float(np.sum(qrel_gains * discount) / ideal_qrel) if ideal_qrel else 0.0
                rows.append({"router": name, "replication": replication,
                             "partition": str(partitions[query_index]),
                             "query": query_index, "cell_budget": cell_budget,
                             "document_budget": document_budget, "overlap": overlap,
                             "ndcg": ndcg, "qrels_ndcg": qrels_ndcg,
                             "raw_postings": raw_count,
                             "unique_candidates": int(len(candidates)),
                             "payload_bytes": payload_bytes, "model_bytes": model_bytes,
                             "route_ms": (time.perf_counter() - started) * 1000.0})
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("router", "replication", "partition", "cell_budget", "document_budget")
    result = []
    for key in sorted({tuple(row[name] for name in keys) for row in rows}):
        current = [row for row in rows if tuple(row[name] for name in keys) == key]
        result.append({**dict(zip(keys, key)), "query_count": len(current),
                       "mean_overlap": float(np.mean([r["overlap"] for r in current])),
                       "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
                       "worst_overlap": float(np.min([r["overlap"] for r in current])),
                       "mean_ndcg": float(np.mean([r["ndcg"] for r in current])),
                       "worst_ndcg": float(np.min([r["ndcg"] for r in current])),
                       "mean_qrels_ndcg": (float(np.mean([r["qrels_ndcg"] for r in current]))
                           if current[0]["qrels_ndcg"] is not None else None),
                       "worst_qrels_ndcg": (float(np.min([r["qrels_ndcg"] for r in current]))
                           if current[0]["qrels_ndcg"] is not None else None),
                       "mean_raw_postings": float(np.mean([r["raw_postings"] for r in current])),
                       "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
                       "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
                       "payload_bytes": current[0]["payload_bytes"],
                       "model_bytes": current[0]["model_bytes"]})
    return result


def load_input(path: Path) -> dict[str, np.ndarray]:
    if path.suffix.lower() == ".json":
        manifest = json.loads(path.read_text(encoding="utf-8"))
        require(manifest.get("family") == "neuroute_ordinal_lattice_input",
                "ordinal lattice manifest family differs")
        root = path.parent
        result = {name: np.load(root / row["path"], mmap_mode="r",
                                allow_pickle=False)
                  for name, row in manifest["outputs"].items()
                  if name not in ("train_document_positions",)}
        source = manifest["source"]
        result["eval_vectors"] = np.memmap(
            Path(source["document_vectors"]), mode="r", dtype="<f4",
            shape=(int(source["document_count"]), int(source["dimension"])))
        return result
    archive = np.load(path, allow_pickle=False)
    required = ("train_vectors", "train_queries", "eval_vectors", "eval_queries",
                "eval_teacher_ids", "eval_teacher_scores")
    missing = [name for name in required if name not in archive]
    require(not missing, "ordinal lattice input missing: " + ", ".join(missing))
    result = {name: np.asarray(archive[name]) for name in required}
    result["eval_partition"] = np.asarray(archive["eval_partition"] if "eval_partition" in archive else np.full(len(result["eval_queries"]), "all", dtype="U5"))
    if "eval_qrel_ids" in archive and "eval_qrel_scores" in archive:
        result["eval_qrel_ids"] = np.asarray(archive["eval_qrel_ids"])
        result["eval_qrel_scores"] = np.asarray(archive["eval_qrel_scores"])
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_input(args.input)
    train = np.asarray(data["train_vectors"], dtype=np.float32)
    documents = np.asarray(data["eval_vectors"], dtype=np.float32)
    queries = np.asarray(data["eval_queries"], dtype=np.float32)
    partitions = np.asarray(data["eval_partition"])
    rows: list[dict[str, Any]] = []
    specs = (("binary12", 12, 2), ("ordinal8x3", 8, 3),
             ("ordinal6x4", 6, 4), ("ordinal4x8", 4, 8),
             ("ordinal10x3", 10, 3))
    for name, axes, levels in specs:
        mean, projection = fit_projection(train, axes)
        doc_projected = (documents - mean) @ projection
        cuts = thresholds((train - mean) @ projection, levels)
        doc_codes = ordinal_values(documents, mean, projection, cuts)
        query_codes = ordinal_values(queries, mean, projection, cuts)
        boundary_costs = np.ones((axes, levels), dtype=np.float32)
        for axis in range(axes):
            values = np.diff(cuts[axis]) if levels > 2 else np.asarray([1.0])
            boundary_costs[axis, :len(values)] = np.maximum(values, 1e-6)
        fixed_bits = axes * int(np.ceil(np.log2(levels)))
        base_bits = int(np.ceil(np.log2(levels ** axes)))
        payload = (fixed_bits + 7) // 8 if args.storage == "fixed" else (base_bits + 7) // 8
        model_bytes = int(mean.nbytes + projection.nbytes + cuts.nbytes)
        for replication in args.replication:
            postings, _ = build_postings(doc_codes, levels, replication, doc_projected, cuts)
            for partition in sorted(set(map(str, partitions))):
                positions = np.flatnonzero(partitions == partition)
                rows.extend(evaluate_router(name, documents, queries[positions],
                    data["eval_teacher_ids"][positions], data["eval_teacher_scores"][positions],
                    (data.get("eval_qrel_ids")[positions] if "eval_qrel_ids" in data else None),
                    (data.get("eval_qrel_scores")[positions] if "eval_qrel_scores" in data else None),
                    np.asarray([partition] * len(positions)), query_codes[positions], postings,
                    levels, args.cell_budgets, args.document_budgets, replication,
                    payload, model_bytes, levels == 2, boundary_costs))
    return {"schema_version": 1, "family": "neuroute_learned_ordinal_lattice_router",
            "storage": args.storage, "rows": rows, "summaries": summarize(rows),
            "teacher_projection_leakage": False, "global_scan_product_path": False}


def smoke() -> None:
    rng = np.random.default_rng(19)
    vectors = rng.normal(size=(96, 12)).astype(np.float32)
    queries = vectors[:8] + rng.normal(scale=.03, size=(8, 12)).astype(np.float32)
    teacher_ids = np.asarray([top(vectors @ query, 10) for query in queries])
    teacher_scores = np.asarray([vectors @ query for query in queries], dtype=np.float32)
    path = Path(".ordinal-lattice-smoke.npz")
    np.savez(path, train_vectors=vectors, train_queries=queries[:4],
             eval_vectors=vectors, eval_queries=queries,
             eval_teacher_ids=teacher_ids, eval_teacher_scores=teacher_scores,
             eval_partition=np.asarray(["config"] * 4 + ["internal"] * 4))
    args = argparse.Namespace(input=path, storage="base_l", replication=[1, 2],
                              cell_budgets=[8, 16], document_budgets=[16])
    report = run(args)
    require(report["summaries"], "ordinal lattice smoke produced no rows")
    require(not report["teacher_projection_leakage"], "ordinal lattice smoke leakage")
    path.unlink(missing_ok=True)
    print("ordinal lattice router self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--storage", choices=("fixed", "base_l"), default="base_l")
    parser.add_argument("--cell-budgets", default="32,64,128,256")
    parser.add_argument("--document-budgets", default="256,512,768,1024,2048")
    parser.add_argument("--replication", default="1,2,3,4")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        smoke(); return 0
    require(args.input is not None and args.output is not None,
            "--input and --output are required")
    args.cell_budgets = [int(value) for value in args.cell_budgets.split(",")]
    args.document_budgets = [int(value) for value in args.document_budgets.split(",")]
    args.replication = [int(value) for value in args.replication.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
