#!/usr/bin/env python3
"""Measure pure random-hyperplane (cosine-LSH/SimHash) locality.

This is intentionally a representation/index-free research runner.  It builds
deterministic Gaussian and Rademacher random-hyperplane signatures directly
from the frozen E5 vectors, then performs an exhaustive packed Hamming scan.
The exhaustive scan is the locality control: no PCA, ITQ training, IVF, or
LSH-table machinery is mixed into this measurement.

The input vectors are memory-mapped, so the 1M-document fixture does not need
to be copied into RAM.  Signatures are cached as little-endian packed bytes in
``--cache-dir`` and can be reused by subsequent runs.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


_POPCOUNT = np.array([int(i).bit_count() for i in range(256)], dtype=np.uint8)


def quantile(values: list[float] | list[int], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), q)) if values else 0.0


def p95(values: list[float] | list[int]) -> float:
    return quantile(values, 0.95)


def load_f32(path: Path, shape: tuple[int, ...]) -> np.ndarray:
    expected = int(np.prod(shape)) * np.dtype("<f4").itemsize
    if path.stat().st_size != expected:
        raise ValueError(f"{path} has {path.stat().st_size} bytes; expected {expected}")
    return np.memmap(path, mode="r", dtype="<f4", shape=shape)


def random_planes(dimension: int, bits: int, family: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if family == "gaussian":
        planes = rng.standard_normal((dimension, bits), dtype=np.float32)
    elif family == "rademacher":
        planes = rng.integers(0, 2, size=(dimension, bits), dtype=np.int8).astype(np.float32)
        planes *= 2.0
        planes -= 1.0
    else:
        raise ValueError("family must be gaussian or rademacher")
    # Column normalization does not change a sign bit, but makes the generated
    # family explicit and avoids scale-dependent BLAS behavior in diagnostics.
    norms = np.linalg.norm(planes, axis=0, keepdims=True)
    return planes / np.maximum(norms, 1e-12)


def materialize_codes(
    vectors: np.ndarray,
    path: Path,
    bits: int,
    family: str,
    seed: int,
    chunk_size: int,
) -> np.memmap:
    words = (bits + 7) // 8
    expected = vectors.shape[0] * words
    if path.exists() and path.stat().st_size == expected:
        return np.memmap(path, mode="r", dtype=np.uint8, shape=(vectors.shape[0], words))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    out = np.memmap(tmp, mode="w+", dtype=np.uint8, shape=(vectors.shape[0], words))
    planes = random_planes(vectors.shape[1], bits, family, seed)
    for start in range(0, vectors.shape[0], chunk_size):
        stop = min(start + chunk_size, vectors.shape[0])
        block = np.asarray(vectors[start:stop], dtype=np.float32)
        block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-12)
        projected = np.asarray(block @ planes, dtype=np.float32)
        out[start:stop] = np.packbits(projected >= 0.0, axis=1, bitorder="little")
    out.flush()
    del out
    tmp.replace(path)
    return np.memmap(path, mode="r", dtype=np.uint8, shape=(vectors.shape[0], words))


def hamming_distances(codes: np.ndarray, query_code: np.ndarray) -> np.ndarray:
    # uint8 lookup is portable across NumPy versions and keeps the database
    # packed for the complete operation (the SIMD/native analogue is POPCNT).
    xor = np.bitwise_xor(codes, query_code)
    return _POPCOUNT[xor].sum(axis=1, dtype=np.uint16)


def evaluate(
    vectors: np.ndarray,
    queries: np.ndarray,
    teacher_ids: np.ndarray,
    family: str,
    bits: int,
    seed: int,
    code_path: Path,
    cache_chunk: int,
    budgets: list[int],
) -> dict[str, object]:
    started = time.perf_counter()
    codes = materialize_codes(vectors, code_path, bits, family, seed, cache_chunk)
    materialize_s = time.perf_counter() - started
    planes = random_planes(vectors.shape[1], bits, family, seed)
    encode_ms: list[float] = []
    scan_ms: list[float] = []
    budget_survival: dict[str, list[float]] = {str(k): [] for k in budgets}
    ranks: list[int] = []
    for row, query in enumerate(queries):
        tic = time.perf_counter()
        normalized_query = query / max(float(np.linalg.norm(query)), 1e-12)
        query_code = np.packbits((normalized_query @ planes >= 0.0).reshape(1, -1), axis=1, bitorder="little")[0]
        encode_ms.append((time.perf_counter() - tic) * 1000.0)
        tic = time.perf_counter()
        distances = hamming_distances(codes, query_code)
        scan_ms.append((time.perf_counter() - tic) * 1000.0)
        counts = np.bincount(distances, minlength=bits + 1)
        cumulative = np.cumsum(counts)
        targets = np.asarray(teacher_ids[row], dtype=np.int64)
        target_distances = distances[targets]
        # Lower-bound rank is deterministic and tie-safe: documents at the
        # same distance are all valid Hamming ties, so rank is reported as the
        # first possible position (1 + number of strictly smaller distances).
        less_counts = np.zeros(target_distances.shape, dtype=np.int64)
        positive = target_distances > 0
        less_counts[positive] = cumulative[target_distances[positive] - 1]
        ranks.extend((less_counts + 1).tolist())
        for budget in budgets:
            k = min(int(budget), distances.size)
            ids = np.argpartition(distances, k - 1)[:k]
            budget_survival[str(budget)].append(float(np.isin(targets, ids).mean()))
    rank_array = np.asarray(ranks, dtype=np.float64)
    row: dict[str, object] = {
        "method": f"{family}_random_hyperplane",
        "family": family,
        "bits": bits,
        "seed": seed,
        "documents": int(vectors.shape[0]),
        "queries": int(queries.shape[0]),
        "bytes_per_document": int((bits + 7) // 8),
        "cache_path": str(code_path),
        "materialize_seconds": materialize_s,
        "query_encode_ms_p50": quantile(encode_ms, 0.50),
        "query_encode_ms_p95": quantile(encode_ms, 0.95),
        "query_encode_ms_p99": quantile(encode_ms, 0.99),
        "hamming_scan_ms_p50": quantile(scan_ms, 0.50),
        "hamming_scan_ms_p95": quantile(scan_ms, 0.95),
        "hamming_scan_ms_p99": quantile(scan_ms, 0.99),
        "rank_r50": quantile(ranks, 0.50),
        "rank_r95": quantile(ranks, 0.95),
        "rank_r99": quantile(ranks, 0.99),
        "rank_max": int(rank_array.max()) if rank_array.size else 0,
        "survival": {
            budget: {
                "mean": float(np.mean(values)),
                "p05": quantile(values, 0.05),
                "worst_query": float(np.min(values)),
                "full_10_of_10_fraction": float(np.mean(np.asarray(values) >= 1.0)),
            }
            for budget, values in budget_survival.items()
        },
        "protocol": "normalized E5 vectors; deterministic random hyperplanes; packed little-endian sign bits; exhaustive Hamming scan; lower-bound ranks",
    }
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, required=True, help="float32 document matrix")
    parser.add_argument("--documents", type=int, required=True)
    parser.add_argument("--dimension", type=int, default=384)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--bits", default="256,512")
    parser.add_argument("--families", default="gaussian,rademacher")
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--chunk-size", type=int, default=8192)
    parser.add_argument("--budgets", default="256,1000,5000,10000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    vectors = load_f32(args.vectors, (args.documents, args.dimension))
    queries = load_f32(args.queries, (args.query_count, args.dimension))
    teacher_expected = args.query_count * 10 * np.dtype("<i8").itemsize
    if args.teacher_ids.stat().st_size != teacher_expected:
        raise ValueError(f"teacher IDs must be {args.query_count} x 10 int64")
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(args.query_count, 10))
    bits = [int(v) for v in args.bits.split(",") if v.strip()]
    families = [v.strip() for v in args.families.split(",") if v.strip()]
    budgets = [int(v) for v in args.budgets.split(",") if v.strip()]
    rows: list[dict[str, object]] = []
    for family in families:
        for width in bits:
            cache_path = args.cache_dir / f"{family}-{width}-seed{args.seed}-document-codes.u8"
            rows.append(evaluate(vectors, queries, teacher_ids, family, width, args.seed, cache_path, args.chunk_size, budgets))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "rows": rows}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
