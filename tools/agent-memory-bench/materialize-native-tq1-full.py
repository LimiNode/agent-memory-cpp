#!/usr/bin/env python3
"""Materialize a source-bound 1M TurboQuant-1 packed table.

The output contains only packed signs, per-row residual scale and the shared
THQ level centroids.  It intentionally does not claim serving performance;
the native flat scorer/audit is a separate gate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np

D, N = 384, 1_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_reference():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TurboQuant reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    fallback = train.mean(axis=0)
    result = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            values = train[levels[:, d] == level, d]
            result[d, level] = values.mean() if len(values) else fallback[d]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=4096)
    args = parser.parse_args()
    if args.chunk_size <= 0:
        raise SystemExit("chunk-size must be positive")
    if args.documents.stat().st_size != N * D * 4:
        raise SystemExit("documents must be DE-1M FP32x384")
    if args.train_vectors.stat().st_size % (D * 4) != 0:
        raise SystemExit("train vectors are not FP32x384")
    ref = load_reference()
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(N, D))
    train_rows = args.train_vectors.stat().st_size // (D * 4)
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_rows, D)), dtype=np.float32)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, 96))
    centroids = fit_centroids(train, thresholds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as stream:
        stream.write(b"AMTQF01\0")
        stream.write(struct.pack("<IIII", D, N, 48, 0))
        stream.write(centroids.astype("<f4").tobytes())
        for start in range(0, N, args.chunk_size):
            stop = min(start + args.chunk_size, N)
            values = np.asarray(docs[start:stop], dtype=np.float32)
            levels = ref.unpack_thq(np.asarray(thq[start:stop]))
            base = centroids[np.arange(D)[None, :], levels]
            residual = values - base
            norms = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32)
            safe = np.where(norms > 1e-12, norms, 1.0)
            rotated = ref.rotate(residual) * (np.sqrt(float(D)) / safe)[:, None]
            signs = (rotated > 0.0).astype(np.uint8)
            packed = np.packbits(signs, axis=1, bitorder="little")
            scales = (safe / np.sqrt(float(D))).astype("<f4")
            scales[norms <= 1e-12] = 0.0
            rows = np.empty((stop - start, 52), dtype=np.uint8)
            rows[:, :48] = packed
            rows[:, 48:] = scales.view(np.uint8).reshape(-1, 4)
            stream.write(rows.tobytes())
    receipt = {
        "schema_version": 1,
        "family": "native_tq1_full_payload_v1",
        "status": "EXECUTED",
        "documents": N,
        "dimension": D,
        "packed_sign_bytes": 48,
        "side_bytes_per_document": 52,
        "payload_bytes": args.output.stat().st_size,
        "payload_sha256": sha256(args.output),
        "materializer_sha256": sha256(Path(__file__)),
        "source_hashes": {name: sha256(path) for name, path in {
            "documents": args.documents, "train_vectors": args.train_vectors,
            "thresholds": args.thresholds, "thq": args.thq}.items()},
        "limitations": ["packed materialization only; native flat serving scorer is a separate gate"],
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
