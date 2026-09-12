#!/usr/bin/env python3
"""Evaluate explicit L2 and spherical semantic posting oracles.

The runner deliberately keeps the Git receipt aggregate-only.  Pass
``--raw-output`` when a per-query artifact is required outside Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
from sklearn.cluster import MiniBatchKMeans
import sklearn


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_stats(values: np.ndarray) -> dict[str, float]:
    norms = np.linalg.norm(values, axis=1).astype(np.float64)
    return {"min": float(norms.min()), "p50": float(np.percentile(norms, 50)),
            "p95": float(np.percentile(norms, 95)), "max": float(norms.max())}


def normalize(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("zero-norm vector encountered in spherical arm")
    return values / norms


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"mean": float(array.mean()), "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)), "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--sample-size", type=int, default=100_000)
    parser.add_argument("--query-limit", type=int, default=152)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--max-iter", type=int, default=100)
    parser.add_argument("--clusters", default="64,128,256,512")
    parser.add_argument("--replications", default="1,2,4")
    parser.add_argument("--nprobe", default="1,2,4,8,16,32")
    parser.add_argument("--arms", default="l2,spherical")
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, dim = int(manifest["documents"]), int(manifest["dimension"])
    query_count = min(args.query_limit, int(manifest["queries"]))
    documents = np.memmap(manifest["references"]["document_vectors"]["path"], mode="r",
                          dtype="<f4", shape=(n, dim))
    queries_raw = np.memmap(manifest["references"]["queries"]["path"], mode="r",
                            dtype="<f4", shape=(query_count, dim))
    teachers = np.memmap(manifest["references"]["teacher_ids"]["path"], mode="r",
                         dtype="<i8", shape=(query_count, 10))
    clusters = [int(value) for value in args.clusters.split(",")]
    replications = [int(value) for value in args.replications.split(",")]
    nprobes = [int(value) for value in args.nprobe.split(",")]
    arms = [value.strip() for value in args.arms.split(",") if value.strip()]
    if set(arms) - {"l2", "spherical"}:
        raise ValueError("arms must be l2 and/or spherical")
    rng = np.random.default_rng(args.seed)
    sample_ids = np.sort(rng.choice(n, size=min(args.sample_size, n), replace=False))
    sample_raw = np.asarray(documents[sample_ids], dtype=np.float32)
    queries_raw_array = np.asarray(queries_raw, dtype=np.float32)
    raw_rows: list[dict[str, object]] = []
    models: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    for arm in arms:
        spherical = arm == "spherical"
        sample = normalize(sample_raw) if spherical else sample_raw
        query_values = normalize(queries_raw_array) if spherical else queries_raw_array
        arm_norms = {"documents_sample": norm_stats(sample_raw),
                     "queries": norm_stats(queries_raw_array)}
        for k in clusters:
            if k <= 0 or k > n:
                raise ValueError("cluster count out of range")
            fit_started = time.perf_counter()
            model = MiniBatchKMeans(n_clusters=k, random_state=args.seed, batch_size=args.batch_size,
                                    max_iter=args.max_iter, n_init=1, reassignment_ratio=0.0,
                                    init_size=max(k * 3, args.batch_size))
            model.fit(sample)
            fit_ms = (time.perf_counter() - fit_started) * 1000.0
            centers = np.asarray(model.cluster_centers_, dtype=np.float32)
            if spherical:
                centers = normalize(centers).astype(np.float32)
            arm_norms["centroids"] = norm_stats(centers)
            assignment_started = time.perf_counter()
            max_replication = max(replications)
            routed = np.empty((n, max_replication), dtype=np.int32)
            for lo in range(0, n, args.batch_size):
                hi = min(lo + args.batch_size, n)
                batch = np.asarray(documents[lo:hi], dtype=np.float32)
                if spherical:
                    batch = normalize(batch)
                if arm == "l2":
                    scores = batch @ centers.T - 0.5 * np.sum(centers * centers, axis=1)
                else:
                    scores = batch @ centers.T
                ids = np.argpartition(-scores, kth=max_replication - 1, axis=1)[:, :max_replication]
                vals = np.take_along_axis(scores, ids, axis=1)
                stable_order = np.argsort(-vals, axis=1, kind="stable")
                routed[lo:hi] = np.take_along_axis(ids, stable_order, axis=1)
            assignment_ms = (time.perf_counter() - assignment_started) * 1000.0
            posting_started = time.perf_counter()
            postings_by_replication = {}
            for replication in replications:
                postings_by_replication[replication] = [
                    np.flatnonzero(np.any(routed[:, :replication] == cell, axis=1))
                    for cell in range(k)]
            posting_build_ms = (time.perf_counter() - posting_started) * 1000.0
            query_started = time.perf_counter()
            for replication in replications:
                for nprobe in nprobes:
                    postings = postings_by_replication[replication]
                    candidate_values: list[float] = []
                    posting_values: list[float] = []
                    recalls: list[float] = []
                    ranks_all: list[float] = []
                    fractions: list[float] = []
                    for qi in range(query_count):
                        scores = centers @ query_values[qi]
                        if arm == "l2":
                            scores = scores - 0.5 * np.sum(centers * centers, axis=1)
                        order = np.lexsort((np.arange(k, dtype=np.int32), -scores))
                        probe = order[:min(nprobe, k)]
                        pieces = [postings[int(cell)] for cell in probe]
                        union = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype=np.int64)
                        touched = int(sum(len(piece) for piece in pieces))
                        recall = float(np.isin(teachers[qi], union).sum() / 10.0)
                        cell_positions = {int(cell): rank + 1 for rank, cell in enumerate(order)}
                        ranks = [min(cell_positions[int(cell)] for cell in routed[int(doc), :replication])
                                 for doc in teachers[qi]]
                        candidate_values.append(float(len(union))); posting_values.append(float(touched))
                        recalls.append(recall); ranks_all.extend(ranks)
                        fractions.append(float(touched / (n * replication)))
                        raw_rows.append({"arm": arm, "k": k, "replication": replication,
                                         "query": qi, "nprobe": nprobe,
                                         "candidate_docs": int(len(union)),
                                         "posting_entries_touched": touched,
                                         "teacher_recall": recall,
                                         "teacher_cell_rank_max": int(max(ranks))})
                    rows.append({"arm": arm, "k": k, "replication": replication, "nprobe": nprobe,
                                 "query_count": query_count,
                                 "index_posting_entries": int(n * replication),
                                 "index_entries_per_document": replication,
                                 "logical_posting_id_bytes": int(n * replication * 4),
                                 "candidate_docs": aggregate(candidate_values),
                                 "posting_entries_touched": aggregate(posting_values),
                                 "teacher_recall": aggregate(recalls),
                                 "fraction_recall_1": float(np.mean(np.asarray(recalls) >= 1.0)),
                                 "fraction_recall_ge_0_9": float(np.mean(np.asarray(recalls) >= 0.9)),
                                 "teacher_cell_rank": aggregate(ranks_all),
                                 "query_posting_entry_fraction": aggregate(fractions)})
            query_generation_ms = (time.perf_counter() - query_started) * 1000.0
            models.append({"arm": arm, "k": k, "fit_ms": fit_ms,
                          "full_assignment_ms": assignment_ms,
                          "posting_build_ms": posting_build_ms,
                          "query_generation_ms": query_generation_ms,
                          "total_experiment_ms": fit_ms + assignment_ms + posting_build_ms + query_generation_ms,
                          "center_sha256": hashlib.sha256(centers.tobytes()).hexdigest(),
                          "norm_stats": arm_norms})
    output = {"schema_version": 2, "family": "semantic_kmeans_routing_oracle_corrected_v1",
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "runner_sha256": sha256(Path(__file__)), "documents": n, "dimension": dim,
              "queries": query_count, "sample_size": len(sample_ids),
              "sample_ids_sha256": hashlib.sha256(sample_ids.astype("<i8").tobytes()).hexdigest(),
              "seed": args.seed, "sample_ids_generation": {"algorithm": "numpy.default_rng.choice",
              "replace": False, "sort": True, "population": n, "size": len(sample_ids)},
              "software": {"python": platform.python_version(), "numpy": np.__version__,
                           "scikit_learn": sklearn.__version__},
              "training": {"algorithm": "sklearn.MiniBatchKMeans", "batch_size": args.batch_size,
                           "max_iter": args.max_iter, "n_init": 1,
                           "reassignment_ratio": 0.0, "init_size": max(max(clusters) * 3, args.batch_size)},
              "arms": arms, "clusters": clusters, "replications": replications,
              "nprobe": nprobes, "matched_query_ids": list(range(min(8, query_count))),
              "models": models, "rows": rows,
              "protocol": {"l2_assignment": "argmax(x dot c - 0.5 ||c||^2)",
                           "spherical_assignment": "L2-normalized vectors and centroids, cosine dot product",
                           "posting_union": "deduplicated document IDs", "teacher_ids_used_for_index": False,
                           "payload_rerank": "not executed", "candidate_budget": "fixed nprobe; no hard candidate cap",
                           "production_activation": False}, "execution_status": "EXECUTED",
              "production_activation": False}
    if args.raw_output:
        args.raw_output.parent.mkdir(parents=True, exist_ok=True)
        raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows},
                                separators=(",", ":")) + "\n").encode("utf-8")
        args.raw_output.write_bytes(raw_bytes)
        output["raw_output"] = {"path": str(args.raw_output),
                                 "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                                 "rows": len(raw_rows)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

