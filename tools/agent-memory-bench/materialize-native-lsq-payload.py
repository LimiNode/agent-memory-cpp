#!/usr/bin/env python3
"""Materialize a compact binary LSQ payload for the native direct-code gate.

The payload is candidate-local (the frozen 152x128 shell), but the scorer reads
codes, codebooks, per-document THQ base vectors, and an FP32 final norm directly
from the binary payload.  It is therefore not a predecoded-vector benchmark.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = codes[:, byte]
        out[:, 4 * byte : 4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--lsq-models", type=Path, required=True)
    parser.add_argument("--lsq-codes", type=Path, required=True)
    parser.add_argument("--payload", type=int, choices=(32, 48), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with np.load(args.lsq_models, allow_pickle=False) as model, np.load(args.lsq_codes, allow_pickle=False) as saved:
        selected = np.asarray(saved["selected_ids"], dtype=np.int64)
        selected_unique = np.unique(selected).astype(np.int32)
        codes = np.asarray(saved[f"codes_{args.payload}"], dtype=np.uint8)
        stages = int(codes.shape[-1])
        books = np.asarray(model[f"lsq{args.payload}_codebooks"], dtype=np.float32)
        offsets = np.asarray(model[f"lsq{args.payload}_offsets"], dtype=np.int64)
        if books.shape != (stages * 256, D) or offsets.shape != (stages + 1,):
            raise RuntimeError("unexpected LSQ model shape")
        thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
        levels = unpack_thq(np.asarray(thq[selected_unique]))
        centroids = np.asarray(model["centroids"], dtype=np.float32)
        base = centroids[np.arange(D)[None, :], levels].astype(np.float32)
        code_rows: dict[int, np.ndarray] = {}
        for doc, row in zip(selected.reshape(-1), codes.reshape(-1, stages)):
            previous = code_rows.get(int(doc))
            if previous is not None and not np.array_equal(previous, row):
                raise RuntimeError(f"conflicting LSQ code for document {doc}")
            code_rows[int(doc)] = np.asarray(row, dtype=np.uint8).copy()
        persisted_codes = np.vstack([code_rows[int(doc)] for doc in selected_unique]).astype(np.uint8)
        decoded = np.zeros_like(base)
        for stage in range(stages):
            decoded += books[offsets[stage] + persisted_codes[:, stage]]
        norms = np.linalg.norm(base + decoded, axis=1).astype(np.float32)
        payload = bytearray()
        payload.extend(b"AMLSQ01\0")
        payload.extend(struct.pack("<III", stages, D, len(selected_unique)))
        payload.extend(selected_unique.astype("<i4").tobytes())
        payload.extend(persisted_codes.tobytes())
        payload.extend(books.astype("<f4").tobytes())
        payload.extend(base.astype("<f4").tobytes())
        payload.extend(norms.astype("<f4").tobytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print({"payload": str(args.output), "documents": len(selected_unique), "stages": stages, "bytes": len(payload), "norm_bytes_per_document": 4})


if __name__ == "__main__":
    main()
