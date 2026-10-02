#!/usr/bin/env python3
"""Independent NumPy replay for the AMTQF01 full-flat TQ1 payload."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from functools import lru_cache
from pathlib import Path

import numpy as np

D, N, SIGN_BYTES = 384, 1_000_000, 48


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def load_reference():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_rotate():
    return load_reference().rotate


def read_raw(path: Path, query_count: int) -> dict[int, list[int]]:
    rows: dict[int, list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("repeat") != 0:
            continue
        query, ids = row.get("query"), row.get("top10_ids")
        if not isinstance(query, int) or not isinstance(ids, list) or len(ids) != 10:
            raise ValueError("native raw shape differs")
        if query in rows or len(set(ids)) != 10:
            raise ValueError("native raw duplicates differ")
        rows[query] = ids
    if len(rows) != query_count:
        raise ValueError("native raw coverage differs")
    return rows


def validate_raw_repeats(path: Path, query_count: int, repeats: int = 5) -> None:
    seen: set[tuple[int, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        query, repeat, timing = row.get("query"), row.get("repeat"), row.get("timing_ms")
        if not isinstance(query, int) or not isinstance(repeat, int) or not isinstance(timing, (int, float)):
            raise ValueError("native raw timing shape differs")
        if not (0 <= query < query_count and 0 <= repeat < repeats) or not np.isfinite(float(timing)):
            raise ValueError("native raw query/repeat contract differs")
        key = (query, repeat)
        if key in seen:
            raise ValueError("native raw duplicate query/repeat")
        seen.add(key)
    if len(seen) != query_count * repeats:
        raise ValueError("native raw measured coverage differs")


def parse_payload(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    data = path.read_bytes()
    if data[:8] != b"AMTQF01\0":
        raise ValueError("TQ1 header differs")
    dim, count, width, flags = struct.unpack_from("<4I", data, 8)
    if (dim, count, width, flags) != (D, N, SIGN_BYTES, 0):
        raise ValueError("TQ1 dimensions differ")
    offset = 24
    centroids = np.frombuffer(data, dtype="<f4", count=D * 4, offset=offset).reshape(D, 4)
    offset += D * 4 * 4
    rows = np.frombuffer(data, dtype=np.uint8, count=N * (SIGN_BYTES + 4), offset=offset).reshape(N, SIGN_BYTES + 4)
    signs = rows[:, :SIGN_BYTES]
    scales = np.frombuffer(rows[:, SIGN_BYTES:].tobytes(), dtype="<f4")
    return centroids, signs, scales


def build_norm2(centroids: np.ndarray, signs: np.ndarray, scales: np.ndarray,
                thq: np.ndarray, chunk_size: int) -> np.ndarray:
    norm2 = np.empty(N, dtype=np.float64)
    for begin in range(0, N, chunk_size):
        end = min(N, begin + chunk_size)
        levels = np.empty((end - begin, D), dtype=np.uint8)
        for byte in range(96):
            code = thq[begin:end, byte]
            levels[:, 4 * byte:4 * byte + 4] = np.stack(
                (code & 3, (code >> 2) & 3, (code >> 4) & 3, (code >> 6) & 3), axis=1
            )
        base_vectors = centroids[np.arange(D)[None, :], levels]
        quantized = np.where(
            np.unpackbits(signs[begin:end], axis=1, bitorder="little")[:, :D] != 0,
            0.7978846,
            -0.7978846,
        )
        residual_vectors = load_reference().inverse_rotate(quantized) * scales[begin:end, None]
        norm2[begin:end] = np.sum((base_vectors + residual_vectors) ** 2, axis=1)
    return norm2


def top10(centroids: np.ndarray, signs: np.ndarray, scales: np.ndarray,
          thq: np.ndarray, norm2: np.ndarray, query: np.ndarray, chunk_size: int) -> list[int]:
    rotate = load_rotate()
    rq = rotate(query[None, :])[0].astype(np.float64)
    qnorm = float(np.linalg.norm(query.astype(np.float64)))
    base = np.empty((96, 256), dtype=np.float64)
    for byte in range(96):
        for value in range(256):
            levels = [(value >> (2 * lane)) & 3 for lane in range(4)]
            base[byte, value] = sum(float(centroids[4 * byte + lane, levels[lane]]) * float(query[4 * byte + lane]) for lane in range(4))
    residual = np.empty((48, 256), dtype=np.float64)
    c = 0.7978846
    for byte in range(48):
        for value in range(256):
            residual[byte, value] = sum((1.0 if (value >> bit) & 1 else -1.0) * c * rq[8 * byte + bit] for bit in range(8))
    candidates: list[tuple[float, int]] = []
    for begin in range(0, N, chunk_size):
        end = min(N, begin + chunk_size)
        base_score = base[np.arange(96), thq[begin:end]].sum(axis=1)
        residual_score = residual[np.arange(48), signs[begin:end]].sum(axis=1)
        scores = (base_score + residual_score * scales[begin:end]) / np.maximum(
            np.sqrt(norm2[begin:end]) * qnorm, np.finfo(np.float64).tiny
        )
        take = min(32, end - begin)
        indices = np.argpartition(-scores, take - 1)[:take]
        candidates.extend((float(scores[index]), begin + int(index)) for index in indices)
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in candidates[:10]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=152)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    native = read_raw(args.raw, args.query_count)
    validate_raw_repeats(args.raw, args.query_count)
    centroids, signs, scales = parse_payload(args.payload)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, 96))
    norm2 = build_norm2(centroids, signs, scales, thq, args.chunk_size)
    query_rows = args.queries.stat().st_size // (D * 4)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_rows, D))[:args.query_count]
    mismatches = []
    for query in range(args.query_count):
        expected = top10(centroids, signs, scales, thq, norm2, queries[query], args.chunk_size)
        if expected != native[query]:
            mismatches.append(query)
    result = {"status": "PASS" if not mismatches else "FAIL", "metric": "reconstructed_cosine",
              "norm_contract": "analytical_thq_base_plus_inverse_rotated_packed_residual",
              "query_count": args.query_count,
              "independent_top10_exact": f"{args.query_count - len(mismatches)}/{args.query_count}",
              "mismatches": mismatches[:16], "payload_sha256": sha256(args.payload),
              "thq_sha256": sha256(args.thq), "queries_sha256": sha256(args.queries),
              "raw_sha256": sha256(args.raw), "reference_runner_sha256": sha256(Path(__file__))}
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
