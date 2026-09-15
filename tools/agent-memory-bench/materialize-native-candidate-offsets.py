#!/usr/bin/env python3
"""Materialize the canonical little-endian offsets sidecar for native replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    rows = raw.get("rows", [])
    if len(rows) != 152:
        raise SystemExit("candidate raw must contain exactly 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype="<u8")
    if np.any(counts < 5000) or np.any(counts > 5099):
        raise SystemExit("candidate count outside frozen 5000..5099 contract")
    offsets = np.concatenate((np.asarray([0], dtype="<u8"), np.cumsum(counts, dtype="<u8")))
    args.output.write_bytes(offsets.astype("<u8", copy=False).tobytes())
    receipt = {
        "schema_version": 1,
        "family": "native_full_corpus_candidate_offsets_v1",
        "status": "EXECUTED",
        "query_count": len(rows),
        "candidate_raw_sha256": sha(args.candidate_raw),
        "offsets_sha256": sha(args.output),
        "offsets_bytes": args.output.stat().st_size,
        "record_bytes": 148,
        "endianness": "little",
        "counts_min": int(counts.min()),
        "counts_max": int(counts.max()),
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
