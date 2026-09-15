#!/usr/bin/env python3
"""Materialize canonical and posting-local packed THQ controls.

The candidate stream is an existing routing workload only. The output files
are storage controls: canonical doc_id -> one 96-byte code versus the same
codes duplicated in candidate/posting order. No query-specific data is added
to either persistent representation.
"""
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
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=32768)
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text())
    receipt = json.loads(args.candidate_receipt.read_text())
    raw = json.loads(args.candidate_raw.read_text())
    n, q = int(manifest["documents"]), int(manifest["queries"])
    rows = raw["rows"]
    total = sum(int(row["candidate_count"]) for row in rows)
    source = Path(manifest["outputs"]["thq4_document_codes"]["path"])
    source_codes = np.memmap(source, mode="r", dtype=np.uint8, shape=(n, 144))
    candidate = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(total, 148))
    args.output_root.mkdir(parents=True, exist_ok=True)
    canonical_path = args.output_root / "canonical-thq96.u8"
    duplicate_path = args.output_root / "posting-duplicate-thq96.u8"
    canonical = np.memmap(canonical_path, mode="w+", dtype=np.uint8, shape=(n, 96))
    for start in range(0, n, args.chunk):
        stop = min(n, start + args.chunk)
        bits = np.unpackbits(np.asarray(source_codes[start:stop]), axis=1, bitorder="little")[:, : 384 * 3]
        levels = bits.reshape(stop - start, 384, 3).sum(axis=2).astype(np.uint8)
        canonical[start:stop] = ((levels[:, 0::4] & 3) |
                                 ((levels[:, 1::4] & 3) << 2) |
                                 ((levels[:, 2::4] & 3) << 4) |
                                 ((levels[:, 3::4] & 3) << 6))
    canonical.flush()
    duplicate = np.memmap(duplicate_path, mode="w+", dtype=np.uint8, shape=(total, 100))
    offset = 0
    for row in rows:
        count = int(row["candidate_count"])
        block = np.asarray(candidate[offset:offset + count])
        ids = np.frombuffer(block[:, :4].tobytes(), dtype="<i4").astype(np.int64)
        require = np.logical_and(ids >= 0, ids < n)
        if not bool(np.all(require)):
            raise RuntimeError("candidate document id out of range")
        duplicate[offset:offset + count, :4] = block[:, :4]
        duplicate[offset:offset + count, 4:] = np.asarray(canonical[ids])
        offset += count
    duplicate.flush()
    counts_path = args.output_root / "candidate-counts.u32le"
    np.asarray([int(row["candidate_count"]) for row in rows], dtype="<u4").tofile(counts_path)
    receipt_out = {
        "schema_version": 1,
        "family": "semantic_thq_persistent_layout_bakeoff_v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "documents": n,
        "queries": q,
        "candidate_entries": total,
        "representations": {
            "canonical": {"path": str(canonical_path), "bytes": canonical_path.stat().st_size,
                          "sha256": sha(canonical_path), "record_bytes": 96,
                          "semantics": "doc_id -> one shared packed ordinal THQ code"},
            "posting_duplicate": {"path": str(duplicate_path), "bytes": duplicate_path.stat().st_size,
                                  "sha256": sha(duplicate_path), "record_bytes": 100,
                                  "semantics": "candidate/posting entry -> doc_id + packed THQ code"},
        },
        "query_counts": {"path": str(counts_path), "bytes": counts_path.stat().st_size,
                         "sha256": sha(counts_path)},
        "total_footprint": {"canonical_bytes": canonical_path.stat().st_size,
                             "posting_duplicate_bytes": duplicate_path.stat().st_size,
                             "canonical_plus_routing_excludes": "routing postings and MDBX overhead are not represented by this control"},
        "source": {"thq_manifest_sha256": sha(args.thq_manifest),
                   "candidate_receipt_sha256": sha(args.candidate_receipt),
                   "candidate_raw_sha256": sha(args.candidate_raw),
                   "candidate_flat_sha256": sha(args.candidate_flat)},
    }
    (args.output_root / "layout-bakeoff.receipt.json").write_text(json.dumps(receipt_out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
