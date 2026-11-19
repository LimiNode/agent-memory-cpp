#!/usr/bin/env python3
"""Offline synthetic smoke comparison of Faiss Lloyd and CPU E-means."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import faiss
import numpy as np
from faiss.contrib.e_means import NumpyEmeans


SEED = 20261119
DIMENSION = 16
CENTROIDS = 8
TRAIN_ROWS = 4096
BASE_ROWS = 4096
QUERY_ROWS = 256
TOP_K = 10


def digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<f4").tobytes()).hexdigest()


def train_mse(values: np.ndarray, centroids: np.ndarray, labels: np.ndarray) -> float:
    selected = centroids[np.asarray(labels, dtype=np.int64)]
    delta = values.astype(np.float64) - selected.astype(np.float64)
    return float(np.mean(np.sum(delta * delta, axis=1)))


def make_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    means = rng.normal(0.0, 2.0, size=(CENTROIDS, DIMENSION)).astype(np.float32)

    def sample(rows: int) -> np.ndarray:
        assignments = rng.integers(0, CENTROIDS, size=rows)
        noise = rng.normal(0.0, 0.35, size=(rows, DIMENSION)).astype(np.float32)
        return means[assignments] + noise

    return sample(TRAIN_ROWS), sample(BASE_ROWS), sample(QUERY_ROWS)


def make_ivf(centroids: np.ndarray, base: np.ndarray) -> faiss.IndexIVFFlat:
    quantizer = faiss.IndexFlatL2(DIMENSION)
    index = faiss.IndexIVFFlat(quantizer, DIMENSION, CENTROIDS, faiss.METRIC_L2)
    index.quantizer.add(np.ascontiguousarray(centroids, dtype=np.float32))
    index.is_trained = True
    index.add(np.ascontiguousarray(base, dtype=np.float32))
    return index


def evaluate(
    index: faiss.IndexIVFFlat,
    queries: np.ndarray,
    exact_ids: np.ndarray,
    nprobe: int,
) -> dict:
    index.nprobe = nprobe
    start = time.perf_counter()
    distances, ids = index.search(queries, TOP_K)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    overlaps = []
    scanned = []
    for query_index, row in enumerate(ids):
        actual = {int(value) for value in row if int(value) >= 0}
        expected = {int(value) for value in exact_ids[query_index] if int(value) >= 0}
        overlaps.append(len(actual & expected) / float(TOP_K))
        _, lists = index.quantizer.search(queries[query_index:query_index + 1], nprobe)
        scanned.append(sum(index.invlists.list_size(int(list_id)) for list_id in lists[0] if list_id >= 0))
    return {
        "nprobe": nprobe,
        "recall_at_10": float(np.mean(overlaps)),
        "scanned_documents_mean": float(np.mean(scanned)),
        "retrieval_ms": elapsed_ms,
        "distance_checksum": float(np.sum(distances[np.isfinite(distances)])),
    }


def run(output: Path, receipt: Path, sidecar: Path, threads: int) -> None:
    faiss.omp_set_num_threads(threads)
    train, base, queries = make_fixture()
    exact = faiss.IndexFlatL2(DIMENSION)
    exact.add(base)
    _, exact_ids = exact.search(queries, TOP_K)
    init = np.ascontiguousarray(train[:CENTROIDS], dtype=np.float32)
    methods = []

    start = time.perf_counter()
    lloyd = faiss.Kmeans(
        DIMENSION, CENTROIDS, niter=10, nredo=1, seed=SEED,
        verbose=False, gpu=False, update_index=False
    )
    lloyd.train(train, init_centroids=init)
    lloyd_time = (time.perf_counter() - start) * 1000.0
    lloyd_centroids = np.asarray(lloyd.centroids, dtype=np.float32).reshape(CENTROIDS, DIMENSION)
    lloyd_labels = np.asarray(lloyd.index.search(train, 1)[1]).reshape(-1)
    lloyd_index_start = time.perf_counter()
    lloyd_ivf = make_ivf(lloyd_centroids, base)
    lloyd_index_ms = (time.perf_counter() - lloyd_index_start) * 1000.0
    methods.append({
        "method": "faiss_lloyd",
        "training_ms": lloyd_time,
        "ivf_build_ms": lloyd_index_ms,
        "epochs": 10,
        "train_mse": train_mse(train, lloyd_centroids, lloyd_labels),
        "centroid_digest": digest(lloyd_centroids),
        "cluster_occupancy": [int(lloyd_labels.tolist().count(i)) for i in range(CENTROIDS)],
        "retrieval": [evaluate(lloyd_ivf, queries, exact_ids, nprobe) for nprobe in (1, 2, 4, 8)],
    })

    start = time.perf_counter()
    emeans = NumpyEmeans(
        DIMENSION, CENTROIDS, nepochs=10, seed=SEED,
        verbose=False, capture_assignments=True, spherical=False
    )
    emeans_centroids, emeans_assignments = emeans.train(train, init_centroids=init)
    emeans_time = (time.perf_counter() - start) * 1000.0
    emeans_centroids = np.asarray(emeans_centroids, dtype=np.float32).reshape(CENTROIDS, DIMENSION)
    emeans_assignments = np.asarray(emeans_assignments).reshape(-1)
    emeans_index_start = time.perf_counter()
    emeans_ivf = make_ivf(emeans_centroids, base)
    emeans_index_ms = (time.perf_counter() - emeans_index_start) * 1000.0
    methods.append({
        "method": "faiss_numpy_emeans",
        "training_ms": emeans_time,
        "ivf_build_ms": emeans_index_ms,
        "epochs": 10,
        "train_mse": train_mse(train, emeans_centroids, emeans_assignments),
        "centroid_digest": digest(emeans_centroids),
        "cluster_occupancy": [int(np.sum(emeans_assignments == i)) for i in range(CENTROIDS)],
        "retrieval": [evaluate(emeans_ivf, queries, exact_ids, nprobe) for nprobe in (1, 2, 4, 8)],
    })

    raw = {
        "schema": "r5.emeans_vs_lloyd.synthetic_smoke.v1",
        "status": "PASS",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "dataset_kind": "synthetic_smoke",
        "leakage": "train/base/query generated independently; query rows are never used for training",
        "seed": SEED,
        "dimension": DIMENSION,
        "centroids": CENTROIDS,
        "train_rows": TRAIN_ROWS,
        "base_rows": BASE_ROWS,
        "query_rows": QUERY_ROWS,
        "top_k": TOP_K,
        "threads": threads,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "faiss": faiss.__version__,
        "faiss_compile_options": faiss.get_compile_options(),
        "cpu": platform.processor(),
        "exact_oracle": "faiss.IndexFlatL2",
        "methods": methods,
        "limitations": [
            "Synthetic smoke only; no quality claim for a production corpus.",
            "nprobe values are not equivalent work budgets; scanned_documents_mean is reported.",
            "O-means is not implemented or represented by these results.",
        ],
    }
    output.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    raw_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt.write_text(json.dumps({
        "schema": "r5.emeans_vs_lloyd.receipt.v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "raw_output": {"path": str(output), "sha256": raw_hash},
        "dataset_kind": raw["dataset_kind"],
        "source_revision": "offline-script-run",
        "faiss": raw["faiss"],
        "numpy": raw["numpy"],
        "python": raw["python"],
        "threads": threads,
        "exact_oracle": raw["exact_oracle"],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar.write_text(f"{raw_hash}  {output.name}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    run(args.output, args.receipt, args.sidecar, args.threads)
    print(json.dumps({"status": "PASS", "output": str(args.output)}))


if __name__ == "__main__":
    main()
