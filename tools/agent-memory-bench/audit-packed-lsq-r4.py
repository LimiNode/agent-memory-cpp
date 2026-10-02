#!/usr/bin/env python3
"""Independent packed LSQ replay for a Modern R4 raw receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np

D, N, THQ_BYTES = 384, 1_000_000, 96


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def self_test() -> None:
    values = np.asarray([0.2, 0.1, 0.3])
    if int(np.argsort(-values, kind="stable")[0]) != 2:
        raise AssertionError("LSQ R4 audit self-test differs")
    print("packed LSQ R4 audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--offsets", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not all((args.payload, args.thq, args.thresholds, args.candidate_flat,
                args.offsets, args.queries, args.raw, args.result)):
        parser.error("all inputs are required")
    data = args.payload.read_bytes()
    if data[:7] != b"AMLSQ01":
        raise SystemExit("invalid LSQ payload")
    stages, dimensions, count = struct.unpack_from("<III", data, 8)
    if dimensions != D or count != N:
        raise SystemExit("LSQ payload shape differs")
    offset = 20
    ids = np.frombuffer(data, dtype="<i4", count=N, offset=offset); offset += N * 4
    codes = np.frombuffer(data, dtype="u1", count=N * stages, offset=offset).reshape(N, stages); offset += N * stages
    books = np.frombuffer(data, dtype="<f4", count=stages * 256 * D, offset=offset).reshape(stages, 256, D); offset += stages * 256 * D * 4
    centroids = np.frombuffer(data, dtype="<f4", count=D * 4, offset=offset).reshape(D, 4); offset += D * 4 * 4
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=offset)
    thq = np.memmap(args.thq, mode="r", dtype="u1", shape=(N, THQ_BYTES))
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, D))
    candidate = args.candidate_flat.read_bytes()
    offsets = np.fromfile(args.offsets, dtype="<u8")
    record_bytes = len(candidate) // int(offsets[-1])
    if record_bytes not in (4, 100, 148):
        raise SystemExit("candidate record width differs")
    # Use explicit byte slicing for deterministic portability across NumPy
    # versions and record layouts.
    candidate_ids = np.asarray([struct.unpack_from("<i", candidate, i * record_bytes)[0] for i in range(int(offsets[-1]))], dtype="i4")
    raw_rows = [json.loads(line) for line in args.raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    measured = {(int(row["query"]), int(row["repeat"])): row for row in raw_rows}
    mismatches = []
    for query_index, query in enumerate(queries):
        begin, end = int(offsets[query_index]), int(offsets[query_index + 1])
        ids_q = candidate_ids[begin:end]
        # Build the canonical interval-distance LUT once per query and gather
        # all packed THQ rows vectorially.
        lut = np.empty((D, 4), dtype="f8")
        for d, value in enumerate(query.astype("f8")):
            c0, c1, c2 = thresholds[d].astype("f8")
            lut[d] = (max(value - c0, 0.0) ** 2,
                      0.0 if c0 <= value <= c1 else min((value - c0) ** 2, (value - c1) ** 2),
                      0.0 if c1 <= value <= c2 else min((value - c1) ** 2, (value - c2) ** 2),
                      max(c2 - value, 0.0) ** 2)
        packed_rows = thq[ids_q]
        levels = ((packed_rows[:, :, None] >> (2 * np.arange(4, dtype="u1"))) & 3).astype("i4")
        thq_scores = np.zeros(len(ids_q), dtype="f8")
        for byte in range(THQ_BYTES):
            thq_scores += lut[byte * 4 + np.arange(4), levels[:, byte]].sum(axis=1)
        order = sorted(range(len(ids_q)), key=lambda i: (thq_scores[i], int(ids_q[i])))[:128]
        top_ids = ids_q[order]
        qnorm = float(np.linalg.norm(query.astype("f8")))
        rows_for_top = np.searchsorted(ids, top_ids)
        top_levels = ((thq[top_ids, :, None] >> (2 * np.arange(4, dtype="u1"))) & 3).astype("i4").reshape(len(top_ids), D)
        decoded_base = centroids[np.arange(D)[None, :], top_levels]
        dots = decoded_base @ query.astype("f8")
        for stage in range(stages):
            dots += books[stage, codes[rows_for_top, stage]].astype("f8") @ query.astype("f8")
        scores = dots / np.maximum(norms[rows_for_top].astype("f8") * qnorm, 1e-30)
        ranked = [int(top_ids[i]) for i in np.lexsort((top_ids, -scores))[:10]]
        native = measured[(query_index, 0)]["top10_ids"]
        if ranked != native:
            mismatches.append(query_index)
    result = {"schema_version": 1, "family": "independent_packed_lsq_modern_r4_audit_v1",
              "status": "PASS" if not mismatches else "FAIL", "query_count": 152,
              "independent_top10_exact": f"{152-len(mismatches)}/152", "mismatches": mismatches,
              "raw_sha256": sha256(args.raw), "payload_sha256": sha256(args.payload),
              "candidate_stream_sha256": sha256(args.candidate_flat), "reference_kind": "independent_packed_replay"}
    args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
