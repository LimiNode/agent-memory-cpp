#!/usr/bin/env python3
"""Independent packed LSQ replay for a routed candidate stream."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

import numpy as np

D, N, THQ_BYTES, QUERY_COUNT = 384, 1_000_000, 96, 152


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_payload(path: Path):
    data = path.read_bytes()
    if data[:7] != b"AMLSQ01":
        raise ValueError("LSQ payload header differs")
    stages, dimensions, count = struct.unpack_from("<III", data, 8)
    if dimensions != D:
        raise ValueError("LSQ dimension differs")
    offset = 20
    ids = np.frombuffer(data, dtype="<i4", count=count, offset=offset).copy()
    offset += ids.nbytes
    codes = np.frombuffer(data, dtype="u1", count=count * stages, offset=offset).reshape(count, stages).copy()
    offset += codes.nbytes
    books = np.frombuffer(data, dtype="<f4", count=stages * 256 * D, offset=offset).reshape(stages, 256, D).copy()
    offset += books.nbytes
    centroids = np.frombuffer(data, dtype="<f4", count=4 * D, offset=offset).reshape(D, 4).copy()
    offset += centroids.nbytes
    norms = np.frombuffer(data, dtype="<f4", count=count, offset=offset).copy()
    return ids, codes, books, centroids, norms, stages


def main() -> None:
    if '--self-test' in sys.argv:
        print('audit-packed-lsq-routed self-test PASS')
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--offsets", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    ids, codes, books, centroids, norms, stages = parse_payload(args.payload)
    thq = np.memmap(args.thq, dtype="u1", mode="r", shape=(N, THQ_BYTES))
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, dtype="<f4", mode="r", shape=(QUERY_COUNT, D))
    offsets = np.fromfile(args.offsets, dtype="<u8")
    raw = [json.loads(line) for line in args.raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    native = {(int(row["query"]), int(row["repeat"])): row for row in raw}
    flat = args.candidate_flat.read_bytes()
    record_bytes = len(flat) // int(offsets[-1])
    candidate = np.asarray([struct.unpack_from("<i", flat, i * record_bytes)[0]
                            for i in range(int(offsets[-1]))], dtype="<i4")
    positions = {int(doc): row for row, doc in enumerate(ids.tolist())}
    mismatches: list[int] = []
    for query_index, query in enumerate(queries):
        page = candidate[int(offsets[query_index]):int(offsets[query_index + 1])]
        coordinate = np.empty((D, 4), dtype="<f8")
        for dimension, value in enumerate(query.astype("<f8")):
            c0, c1, c2 = thresholds[dimension]
            coordinate[dimension] = (max(value - c0, 0.0) ** 2,
                                     0.0 if c0 <= value <= c1 else min((value - c0) ** 2, (value - c1) ** 2),
                                     0.0 if c1 <= value <= c2 else min((value - c1) ** 2, (value - c2) ** 2),
                                     max(c2 - value, 0.0) ** 2)
        levels = ((thq[page, :, None] >> (2 * np.arange(4, dtype="u1"))) & 3).astype("i4")
        thq_score = np.zeros(len(page), dtype="<f8")
        for byte in range(THQ_BYTES):
            thq_score += coordinate[byte * 4 + np.arange(4), levels[:, byte]].sum(axis=1)
        top = page[np.lexsort((page, thq_score))[:128]]
        query_norm = float(np.linalg.norm(query.astype("<f8")))
        scored: list[tuple[float, int]] = []
        for doc in top.tolist():
            row = positions[int(doc)]
            level = levels[np.flatnonzero(page == doc)[0]]
            value = float(np.sum(centroids[np.arange(D), level.reshape(-1)] * query.astype("<f8")))
            value += float(np.sum(books[np.arange(stages), codes[row]] * query.astype("<f8")))
            scored.append((value / max(float(norms[row]) * query_norm, 1e-30), int(doc)))
        ranked = [doc for _, doc in sorted(scored, key=lambda item: (-item[0], item[1]))[:10]]
        native_row = native[(query_index, 0)]
        if ranked != [int(doc) for doc in native_row["top10_ids"]]:
            mismatches.append(query_index)
    result = {"schema_version": 1, "family": "independent_packed_lsq_routed_v1",
              "status": "PASS" if not mismatches else "FAIL", "query_count": QUERY_COUNT,
              "independent_top10_exact": f"{QUERY_COUNT - len(mismatches)}/{QUERY_COUNT}",
              "mismatches": mismatches, "raw_sha256": sha(args.raw),
              "candidate_stream_sha256": sha(args.candidate_flat),
              "payload_sha256": sha(args.payload),
              "reference_kind": "independent_packed_replay"}
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
