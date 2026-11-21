#!/usr/bin/env python3
"""Research-only IVF training comparison scaffold.

The runner executes the ordinary Faiss Lloyd baseline when its inputs and
dependencies are present. E-means and O-means are reported as
``NOT_EXECUTED`` until a pinned adapter and corpus are supplied; the script
never substitutes Lloyd output for an unavailable method. No result from this
file is a production retrieval claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any


OMEANS_REFERENCE = {
    "project": "faiss",
    "pull_request": 5917,
    "commit": "9ecbf0c2a2f4ca7a771b552badc145584b09e1a6",
}


def provenance() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "status": "NOT_EXECUTED",
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
        if key != "output"
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def centroid_digest(centroids: Any) -> str:
    """Return a stable digest of the exact float32 centroid bytes."""

    import numpy as np

    canonical = np.asarray(centroids, dtype="<f4", order="C")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def distance_workspace_bound(
    query_count: int, centroid_count: int, dimension: int, block_size: int
) -> dict[str, int]:
    """Describe the bounded temporary distance tile.

    The implementation evaluates one ``query_tile x centroid_tile x
    dimension`` block at a time.  The byte counts are an explicit upper bound
    for the float64 distance tensor and its two arithmetic temporaries; they
    exclude NumPy allocator overhead and the result vectors.
    """

    if query_count <= 0 or centroid_count <= 0 or dimension <= 0:
        raise ValueError("query, centroid, and dimension counts must be positive")
    if block_size <= 0:
        raise ValueError("distance block size must be positive")
    query_tile = min(query_count, block_size)
    centroid_tile = min(centroid_count, block_size)
    tensor_elements = query_tile * centroid_tile * dimension
    tensor_bytes = tensor_elements * 8
    return {
        "query_tile": query_tile,
        "centroid_tile": centroid_tile,
        "dimension": dimension,
        "distance_tensor_elements": tensor_elements,
        "distance_tensor_bytes": tensor_bytes,
        "estimated_arithmetic_tensor_bytes": tensor_bytes * 2,
        "estimated_peak_intermediate_bytes": tensor_bytes * 3,
    }


def not_executed(reason: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": "ivf-training-comparison-v2",
        "status": "NOT_EXECUTED",
        "reason": reason,
        "provenance": provenance(),
        "configuration": configuration(args),
        "reference": {"o_means": OMEANS_REFERENCE},
        "methods": {
            "lloyd": {"status": "NOT_EXECUTED"},
            "e_means": {
                "status": "NOT_EXECUTED",
                "reason": "no pinned E-means adapter and corpus supplied",
            },
            "o_means": {
                "status": "NOT_EXECUTED",
                "reason": "no pinned O-means adapter and corpus supplied",
                "reference": OMEANS_REFERENCE,
            },
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


def nearest_centroids_bounded(vectors, centroids, block_size: int):
    """Find nearest centroids without materialising a Q x K x D tensor.

    The work is still O(Q*K*D), as it must be for an exact Lloyd assignment,
    but both query and centroid blocks are bounded. Equal distances resolve to
    the lowest centroid id, making the receipt deterministic.
    """

    import numpy as np

    if vectors.ndim != 2 or centroids.ndim != 2 or vectors.shape[1] != centroids.shape[1]:
        raise ValueError("vectors and centroids must be two-dimensional with equal dimension")
    distance_workspace_bound(
        vectors.shape[0], centroids.shape[0], vectors.shape[1], block_size
    )

    best_distances = np.full(vectors.shape[0], np.inf, dtype="float64")
    best_ids = np.full(vectors.shape[0], centroids.shape[0], dtype="int64")
    for q_start in range(0, vectors.shape[0], block_size):
        q_stop = min(q_start + block_size, vectors.shape[0])
        queries = vectors[q_start:q_stop].astype("float64", copy=False)
        local_distances = np.full(queries.shape[0], np.inf, dtype="float64")
        local_ids = np.full(queries.shape[0], centroids.shape[0], dtype="int64")
        for c_start in range(0, centroids.shape[0], block_size):
            c_stop = min(c_start + block_size, centroids.shape[0])
            current = centroids[c_start:c_stop].astype("float64", copy=False)
            distances = ((queries[:, None, :] - current[None, :, :]) ** 2).sum(axis=2)
            candidate_ids = np.arange(c_start, c_stop, dtype="int64")
            candidate_index = distances.argmin(axis=1)
            candidate_distances = distances[np.arange(distances.shape[0]), candidate_index]
            candidate_id = candidate_ids[candidate_index]
            replace = (candidate_distances < local_distances) | (
                (candidate_distances == local_distances) & (candidate_id < local_ids)
            )
            local_distances[replace] = candidate_distances[replace]
            local_ids[replace] = candidate_id[replace]
        best_distances[q_start:q_stop] = local_distances
        best_ids[q_start:q_stop] = local_ids
    return best_distances, best_ids


def build_pretrained_index(faiss, train, centroids):
    """Build an IVF index from supplied centroids without retraining it."""

    if train.ndim != 2 or centroids.ndim != 2:
        raise ValueError("train and centroids must be two-dimensional")
    if train.shape[0] == 0 or centroids.shape[0] == 0 or train.shape[1] == 0:
        raise ValueError("train and centroids must be non-empty")
    if train.shape[1] != centroids.shape[1]:
        raise ValueError("train and centroids must have equal dimension")

    quantizer = faiss.IndexFlatL2(train.shape[1])
    quantizer.add(centroids)
    index = faiss.IndexIVFFlat(
        quantizer, train.shape[1], centroids.shape[0], faiss.METRIC_L2
    )
    # The quantizer is already populated with trained centroids. Calling
    # IndexIVFFlat.train() here would silently replace them, so mark this
    # caller-owned layout as trained and only add vectors below.
    index.is_trained = True
    return index


def quantizer_centroids(index, count: int):
    import numpy as np

    try:
        return np.asarray(index.quantizer.reconstruct_n(0, count), dtype="float32")
    except (AttributeError, RuntimeError, TypeError):
        rows = [index.quantizer.reconstruct(i) for i in range(count)]
        return np.asarray(rows, dtype="float32")


def verify_installed_centroids(index, centroids):
    """Reject an IVF quantizer whose installed centroids are not identical."""

    import numpy as np

    installed = quantizer_centroids(index, int(centroids.shape[0]))
    installed_digest = centroid_digest(installed)
    expected_digest = centroid_digest(centroids)
    if installed_digest != expected_digest or not np.array_equal(installed, centroids):
        raise RuntimeError(
            "installed IVF quantizer centroids differ from Lloyd output"
        )
    return installed, installed_digest


def run_lloyd(
    train, queries, args: argparse.Namespace, train_digest: str, query_digest: str
) -> dict[str, Any]:
    try:
        import faiss
        import numpy as np
    except ImportError as exc:
        return {"status": "NOT_EXECUTED", "reason": f"Faiss or NumPy unavailable: {exc}"}

    if args.nlist <= 0 or args.nlist > train.shape[0]:
        return {"status": "NOT_EXECUTED", "reason": "nlist must be in [1, training_rows]"}
    if args.nprobe <= 0 or args.top_k <= 0:
        return {"status": "NOT_EXECUTED", "reason": "nprobe and top_k must be positive"}
    if args.distance_block_size <= 0:
        return {"status": "NOT_EXECUTED", "reason": "distance block size must be positive"}
    if args.min_training_vectors_per_centroid <= 0:
        return {
            "status": "NOT_EXECUTED",
            "reason": "minimum training vectors per centroid must be positive",
        }

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
    trained_digest = centroid_digest(centroids)
    _, assignments = nearest_centroids_bounded(train, centroids, args.distance_block_size)
    workspace = distance_workspace_bound(
        train.shape[0], centroids.shape[0], train.shape[1], args.distance_block_size
    )
    counts = np.bincount(assignments, minlength=args.nlist)
    if int(counts.min()) < args.min_training_vectors_per_centroid:
        return {
            "status": "NOT_EXECUTED",
            "reason": "training vectors per centroid below configured minimum",
            "cluster_size_min": int(counts.min()),
            "minimum_training_vectors_per_centroid": int(args.min_training_vectors_per_centroid),
            "centroid_digest": trained_digest,
        }

    # This is deliberately the only index construction path: trained
    # centroids are installed into the quantizer and IndexIVFFlat.train() is
    # never called a second time.
    index = build_pretrained_index(faiss, train, centroids)
    try:
        installed, installed_digest = verify_installed_centroids(index, centroids)
    except RuntimeError:
        return {
            "status": "NOT_EXECUTED",
            "reason": "installed IVF quantizer centroids differ from Lloyd output",
            "centroid_digest": trained_digest,
            "installed_centroid_digest": installed_digest,
        }
    index.add(train)
    index.nprobe = min(args.nprobe, args.nlist)
    query_started = time.perf_counter()
    distances, ids = index.search(queries, min(args.top_k, train.shape[0]))
    query_elapsed = time.perf_counter() - query_started
    del distances, ids

    held_out_distances, held_out_assignments = nearest_centroids_bounded(
        queries, centroids, args.distance_block_size
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
        "minimum_training_vectors_per_centroid": int(args.min_training_vectors_per_centroid),
        "nprobe": int(index.nprobe),
        "top_k": int(args.top_k),
        "query_count": int(queries.shape[0]),
        "assignment_checksum": int(np.asarray(held_out_assignments).sum()),
        "centroid_digest": trained_digest,
        "installed_centroid_digest": installed_digest,
        "train_input_digest": train_digest,
        "query_input_digest": query_digest,
        "distance_block_size": int(args.distance_block_size),
        "distance_workspace_bound": workspace,
        "mutation_controls": {
            "second_index_train": "forbidden_by_construction",
            "centroid_digest_mismatch": "fail_closed",
        },
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
    parser.add_argument("--distance-block-size", type=int, default=256)
    parser.add_argument("--min-training-vectors-per-centroid", type=int, default=1)
    args = parser.parse_args()

    if not args.train or not args.queries:
        receipt = not_executed("training and query .npy inputs were not supplied", args)
    else:
        try:
            train = load_matrix(args.train, "train")
            queries = load_matrix(args.queries, "queries")
            if train.shape[1] != queries.shape[1]:
                raise RuntimeError("train and queries must have the same dimension")
            measured_provenance = provenance()
            measured_provenance["status"] = "PARTIAL"
            train_digest = sha256_file(args.train)
            query_digest = sha256_file(args.queries)
            lloyd = run_lloyd(train, queries, args, train_digest, query_digest)
            receipt = {
                "schema": "ivf-training-comparison-v2",
                "status": "PARTIAL",
                "provenance": measured_provenance,
                "configuration": configuration(args),
                "reference": {"o_means": OMEANS_REFERENCE},
                "corpus": {
                    "train": str(args.train),
                    "queries": str(args.queries),
                    "train_sha256": train_digest,
                    "queries_sha256": query_digest,
                    "train_rows": int(train.shape[0]),
                    "query_rows": int(queries.shape[0]),
                    "dimension": int(train.shape[1]),
                },
                "methods": {
                    "lloyd": lloyd,
                    "e_means": {
                        "status": "NOT_EXECUTED",
                        "reason": "no pinned E-means adapter and corpus supplied",
                    },
                    "o_means": {
                        "status": "NOT_EXECUTED",
                        "reason": "Faiss PR #5917 adapter at the pinned commit was not supplied",
                        "reference": OMEANS_REFERENCE,
                    },
                },
            }
        except (OSError, RuntimeError, ValueError) as exc:
            receipt = not_executed(str(exc), args)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
