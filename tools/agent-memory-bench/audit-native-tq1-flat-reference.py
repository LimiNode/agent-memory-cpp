#!/usr/bin/env python3
"""Independent NumPy replay for the AMTQF01 full-flat TQ1 payload."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np

D, N, SIGN_BYTES = 384, 1_000_000, 48


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rotate():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.rotate


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


def top10(centroids: np.ndarray, signs: np.ndarray, scales: np.ndarray,
          thq: np.ndarray, query: np.ndarray, chunk_size: int) -> list[int]:
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
        residual_score = residual[np.arange(48), signs[begin:end]].sum(axis=1) * scales[begin:end]
        scores = (base_score + residual_score) / max(qnorm, 1e-30)
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
    centroids, signs, scales = parse_payload(args.payload)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, 96))
    query_rows = args.queries.stat().st_size // (D * 4)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_rows, D))[:args.query_count]
    mismatches = []
    for query in range(args.query_count):
        expected = top10(centroids, signs, scales, thq, queries[query], args.chunk_size)
        if expected != native[query]:
            mismatches.append(query)
    result = {"status": "PASS" if not mismatches else "FAIL", "query_count": args.query_count,
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
