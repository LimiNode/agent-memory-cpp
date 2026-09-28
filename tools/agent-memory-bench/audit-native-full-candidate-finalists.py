#!/usr/bin/env python3
"""Independent audit for the full-candidate native finalist gate.

The runner performs its own Python/native comparison.  This audit replays the
THQ top-128 selection from the persisted candidate stream and checks the native
JSONL outputs, payload-byte contract, and committed result binding separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, QUERY_COUNT, TOP = 384, 96, 152, 128
EXPECTED_BYTES = {"joint2": 32, "rslm3": 148, "rslm4": 196}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def unpack(codes: np.ndarray) -> np.ndarray:
    value = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(value), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        lane = value[:, byte]
        out[:, 4 * byte:4 * byte + 4] = np.stack(
            ((lane & 3), (lane >> 2) & 3, (lane >> 4) & 3,
             (lane >> 6) & 3), axis=1)
    return out


def load_stream(flat: Path, raw_path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]],
                        dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    width = int(raw.get("record_bytes", 0))
    if width not in (100, 148):
        require(flat.stat().st_size % int(offsets[-1]) == 0,
                "candidate width cannot be inferred")
        width = flat.stat().st_size // int(offsets[-1])
    require(width in (100, 148) and flat.stat().st_size == int(offsets[-1]) * width,
            "candidate stream shape differs")
    payload = flat.read_bytes()
    ids = np.ndarray((int(offsets[-1]),), dtype="<i4", buffer=payload,
                     strides=(width,)).astype(np.int32, copy=True)
    require(len(counts) == QUERY_COUNT, "candidate query count differs")
    return ids, offsets.astype(np.int64)


def thq_top(query: np.ndarray, ids: np.ndarray, thq: np.ndarray,
            thresholds: np.ndarray) -> np.ndarray:
    levels = unpack(thq[ids])
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = (low - query[coordinate] if query[coordinate] < low else
                     query[coordinate] - high if query[coordinate] > high else 0.0)
            lut[coordinate, level] = delta * delta
    score = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, score))[:TOP]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("status") == "EXECUTED", "result is not executed")
    ids, offsets = load_stream(args.candidate_flat, args.candidate_raw)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8,
                    shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(QUERY_COUNT, D))
    rows = []
    for codec, expected_bytes in EXPECTED_BYTES.items():
        path = args.native_dir / f"{codec}.native.jsonl"
        native_rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                       if line]
        require(len(native_rows) == QUERY_COUNT, f"{codec} native row count differs")
        set_ok = order_ok = True
        mismatches: list[int] = []
        for query_index, native in enumerate(native_rows):
            require(int(native["query"]) == query_index, f"{codec} query order differs")
            selected = thq_top(np.asarray(queries[query_index], dtype=np.float32),
                               ids[offsets[query_index]:offsets[query_index + 1]],
                               thq, thresholds)
            actual = np.asarray(native["thq4_top128_ids"], dtype=np.int32)
            if not np.array_equal(np.sort(selected), np.sort(actual)):
                set_ok = False
                mismatches.append(query_index)
            if not np.array_equal(selected, actual):
                order_ok = False
            require(int(native["logical_payload_bytes"]) == expected_bytes,
                    f"{codec} payload byte contract differs")
        result_row = next(row for row in result["rows"] if row["codec"] == codec)
        require(int(result_row["payload_bytes"]) == expected_bytes,
                f"{codec} result payload bytes differ")
        rows.append({"codec": codec, "thq_top128_set_parity": set_ok,
                     "thq_top128_ordered_parity": order_ok,
                     "mismatch_queries": mismatches,
                     "native_sha256": sha256(path)})
    # Native and Python use the same deterministic ID tie-break, but scalar
    # accumulation can move equal/near-equal THQ distances across the cut.
    # The fail-closed correctness contract is therefore set parity; ordering
    # differences are reported explicitly rather than mislabelled as equality.
    status = "PASS" if all(row["thq_top128_set_parity"] for row in rows) else "FAIL"
    audit = {"schema_version": 1,
             "family": "thq_native_full_candidate_finalists_audit_v1",
             "status": status, "query_count": QUERY_COUNT,
             "source_hashes": {key: sha256(path) for key, path in {
                 "result": args.result, "thq": args.thq,
                 "thresholds": args.thresholds,
                 "candidate_flat": args.candidate_flat,
                 "candidate_raw": args.candidate_raw,
                 "queries": args.queries}.items()},
             "rows": rows,
             "acceptance": {"thq_set_parity": True,
                            "thq_ordered_parity": "reported; explicit numerical/tie-order exception",
                            "payload_bytes": EXPECTED_BYTES,
                            "ordering_policy": "set parity is mandatory; ordering-only differences are not semantic mismatches"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    require(status == "PASS", "native full-candidate audit failed")
    print(json.dumps({"status": status, "codecs": list(EXPECTED_BYTES)}, sort_keys=True))


if __name__ == "__main__":
    main()
