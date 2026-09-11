#!/usr/bin/env python3
"""Materialize a packed ordinal THQ-ADC vertical/AoSoA layout.

Payload paths are read only from the supplied frozen manifest.  The command
never downloads or discovers external data implicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--tile-docs", type=int, default=4096)
    parser.add_argument("--coords-per-block", type=int, default=32)
    args = parser.parse_args()
    if args.tile_docs <= 0 or args.coords_per_block <= 0:
        raise ValueError("tile-docs and coords-per-block must be positive")
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    n, d = int(manifest["documents"]), int(manifest["dimension"])
    if d % args.coords_per_block:
        raise ValueError("coords-per-block must divide the frozen dimension")
    source = Path(manifest["outputs"]["thq4_document_codes"]["path"])
    expected = n * 144
    if source.stat().st_size != expected:
        raise ValueError(f"THQ payload size differs: expected {expected} bytes")
    source_hash = sha256(source)
    declared_source_hash = manifest["outputs"]["thq4_document_codes"].get("sha256")
    if declared_source_hash and declared_source_hash != source_hash:
        raise ValueError("THQ document-code hash differs from frozen manifest")
    manifest_hash = sha256(args.thq_manifest)
    codes = np.memmap(source, mode="r", dtype=np.uint8, shape=(n, 144))
    bits = np.unpackbits(np.asarray(codes), axis=1, bitorder="little")[:, : d * 3]
    levels = bits.reshape(n, d, 3).sum(axis=2).astype(np.uint8)
    args.output_root.mkdir(parents=True, exist_ok=False)
    records = []
    block_count = d // args.coords_per_block
    for tile_id, start in enumerate(range(0, n, args.tile_docs)):
        stop = min(start + args.tile_docs, n)
        tile = levels[start:stop]
        for block_id in range(block_count):
            lo = block_id * args.coords_per_block
            width = args.coords_per_block
            packed = np.zeros((stop - start, (width + 3) // 4), dtype=np.uint8)
            for coordinate in range(width):
                packed[:, coordinate // 4] |= tile[:, lo + coordinate] << (2 * (coordinate % 4))
            path = args.output_root / f"tile-{tile_id:05d}-block-{block_id:03d}.u8"
            packed.tofile(path)
            records.append({
                "tile": tile_id,
                "block": block_id,
                "document_start": start,
                "document_count": stop - start,
                "coordinate_start": lo,
                "coordinate_count": width,
                "bytes": int(path.stat().st_size),
                "sha256": sha256(path),
                "path": str(path),
            })
    output = {
        "schema_version": 1,
        "family": "progressive_thq_aosoa_vertical_layout_v1",
        "documents": n,
        "dimension": d,
        "tile_docs": args.tile_docs,
        "coords_per_block": args.coords_per_block,
        "packed_bits_per_level": 2,
        "layout": "tile_then_coordinate_block_then_packed_levels",
        "source_manifest": str(args.thq_manifest),
        "source_manifest_sha256": manifest_hash,
        "source_document_codes_sha256": source_hash,
        "blocks": records,
        "execution_status": "MATERIALIZED_LAYOUT_PENDING_NATIVE_TIMING",
        "physical_bytes_semantics": "logical_block_payload_bytes",
        "production_activation": False,
    }
    (args.output_root / "layout-manifest.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
