#!/usr/bin/env python3
"""Independent NumPy replay for the AMPLSQF1 full-flat PLSQ scorer."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path

try:
    import numpy as np
except ModuleNotFoundError:  # The self-test is intentionally dependency-free.
    np = None  # type: ignore[assignment]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_raw(path: Path, query_count: int) -> dict[int, list[int]]:
    rows: dict[int, list[int]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("repeat") != 0:
            continue
        query = row.get("query")
        ids = row.get("top10_ids")
        require(isinstance(query, int) and 0 <= query < query_count, "native query differs")
        require(isinstance(ids, list) and len(ids) == 10, "native top10 shape differs")
        require(query not in rows, "duplicate native reference repeat")
        rows[query] = ids
    require(len(rows) == query_count, "native reference repeat coverage differs")
    return rows


def parse_payload(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    require(np is not None, "NumPy is required for the full reference replay")
    data = path.read_bytes()
    require(data[:8] == b"AMPLSQF1", "full-flat payload header differs")
    count, splits, sub, code_bytes = struct.unpack_from("<4I", data, 8)
    require((count, splits, sub, code_bytes) == (1_000_000, 8, 6, 48),
            "PLSQ dimensions differ")
    split_dim = 384 // splits
    expected = 24 + count * code_bytes + count * 4 + 384 * 4 * 4 + splits * sub * 256 * split_dim * 4
    require(len(data) == expected, "full-flat payload size differs")
    offset = 24
    codes = np.frombuffer(data, dtype="<u1", count=count * code_bytes, offset=offset).reshape(count, code_bytes)
    offset += count * code_bytes
    norms = np.frombuffer(data, dtype="<f4", count=count, offset=offset)
    offset += count * 4
    centroids = np.frombuffer(data, dtype="<f4", count=384 * 4, offset=offset).reshape(384, 4)
    offset += 384 * 4 * 4
    books = np.frombuffer(data, dtype="<f4", count=splits * sub * 256 * split_dim,
                          offset=offset).reshape(splits, sub, 256, split_dim)
    return codes, norms, centroids, books


def lookup_tables(centroids: np.ndarray, books: np.ndarray, query: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    require(np is not None, "NumPy is required for the full reference replay")
    base = np.empty((96, 256), dtype=np.float32)
    packed = np.arange(256, dtype=np.uint16)
    for byte in range(96):
        for value in range(256):
            total = np.float32(0.0)
            for lane in range(4):
                total = np.float32(total + centroids[byte * 4 + lane, (value >> (lane * 2)) & 3] * query[byte * 4 + lane])
            base[byte, value] = total
    code = np.empty((8 * 6, 256), dtype=np.float32)
    for split in range(8):
        for part in range(6):
            code[split * 6 + part] = np.asarray(
                books[split, part] @ query[split * 48:(split + 1) * 48], dtype=np.float32
            )
    return base, code


def reference_top10(codes: np.ndarray, norms: np.ndarray, centroids: np.ndarray,
                   books: np.ndarray, thq: np.ndarray, query: np.ndarray,
                   chunk_size: int) -> list[int]:
    require(np is not None, "NumPy is required for the full reference replay")
    qnorm = float(np.sqrt(np.sum(query.astype(np.float64) * query.astype(np.float64))))
    base, code = lookup_tables(centroids, books, query)
    candidates: list[tuple[float, int]] = []
    for begin in range(0, codes.shape[0], chunk_size):
        end = min(codes.shape[0], begin + chunk_size)
        base_score = base[np.arange(96), thq[begin:end]].sum(axis=1, dtype=np.float64)
        code_score = code[np.arange(48), codes[begin:end]].sum(axis=1, dtype=np.float64)
        scores = (base_score + code_score) / (norms[begin:end].astype(np.float64) * max(qnorm, 1e-30))
        take = min(32, end - begin)
        indices = np.argpartition(-scores, take - 1)[:take]
        candidates.extend((float(scores[index]), begin + int(index)) for index in indices)
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in candidates[:10]]


def audit(payload: Path, thq_path: Path, queries_path: Path, raw: Path,
          query_count: int, chunk_size: int) -> dict:
    require(np is not None, "NumPy is required for the full reference replay")
    codes, norms, centroids, books = parse_payload(payload)
    thq = np.fromfile(thq_path, dtype=np.uint8).reshape(1_000_000, 96)
    queries = np.fromfile(queries_path, dtype="<f4").reshape(-1, 384)[:query_count]
    require(thq.shape == (1_000_000, 96), "THQ shape differs")
    require(queries.shape == (query_count, 384), "query shape differs")
    native = read_raw(raw, query_count)
    mismatches: list[int] = []
    for query in range(query_count):
        expected = reference_top10(codes, norms, centroids, books, thq, queries[query], chunk_size)
        if expected != native[query]:
            mismatches.append(query)
    require(not mismatches, f"independent top10 differs at queries {mismatches[:8]}")
    return {
        "status": "PASS",
        "query_count": query_count,
        "native_repeat": 0,
        "independent_top10_exact": f"{query_count}/{query_count}",
        "payload_sha256": sha256(payload),
        "thq_sha256": sha256(thq_path),
        "queries_sha256": sha256(queries_path),
        "raw_sha256": sha256(raw),
        "reference_runner_sha256": sha256(Path(__file__)),
    }


def self_test() -> None:
    ordered = sorted([(0.25, 4), (0.9, 2), (0.9, 1), (-0.1, 3)],
                     key=lambda item: (-item[0], item[1]))
    require([item[1] for item in ordered[:3]] == [1, 2, 4], "reference tie ordering differs")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "raw.jsonl"
        path.write_text(json.dumps({"repeat": 0, "query": 0, "top10_ids": list(range(10))}) + "\n", encoding="utf-8")
        require(read_raw(path, 1)[0] == list(range(10)), "raw self-test differs")
        path.write_text(json.dumps({"repeat": 0, "query": 0, "top10_ids": list(range(9))}) + "\n", encoding="utf-8")
        try:
            read_raw(path, 1)
        except ValueError:
            pass
        else:
            raise ValueError("malformed top10 was accepted")
    print("audit-native-plsq-flat-reference self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--query-count", type=int, default=152)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    for value in (args.payload, args.thq, args.queries, args.raw):
        if value is None:
            parser.error("--payload, --thq, --queries and --raw are required")
    result = audit(args.payload, args.thq, args.queries, args.raw, args.query_count, args.chunk_size)
    if args.result:
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
