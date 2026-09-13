#!/usr/bin/env python3
"""Run a whole-posting semantic routing scale gate on frozen DE-1M."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import MiniBatchKMeans
import sklearn


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "max": float(arr.max())}


def norm(values: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(lengths <= 1e-12):
        raise ValueError("zero-norm vector encountered")
    return values / lengths


def posting_stats(postings: list[np.ndarray]) -> dict[str, Any]:
    sizes = np.asarray([len(value) for value in postings], dtype=np.float64)
    return {"cells": int(len(postings)), "effective_cells": int(np.count_nonzero(sizes)),
            "empty_cells": int(np.count_nonzero(sizes == 0)),
            "size": aggregate(sizes.tolist())}


def build_postings(assignments: np.ndarray, k: int, document_ids: np.ndarray) -> list[np.ndarray]:
    order = np.argsort(assignments, kind="stable")
    sorted_cells = assignments[order]
    starts = np.searchsorted(sorted_cells, np.arange(k), side="left")
    ends = np.searchsorted(sorted_cells, np.arange(k), side="right")
    return [np.asarray(document_ids[order[starts[cell]:ends[cell]]], dtype=np.int32)
            for cell in range(k)]


def query_snapshot(postings: list[np.ndarray], cell_order: np.ndarray,
                   teachers: np.ndarray, teacher_cell_ranks: list[int],
                   teacher_cell_count: int, budgets: list[int], n: int,
                   mode: str) -> list[dict[str, Any]]:
    seen = np.zeros(n, dtype=np.bool_)
    selected = 0
    touched_entries = 0
    touched_postings = 0
    snapshots: list[dict[str, Any]] = []
    budget_index = 0
    for cell in cell_order:
        if budget_index >= len(budgets):
            break
        ids = postings[int(cell)]
        touched_postings += 1
        touched_entries += int(len(ids))
        fresh = ids[~seen[ids]]
        if mode == "whole_posting":
            seen[ids] = True
            selected += int(len(fresh))
        else:
            offset = 0
            while offset < len(fresh) and budget_index < len(budgets):
                remaining = budgets[budget_index] - selected
                take = min(max(remaining, 0), len(fresh) - offset)
                if take:
                    chosen = fresh[offset:offset + take]
                    seen[chosen] = True
                    selected += int(len(chosen))
                    offset += int(take)
                if selected < budgets[budget_index]:
                    break
                # The snapshot loop below advances budget_index; continue
                # consuming the same posting remainder for the next cap.
                present = np.count_nonzero(seen[teachers])
                snapshots.append({
                    "requested_candidate_budget": int(budgets[budget_index]),
                    "actual_unique_candidates": int(selected),
                    "budget_overshoot": int(selected - budgets[budget_index]),
                    "posting_entries_touched": int(touched_entries),
                    "postings_touched": int(touched_postings),
                    "teacher_recall": float(present / len(teachers)),
                    "teacher_ids_missed": [int(doc) for doc in teachers if not seen[int(doc)]],
                    "teacher_cell_ranks": teacher_cell_ranks,
                    "unique_teacher_cells": int(teacher_cell_count),
                    "mode": mode,
                })
                budget_index += 1
        while budget_index < len(budgets) and selected >= budgets[budget_index]:
            snapshots.append({
                "requested_candidate_budget": int(budgets[budget_index]),
                "actual_unique_candidates": int(selected),
                "budget_overshoot": int(selected - budgets[budget_index]),
                "posting_entries_touched": int(touched_entries),
                "postings_touched": int(touched_postings),
                "teacher_recall": float(np.count_nonzero(seen[teachers]) / len(teachers)),
                "teacher_ids_missed": [int(doc) for doc in teachers if not seen[int(doc)]],
                "teacher_cell_ranks": teacher_cell_ranks,
                "unique_teacher_cells": int(teacher_cell_count),
                "mode": mode,
            })
            budget_index += 1
    while budget_index < len(budgets):
        snapshots.append({
            "requested_candidate_budget": int(budgets[budget_index]),
            "actual_unique_candidates": int(selected),
            "budget_overshoot": int(selected - budgets[budget_index]),
            "posting_entries_touched": int(touched_entries),
            "postings_touched": int(touched_postings),
            "teacher_recall": float(np.count_nonzero(seen[teachers]) / len(teachers)),
            "teacher_ids_missed": [int(doc) for doc in teachers if not seen[int(doc)]],
            "teacher_cell_ranks": teacher_cell_ranks,
            "unique_teacher_cells": int(teacher_cell_count),
            "mode": mode,
        })
        budget_index += 1
    return snapshots

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=100_000)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--max-iter", type=int, default=20)
    parser.add_argument("--clusters", default="1024,2048,4096")
    parser.add_argument("--replications", default="1,2,4")
    parser.add_argument("--budgets", default="5000,10000,20000,50000,100000")
    parser.add_argument("--arms", default="l2")
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, dim = int(manifest["documents"]), int(manifest["dimension"])
    query_count = min(args.query_limit, int(manifest["queries"]))
    documents = np.memmap(manifest["references"]["document_vectors"]["path"], mode="r",
                          dtype="<f4", shape=(n, dim))
    queries = np.memmap(manifest["references"]["queries"]["path"], mode="r",
                        dtype="<f4", shape=(query_count, dim))
    teachers = np.memmap(manifest["references"]["teacher_ids"]["path"], mode="r",
                         dtype="<i8", shape=(query_count, 10))
    clusters = [int(x) for x in args.clusters.split(",") if x]
    replications = [int(x) for x in args.replications.split(",") if x]
    budgets = [int(x) for x in args.budgets.split(",") if x]
    arms = [x.strip() for x in args.arms.split(",") if x.strip()]
    if set(arms) - {"l2", "spherical"}:
        raise ValueError("unsupported arm")
    rng = np.random.default_rng(args.seed)
    sample_ids = np.sort(rng.choice(n, size=min(args.sample_size, n), replace=False))
    sample_raw = np.asarray(documents[sample_ids], dtype=np.float32)
    query_raw = np.asarray(queries, dtype=np.float32)
    raw_rows: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    for arm in arms:
        spherical = arm == "spherical"
        train = norm(sample_raw) if spherical else sample_raw
        query_values = norm(query_raw) if spherical else query_raw
        for k in clusters:
            fit_started = time.perf_counter()
            model = MiniBatchKMeans(n_clusters=k, random_state=args.seed,
                                    batch_size=args.batch_size, max_iter=args.max_iter,
                                    n_init=1, reassignment_ratio=0.0,
                                    init_size=max(k * 3, args.batch_size))
            model.fit(train)
            fit_ms = (time.perf_counter() - fit_started) * 1000.0
            centers = np.asarray(model.cluster_centers_, dtype=np.float32)
            if spherical:
                centers = norm(centers).astype(np.float32)
            assignment_started = time.perf_counter()
            routed = np.empty((n, max(replications)), dtype=np.int32)
            center_norm = np.sum(centers * centers, axis=1)
            for lo in range(0, n, args.batch_size):
                hi = min(lo + args.batch_size, n)
                batch = np.asarray(documents[lo:hi], dtype=np.float32)
                if spherical:
                    batch = norm(batch)
                scores = batch @ centers.T
                if not spherical:
                    scores = scores - 0.5 * center_norm
                ids = np.argpartition(-scores, kth=max(replications) - 1, axis=1)[:, :max(replications)]
                vals = np.take_along_axis(scores, ids, axis=1)
                stable = np.argsort(-vals, axis=1, kind="stable")
                routed[lo:hi] = np.take_along_axis(ids, stable, axis=1)
            assignment_ms = (time.perf_counter() - assignment_started) * 1000.0
            for replication in replications:
                document_ids = np.repeat(np.arange(n, dtype=np.int32), replication)
                postings = build_postings(routed[:, :replication].reshape(-1), k, document_ids)
                pstats = posting_stats(postings)
                query_started = time.perf_counter()
                config_raw: list[dict[str, Any]] = []
                for qi in range(query_count):
                    scores = centers @ query_values[qi]
                    if not spherical:
                        scores = scores - 0.5 * center_norm
                    order = np.lexsort((np.arange(k, dtype=np.int32), -scores))
                    rank_by_cell = np.empty(k, dtype=np.int32)
                    rank_by_cell[order] = np.arange(1, k + 1, dtype=np.int32)
                    teacher_docs = np.asarray(teachers[qi], dtype=np.int64)
                    assigned_cells = routed[teacher_docs, :replication]
                    teacher_ranks = np.min(rank_by_cell[assigned_cells], axis=1).astype(int).tolist()
                    teacher_cell_count = int(np.unique(assigned_cells).size)
                    for mode in ("whole_posting", "hard_cap"):
                        snapshots = query_snapshot(postings, order, teacher_docs,
                                                   teacher_ranks, teacher_cell_count,
                                                   budgets, n, mode)
                        for snap in snapshots:
                            config_raw.append({"arm": arm, "k": k, "replication": replication,
                                               "query": qi, **snap})
                query_ms = (time.perf_counter() - query_started) * 1000.0
                for mode in ("whole_posting", "hard_cap"):
                    for budget in budgets:
                        selected = [row for row in config_raw
                                    if row["mode"] == mode
                                    and row["requested_candidate_budget"] == budget]
                        rows.append({"arm": arm, "k": k, "replication": replication,
                                     "mode": mode, "requested_candidate_budget": budget,
                                     "query_count": query_count,
                                     "actual_unique_candidates": aggregate([float(x["actual_unique_candidates"]) for x in selected]),
                                     "budget_overshoot": aggregate([float(x["budget_overshoot"]) for x in selected]),
                                     "posting_entries_touched": aggregate([float(x["posting_entries_touched"]) for x in selected]),
                                     "postings_touched": aggregate([float(x["postings_touched"]) for x in selected]),
                                     "teacher_recall": aggregate([float(x["teacher_recall"]) for x in selected]),
                                     "teacher_cell_rank": aggregate([float(rank) for x in selected for rank in x["teacher_cell_ranks"]]),
                                     "worst_queries": sorted(({"query": int(x["query"]), "recall": float(x["teacher_recall"]),
                                                               "teacher_cell_ranks": x["teacher_cell_ranks"],
                                                               "teacher_ids_missed": x["teacher_ids_missed"]}
                                                              for x in selected), key=lambda x: (x["recall"], x["query"]))[:10]})
                raw_rows.extend(config_raw)
                models.append({"arm": arm, "k": k, "replication": replication,
                              "fit_ms": fit_ms, "full_assignment_ms": assignment_ms,
                              "query_generation_ms": query_ms, "posting_stats": pstats,
                              "center_sha256": hashlib.sha256(centers.tobytes()).hexdigest()})
    raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows},
                            separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    output = {"schema_version": 1, "family": "semantic_routing_scale_r4_gate_v1",
              "execution_status": "EXECUTED", "production_activation": False,
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "runner_sha256": sha256(Path(__file__)), "documents": n, "dimension": dim,
              "queries": query_count, "sample_size": len(sample_ids), "seed": args.seed,
              "sample_ids_sha256": hashlib.sha256(sample_ids.astype("<i8").tobytes()).hexdigest(),
              "sample_ids_generation": {"algorithm": "numpy.default_rng.choice", "population": n,
                                        "replace": False, "sort": True, "size": len(sample_ids)},
              "software": {"python": platform.python_version(), "numpy": np.__version__,
                           "scikit_learn": sklearn.__version__},
              "training": {"algorithm": "sklearn.MiniBatchKMeans", "batch_size": args.batch_size,
                           "max_iter": args.max_iter, "n_init": 1, "reassignment_ratio": 0.0},
              "arms": arms, "clusters": clusters, "replications": replications, "budgets": budgets,
              "models": models, "rows": rows,
              "raw_output": {"path": str(args.raw_output), "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                             "rows": len(raw_rows)},
              "protocol": {"primary_mode": "whole_posting", "secondary_mode": "hard_cap",
                           "posting_traversal": "single stable centroid order per query and replication",
                           "tie_break": "centroid score descending then cell id ascending",
                           "teacher_ids_used_for_index": False, "r4_comparator": "pending",
                           "payload_rerank": "not executed", "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()