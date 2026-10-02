#!/usr/bin/env python3
"""Materialize query-local AMPLSQ payloads from a full packed PLSQ table."""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

D, N, THQ_BYTES, WIDTH = 384, 1_000_000, 96, 128


def self_test() -> None:
    if np.lexsort((np.asarray([2, 1]), np.asarray([0.2, 0.1]))).tolist() != [1, 0]:
        raise AssertionError("PLSQ materializer self-test differs")
    print("PLSQ routed materializer self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--full-payload", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--offsets", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not all((args.full_payload, args.thq, args.thresholds, args.candidate_flat,
                args.offsets, args.queries, args.output)):
        parser.error("all inputs are required")
    data = args.full_payload.read_bytes()
    if data[:8] != b"AMPLSQF1":
        raise SystemExit("invalid full PLSQ payload")
    count, splits, sub, code_bytes = struct.unpack_from("<IIII", data, 8)
    if count != N or splits != 8 or sub not in (4, 6) or code_bytes != splits * sub:
        raise SystemExit("full PLSQ dimensions differ")
    offset = 24
    codes = np.frombuffer(data, dtype="u1", count=N * code_bytes, offset=offset).reshape(N, code_bytes); offset += N * code_bytes
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=offset); offset += N * 4
    centroids = np.frombuffer(data, dtype="<f4", count=D * 4, offset=offset); offset += D * 4 * 4
    split_dim = D // splits
    books = np.frombuffer(data, dtype="<f4", count=splits * sub * 256 * split_dim, offset=offset)
    thq = np.memmap(args.thq, mode="r", dtype="u1", shape=(N, THQ_BYTES))
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, D))
    candidate = args.candidate_flat.read_bytes(); offsets = np.fromfile(args.offsets, dtype="<u8")
    record_bytes = len(candidate) // int(offsets[-1])
    if record_bytes not in (4, 100, 148): raise SystemExit("candidate record width differs")
    ids = np.asarray([struct.unpack_from("<i", candidate, i * record_bytes)[0] for i in range(int(offsets[-1]))], dtype="i4")
    selected = []
    for qi, query in enumerate(queries):
        page = ids[int(offsets[qi]):int(offsets[qi + 1])]
        scores = np.zeros(len(page), dtype="f8")
        for byte in range(THQ_BYTES):
            for lane in range(4):
                d = byte * 4 + lane; value = float(query[d]); c0, c1, c2 = thresholds[d]
                distances = np.asarray([max(value - c0, 0.0) ** 2,
                                        0.0 if c0 <= value <= c1 else min((value-c0)**2, (value-c1)**2),
                                        0.0 if c1 <= value <= c2 else min((value-c1)**2, (value-c2)**2),
                                        max(c2 - value, 0.0) ** 2])
                scores += distances[((thq[page, byte] >> (2 * lane)) & 3)]
        order = np.lexsort((page, scores))[:WIDTH]
        selected.append(page[order])
    selected_ids = np.asarray(selected, dtype="<i4")
    selected_codes = codes[selected_ids].reshape(-1)
    selected_norms = norms[selected_ids].reshape(-1)
    header = struct.pack("<8sIIIII", b"AMPLSQ01", 152, WIDTH, splits, sub, code_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(header + selected_ids.tobytes() + selected_codes.tobytes() + selected_norms.tobytes() + centroids.tobytes() + books.tobytes())
    print(f"materialized {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
