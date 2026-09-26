#!/usr/bin/env python3
"""Materialize a deterministic THQ residual training matrix and pre-fit manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

D = 384


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return centroids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.train_vectors.stat().st_size % (4 * D) or args.thq4_thresholds.stat().st_size != D * 3 * 4:
        raise RuntimeError("unexpected training or threshold shape")
    count = args.train_vectors.stat().st_size // (4 * D)
    train = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(count, D))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    centroids = fit_centroids(np.asarray(train, dtype=np.float32), thresholds)
    codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, 96))
    # Recompute the code assignment from the same thresholds; the document table
    # is intentionally not used as a hidden source for the train rows.
    levels = np.sum(np.asarray(train)[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    residual = np.asarray(train, dtype=np.float32) - centroids[np.arange(D)[None, :], levels]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Write the actual NumPy container consumed by the upstream QINCo loader;
    # a raw float32 stream is not a valid .npy training dataset.
    persisted = np.lib.format.open_memmap(args.output, mode="w+", dtype="<f4", shape=(count, D))
    persisted[:] = residual
    persisted.flush()
    del persisted
    manifest = {
        "schema_version": 1,
        "status": "PREFIT_MATERIALIZED",
        "materializer": str(Path(__file__).as_posix()),
        "materializer_sha256": sha256(Path(__file__)),
        "source_train_vectors": {"path": str(args.train_vectors), "sha256": sha256(args.train_vectors), "rows": int(count), "row_ids": "0..rows-1 in source order"},
        "thq4_thresholds": {"path": str(args.thq4_thresholds), "sha256": sha256(args.thq4_thresholds), "shape": [D, 3]},
        "thq4_codes_reference": {"path": str(args.thq4_codes), "sha256": sha256(args.thq4_codes), "role": "canonical table provenance; train assignment is recomputed from thresholds"},
        "centroid_rule": "per-coordinate mean of threshold-assigned four ordinal levels; global-coordinate mean fallback for empty levels",
        "residual_rule": "r = x - centroid[level(x)]",
        "output": {"path": str(args.output), "sha256": sha256(args.output), "dtype": "float32", "shape": [int(count), D], "format": "numpy_npy_open_memmap"},
        "fit_split": {"source_pool_rows": int(count), "effective_train_rows": max(0, int(count) - 5000), "validation_rows": min(5000, int(count))},
        "environment": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform()},
        "argv": sys.argv,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
