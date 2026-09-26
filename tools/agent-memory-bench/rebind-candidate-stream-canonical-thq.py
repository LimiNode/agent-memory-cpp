#!/usr/bin/env python3
"""Rebind a frozen candidate-ID stream to canonical packed THQ4 codes.

The historical fused R4 stream stores ``[int32 id][144-byte thermometer]``
records.  Its document IDs and query order are still useful, but the payload
is not the canonical 96-byte ordinal THQ4 representation used by the current
residual gates.  This tool preserves the frozen IDs/row boundaries and
re-materializes the payload from the source-bound 96-byte table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


OLD_RECORD = 148
NEW_RECORD = 100
DOCUMENTS = 1_000_000
THQ_BYTES = 96


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-flat", type=Path, required=True)
    parser.add_argument("--legacy-raw", type=Path, required=True)
    parser.add_argument("--legacy-receipt", type=Path, required=True)
    parser.add_argument("--canonical-thq4", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.legacy_raw.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    if not isinstance(rows, list) or len(rows) != 152:
        raise RuntimeError("legacy candidate raw must contain exactly 152 rows")
    receipt = json.loads(args.legacy_receipt.read_text(encoding="utf-8"))
    if receipt.get("execution_status") != "EXECUTED":
        raise RuntimeError("legacy candidate receipt is not EXECUTED")
    if receipt.get("raw_sha256") != sha256(args.legacy_raw):
        raise RuntimeError("legacy raw SHA does not match receipt")
    legacy_sha = receipt.get("flat_file", {}).get("sha256")
    if legacy_sha != sha256(args.legacy_flat):
        raise RuntimeError("legacy flat SHA does not match receipt")
    if args.legacy_flat.stat().st_size % OLD_RECORD:
        raise RuntimeError("legacy flat size is not record aligned")
    total = args.legacy_flat.stat().st_size // OLD_RECORD
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    if int(counts.sum()) != total:
        raise RuntimeError("legacy row counts do not match flat payload")
    if args.canonical_thq4.stat().st_size != DOCUMENTS * THQ_BYTES:
        raise RuntimeError("canonical THQ4 table must be 1M x 96 bytes")

    ids_and_old = np.memmap(args.legacy_flat, mode="r", dtype=np.uint8,
                            shape=(total, OLD_RECORD))
    canonical = np.memmap(args.canonical_thq4, mode="r", dtype=np.uint8,
                          shape=(DOCUMENTS, THQ_BYTES))
    args.output_root.mkdir(parents=True, exist_ok=True)
    flat = args.output_root / "candidate-canonical-flat.bin"
    out_hash = hashlib.sha256()
    offset = 0
    with flat.open("wb") as stream:
        for count in counts.tolist():
            block = np.asarray(ids_and_old[offset:offset + count, :4]).copy()
            ids = block.view("<i4").reshape(-1).astype(np.int64)
            if np.any(ids < 0) or np.any(ids >= DOCUMENTS):
                raise RuntimeError("candidate document ID outside canonical corpus")
            payload = np.empty((count, NEW_RECORD), dtype=np.uint8)
            payload[:, :4] = block
            payload[:, 4:] = np.asarray(canonical[ids])
            data = payload.tobytes()
            stream.write(data)
            out_hash.update(data)
            offset += count

    raw_out = {
        "schema_version": 1,
        "family": "canonical_thq4_rebound_candidate_materialization_v1",
        "execution_status": "EXECUTED",
        "query_count": len(rows),
        "rows": rows,
        "record": "[int32 doc_id][96-byte canonical packed ordinal THQ4]",
        "candidate_id_stream": "preserved byte-for-byte from legacy fused stream",
        "legacy_record_bytes": OLD_RECORD,
        "record_bytes": NEW_RECORD,
        "legacy_flat_sha256": sha256(args.legacy_flat),
        "legacy_raw_sha256": sha256(args.legacy_raw),
        "legacy_receipt_sha256": sha256(args.legacy_receipt),
        "canonical_thq4_sha256": sha256(args.canonical_thq4),
    }
    raw_path = args.output_root / "candidate-canonical.raw.json"
    raw_path.write_text(json.dumps(raw_out, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    receipt_out = {
        "schema_version": 1,
        "family": raw_out["family"],
        "execution_status": "EXECUTED",
        "production_activation": False,
        "raw_sha256": sha256(raw_path),
        "runner_sha256": sha256(Path(__file__)),
        "legacy_flat_sha256": sha256(args.legacy_flat),
        "legacy_raw_sha256": sha256(args.legacy_raw),
        "legacy_receipt_sha256": sha256(args.legacy_receipt),
        "canonical_thq4_sha256": sha256(args.canonical_thq4),
        "flat_file": {
            "path": str(flat),
            "bytes": flat.stat().st_size,
            "sha256": out_hash.hexdigest(),
            "record_bytes": NEW_RECORD,
            "records": total,
        },
    }
    (args.output_root / "candidate-canonical.receipt.json").write_text(
        json.dumps(receipt_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
