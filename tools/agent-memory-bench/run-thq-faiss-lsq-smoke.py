#!/usr/bin/env python3
"""Small synthetic Faiss LSQ smoke and scaling diagnostic."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def run(rows: int, dimensions: int, stages: int, seed: int) -> dict[str, object]:
    import faiss

    values = np.random.default_rng(seed).normal(size=(rows, dimensions)).astype(np.float32)
    started = time.perf_counter()
    quantizer = faiss.LocalSearchQuantizer(dimensions, stages, 8)
    quantizer.train_iters = 2
    quantizer.train_ils_iters = 1
    quantizer.encode_ils_iters = 2
    quantizer.icm_iters = 1
    quantizer.nperts = 1
    quantizer.random_seed = seed
    fit_started = time.perf_counter()
    quantizer.train(values)
    fit_seconds = time.perf_counter() - fit_started
    code_started = time.perf_counter()
    codes = np.asarray(quantizer.compute_codes(values[: min(rows, 32)]), dtype=np.uint8)
    encode_seconds = time.perf_counter() - code_started
    decoded = np.asarray(quantizer.decode(codes), dtype=np.float32)
    codebooks = faiss.vector_to_array(quantizer.codebooks).astype(np.float32)
    offsets = faiss.vector_to_array(quantizer.codebook_offsets).astype(np.int64)
    books = codebooks.reshape(-1, dimensions)
    manual = np.zeros_like(decoded)
    for stage in range(stages):
        manual += books[offsets[stage] + codes[:, stage]]
    if codes.shape != (min(rows, 32), stages):
        raise RuntimeError("unexpected LSQ code shape")
    if not np.isfinite(decoded).all() or not np.allclose(decoded, manual, rtol=0.0, atol=1e-5):
        raise RuntimeError("LSQ synthetic decode differs from additive reference")
    return {"rows": rows, "dimensions": dimensions, "stages": stages, "fit_seconds": fit_seconds, "encode_seconds": encode_seconds, "total_seconds": time.perf_counter() - started, "codes_shape": list(codes.shape), "codebook_values": int(codebooks.size), "finite_decode": True, "manual_decode_parity": True, "faiss_version": faiss.__version__}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=256)
    parser.add_argument("--dimensions", type=int, default=16)
    parser.add_argument("--stages", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.rows, args.dimensions, args.stages) < 1:
        parser.error("rows, dimensions and stages must be positive")
    result = {"schema_version": 1, "family": "thq_faiss_lsq_synthetic_smoke_v1", "status": "PASS", "quality_status": "NO_QUALITY_CLAIM", "run": run(args.rows, args.dimensions, args.stages, args.seed), "limitations": ["synthetic bounded matrix only", "reduced fit/ILS budget", "does not estimate canonical 1M fit time or convergence"]}
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
