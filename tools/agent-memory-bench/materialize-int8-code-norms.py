#!/usr/bin/env python3
"""Materialize the analytical INT8 code-norm sidecar used by cosine scoring."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np


N, D = 1_000_000, 384


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    codes = np.memmap(args.codes, mode="r", dtype=np.int8, shape=(N, D))
    squared = np.empty(N, dtype=np.float64)
    for begin in range(0, N, 100_000):
        end = min(N, begin + 100_000)
        block = codes[begin:end].astype(np.float64)
        squared[begin:end] = np.sum(block * block, axis=1)
    if np.any(squared <= 0.0):
        raise ValueError("INT8 code row has zero norm")
    norms = (1.0 / np.sqrt(squared)).astype("<f4")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    norms.tofile(args.output)
    print({
        "documents": N,
        "bytes": int(norms.nbytes),
        "sha256": sha256(args.output),
        "min_code_norm": float(np.min(1.0 / norms.astype(np.float64))),
        "max_code_norm": float(np.max(1.0 / norms.astype(np.float64))),
        "mean_reconstructed_norm_if_scale_applied": None,
    })


if __name__ == "__main__":
    main()
