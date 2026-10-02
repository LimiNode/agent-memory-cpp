#!/usr/bin/env python3
"""Materialize a source-bound 1M TQ1+PQ8 packed payload.

The PQ8 codebook is taken from the validated strong-pq replay artifact.  The
full corpus is encoded against that frozen codebook; no decoded FP32 document
rows are persisted.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np

D, N, THQ_BYTES, SUBSPACES, WIDTH = 384, 1_000_000, 96, 8, 48
CENTROID = np.float32(0.7978846)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tq():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_full", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    fallback = train.mean(axis=0)
    out = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            values = train[levels[:, dimension] == level, dimension]
            out[dimension, level] = values.mean() if len(values) else fallback[dimension]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--pq-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=8192)
    args = parser.parse_args()
    if args.chunk_size <= 0 or args.documents.stat().st_size != N * D * 4:
        raise SystemExit("expected DE-1M FP32x384 documents")
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(25_000, D)), dtype=np.float32)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, THQ_BYTES))
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(N, D))
    tq = load_tq(); centroids = fit_centroids(train, thresholds)
    with np.load(args.pq_artifact, allow_pickle=False) as artifact:
        pq_centroids = np.asarray(artifact["pq_centroids"], dtype=np.float32)
        if pq_centroids.shape != (SUBSPACES, 256, WIDTH):
            raise RuntimeError("frozen PQ8 codebook shape differs")
    signs = np.empty((N, 48), dtype=np.uint8); scales = np.empty(N, dtype=np.float32)
    codes = np.empty((N, SUBSPACES), dtype=np.uint8); final_norms = np.empty(N, dtype=np.float32)
    for start in range(0, N, args.chunk_size):
        stop = min(N, start + args.chunk_size); ids = np.arange(start, stop)
        levels = tq.unpack_thq(np.asarray(thq[start:stop]))
        base = centroids[np.arange(D)[None, :], levels]
        residual = np.asarray(docs[start:stop], dtype=np.float32) - base
        residual_norm = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32)
        safe = np.where(residual_norm > 1e-12, residual_norm, 1.0).astype(np.float32)
        rotated = tq.rotate(residual) * (np.sqrt(float(D)) / safe)[:, None]
        chunk_signs = rotated > 0.0; signs[start:stop] = np.packbits(chunk_signs, axis=1, bitorder="little")
        chunk_scales = (safe / np.sqrt(float(D))).astype(np.float32); chunk_scales[residual_norm <= 1e-12] = 0.0
        scales[start:stop] = chunk_scales
        decoded = tq.inverse_rotate(np.where(chunk_signs, CENTROID, -CENTROID)) * chunk_scales[:, None]
        decoded[residual_norm <= 1e-12] = 0.0
        correction = residual - decoded
        reconstructed = base + decoded
        for subspace in range(SUBSPACES):
            begin, end = subspace * WIDTH, (subspace + 1) * WIDTH
            values = correction[:, begin:end].astype(np.float32); centers = pq_centroids[subspace]
            distances = np.sum(values * values, axis=1)[:, None] + np.sum(centers * centers, axis=1)[None, :] - 2.0 * (values @ centers.T)
            selected = np.argmin(distances, axis=1); codes[start:stop, subspace] = selected.astype(np.uint8)
            reconstructed[:, begin:end] += centers[selected]
        final_norms[start:stop] = np.linalg.norm(reconstructed.astype(np.float64), axis=1).astype(np.float32)
    payload = bytearray(b"AMTQP01\0")
    payload.extend(struct.pack("<IIII", D, N, SUBSPACES, 0))
    payload.extend(np.arange(N, dtype="<i4").tobytes()); payload.extend(signs.tobytes()); payload.extend(scales.astype("<f4").tobytes())
    payload.extend(codes.tobytes()); payload.extend(final_norms.astype("<f4").tobytes()); payload.extend(centroids.astype("<f4").tobytes()); payload.extend(pq_centroids.astype("<f4").tobytes())
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_bytes(payload)
    receipt = {"schema_version": 1, "family": "native_tq1_pq8_full_payload_v1", "status": "EXECUTED", "candidate_local": False, "documents": N, "dimension": D, "side_bytes_per_document": 64, "payload_bytes": len(payload), "payload_sha256": sha256(args.output), "materializer_sha256": sha256(Path(__file__)), "pq_codebook_sha256": sha256(args.pq_artifact), "source_hashes": {name: sha256(path) for name, path in {"documents": args.documents, "train_vectors": args.train_vectors, "thresholds": args.thresholds, "thq": args.thq, "pq_artifact": args.pq_artifact}.items()}}
    args.receipt.parent.mkdir(parents=True, exist_ok=True); args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
