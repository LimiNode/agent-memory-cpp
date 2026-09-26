#!/usr/bin/env python3
"""Materialize candidate-local packed TQ1 + PQ8 direct-scoring payloads."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np

D, THQ_BYTES = 384, 96
TQ_CENTROID = np.float32(0.7978846)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_tq():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference_materializer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tq = load_tq()


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2,
                    dtype=np.uint8)
    fallback = train.mean(axis=0)
    result = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            values = train[levels[:, dimension] == level, dimension]
            result[dimension, level] = values.mean() if len(values) else fallback[dimension]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--canonical-tq-payload", type=Path, required=True)
    parser.add_argument("--pq-artifact", type=Path, required=True)
    parser.add_argument("--include-tq-norm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    documents = np.memmap(args.documents, mode="r", dtype="<f4",
                          shape=(1_000_000, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(25_000, D)), dtype=np.float32)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    centroids = fit_centroids(train, thresholds)
    thq_codes = np.memmap(args.thq, mode="r", dtype=np.uint8,
                          shape=(1_000_000, THQ_BYTES))

    with np.load(args.canonical_tq_payload, allow_pickle=False) as canonical, \
            np.load(args.pq_artifact, allow_pickle=False) as pq:
        ids = np.asarray(pq["unique_ids"], dtype=np.int64)
        canonical_ids = np.asarray(canonical["document_ids"], dtype=np.int64)
        if not np.array_equal(ids, canonical_ids) or not np.all(ids[:-1] < ids[1:]):
            raise RuntimeError("TQ/PQ document identities differ or are not sorted")
        levels = tq.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        if not np.allclose(base, np.asarray(pq["base"], dtype=np.float32),
                           rtol=0.0, atol=2e-6):
            raise RuntimeError("PQ base differs from canonical THQ source replay")
        residual = np.asarray(documents[ids], dtype=np.float32) - base
        residual_norms = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32)
        safe = np.where(residual_norms > 1e-12, residual_norms, 1.0).astype(np.float32)
        rotated = tq.rotate(residual) * (np.sqrt(float(D)) / safe)[:, None]
        signs = rotated >= 0.0
        packed_signs = np.packbits(signs, axis=1, bitorder="little")
        scales = (safe / np.sqrt(float(D))).astype(np.float32)
        scales[residual_norms <= 1e-12] = 0.0
        decoded = tq.inverse_rotate(np.where(signs, TQ_CENTROID, -TQ_CENTROID)) * scales[:, None]
        decoded[residual_norms <= 1e-12] = 0.0
        persisted_tq = np.asarray(pq["tq_decoded"], dtype=np.float32)
        if np.max(np.abs(decoded - persisted_tq)) > 3e-6:
            raise RuntimeError("packed TQ1 reconstruction differs from canonical payload")
        pq_codes = np.asarray(pq["pq_codes"], dtype=np.uint8)
        pq_centroids = np.asarray(pq["pq_centroids"], dtype=np.float32)
        if pq_codes.shape != (len(ids), 8) or pq_centroids.shape != (8, 256, 48):
            raise RuntimeError("expected an 8-byte PQ8 correction")
        tq_norms = np.asarray(pq["tq_norms"], dtype=np.float32)
        final_norms = np.asarray(pq["corrected_norms"], dtype=np.float32)
        if not (np.isfinite(scales).all() and np.isfinite(tq_norms).all() and
                np.isfinite(final_norms).all() and np.all(final_norms > 0)):
            raise RuntimeError("non-finite TQ/PQ sidecar")

        flags = 1 if args.include_tq_norm else 0
        payload = bytearray(b"AMTQP01\0")
        payload.extend(struct.pack("<IIII", D, len(ids), 8, flags))
        payload.extend(ids.astype("<i4").tobytes())
        payload.extend(packed_signs.astype(np.uint8).tobytes())
        payload.extend(scales.astype("<f4").tobytes())
        payload.extend(pq_codes.tobytes())
        if args.include_tq_norm:
            payload.extend(tq_norms.astype("<f4").tobytes())
        payload.extend(final_norms.astype("<f4").tobytes())
        payload.extend(centroids.astype("<f4").tobytes())
        payload.extend(pq_centroids.astype("<f4").tobytes())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    side_bytes = 48 + 4 + 8 + 4 + (4 if args.include_tq_norm else 0)
    sources = {
        "documents": args.documents,
        "train-vectors": args.train_vectors,
        "thresholds": args.thresholds,
        "thq": args.thq,
        "canonical-tq-payload": args.canonical_tq_payload,
        "pq-artifact": args.pq_artifact,
    }
    receipt = {
        "schema_version": 1,
        "family": "native_tq1_pq8_direct_payload_v1",
        "status": "EXECUTED",
        "candidate_local": True,
        "documents": int(len(ids)),
        "dimension": D,
        "tq_sign_bytes": 48,
        "tq_scale_bytes": 4,
        "pq_code_bytes": 8,
        "tq_norm_bytes": 4 if args.include_tq_norm else 0,
        "final_norm_bytes": 4,
        "side_bytes_per_document": side_bytes,
        "include_tq_norm": args.include_tq_norm,
        "payload_bytes": len(payload),
        "payload_sha256": sha256(args.output),
        "materializer_sha256": sha256(Path(__file__)),
        "source_hashes": {name: sha256(path) for name, path in sources.items()},
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    print({"payload": str(args.output), "documents": len(ids),
           "side_bytes_per_document": side_bytes,
           "include_tq_norm": args.include_tq_norm, "bytes": len(payload),
           "receipt": str(args.receipt)})


if __name__ == "__main__":
    main()
