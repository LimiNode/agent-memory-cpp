#!/usr/bin/env python3
"""Research-only IVF training comparison scaffold.

The runner executes the ordinary Faiss Lloyd baseline when its inputs and
dependencies are present. E-means and O-means are reported as NOT_RUN until a
pinned adapter is supplied; the script never substitutes Lloyd output for an
unavailable method. No result from this file is a production retrieval claim.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any


def provenance() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "status": "NOT_RUN",
    }
    for package in ("numpy", "faiss-cpu", "faiss-gpu"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def configuration(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
    }


def not_run(reason: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": "ivf-training-comparison-v1",
        "status": "NOT_RUN",
        "reason": reason,
        "provenance": provenance(),
        "configuration": configuration(args),
        "methods": {
            "lloyd": {"status": "NOT_RUN"},
            "e_means": {"status": "NOT_RUN"},
            "o_means": {"status": "NOT_RUN"},
        },
    }


def load_matrix(path: Path, name: str):
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("numpy is required for a measured run") from exc
    matrix = np.load(path, allow_pickle=False)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise RuntimeError(f"{name} must be a non-empty two-dimensional .npy matrix")
    if not np.isfinite(matrix).all():
        raise RuntimeError(f"{name} contains NaN or infinity")
    return matrix.astype("float32", copy=False)


def run_lloyd(train, queries, args: argparse.Namespace) -> dict[str, Any]:
    try:
        import faiss
        import numpy as np
    except ImportError as exc:
        return {"status": "NOT_RUN", "reason": f"Faiss or NumPy unavailable: {exc}"}

    if args.nlist <= 0 or args.nlist > train.shape[0]:
        return {"status": "NOT_RUN", "reason": "nlist must be in [1, training_rows]"}

    started = time.perf_counter()
    kmeans = faiss.Kmeans(
        train.shape[1],
        args.nlist,
        niter=args.iterations,
        nredo=args.nredo,
        seed=args.seed,
        verbose=False,
        gpu=False,
    )
    kmeans.train(train)
    elapsed = time.perf_counter() - started
    centroids = np.asarray(kmeans.centroids, dtype="float32").reshape(args.nlist, train.shape[1])
    _, assignments = faiss.knn(train, centroids, 1)
    assignments = assignments[:, 0]
    counts = np.bincount(assignments, minlength=args.nlist)

    quantizer = faiss.IndexFlatL2(train.shape[1])
    index = faiss.IndexIVFFlat(quantizer, train.shape[1], args.nlist, faiss.METRIC_L2)
    index.train(train)
    index.add(train)
    index.nprobe = min(args.nprobe, args.nlist)
    query_started = time.perf_counter()
    distances, ids = index.search(queries, min(args.top_k, train.shape[0]))
    query_elapsed = time.perf_counter() - query_started
    del distances, ids

    held_out = queries
    _, held_out_assignments = faiss.knn(held_out, centroids, 1)
    held_out_distances = np.min(
        ((held_out[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2), axis=1
    )
    return {
        "status": "MEASURED",
        "trainer": "faiss.Kmeans",
        "training_seconds": elapsed,
        "query_seconds": query_elapsed,
        "held_out_mse": float(held_out_distances.mean()),
        "cluster_size_min": int(counts.min()),
        "cluster_size_max": int(counts.max()),
        "empty_clusters": int((counts == 0).sum()),
        "nprobe": int(index.nprobe),
        "top_k": int(args.top_k),
        "query_count": int(queries.shape[0]),
        "assignment_checksum": int(np.asarray(held_out_assignments).sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nlist", type=int, default=256)
    parser.add_argument("--nprobe", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--nredo", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261118)
    args = parser.parse_args()

    if not args.train or not args.queries:
        receipt = not_run("training and query .npy inputs were not supplied", args)
    else:
        try:
            train = load_matrix(args.train, "train")
            queries = load_matrix(args.queries, "queries")
            if train.shape[1] != queries.shape[1]:
                raise RuntimeError("train and queries must have the same dimension")
            measured_provenance = provenance()
            measured_provenance["status"] = "PARTIAL"
            receipt = {
                "schema": "ivf-training-comparison-v1",
                "status": "PARTIAL",
                "provenance": measured_provenance,
                "configuration": configuration(args),
                "corpus": {
                    "train": str(args.train),
                    "queries": str(args.queries),
                    "train_rows": int(train.shape[0]),
                    "query_rows": int(queries.shape[0]),
                    "dimension": int(train.shape[1]),
                },
                "methods": {
                    "lloyd": run_lloyd(train, queries, args),
                    "e_means": {
                        "status": "NOT_RUN",
                        "reason": "no pinned E-means adapter supplied",
                    },
                    "o_means": {
                        "status": "NOT_RUN",
                        "reason": "Faiss PR #5917 adapter at commit 9ecbf0c2 was not supplied",
                    },
                },
            }
        except (OSError, RuntimeError, ValueError) as exc:
            receipt = not_run(str(exc), args)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
