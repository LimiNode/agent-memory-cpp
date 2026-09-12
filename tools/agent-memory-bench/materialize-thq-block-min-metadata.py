#!/usr/bin/env python3
"""Materialize immutable per-tile THQ level-presence summaries.

Each summary byte is a four-bit presence mask for one coordinate.  The mask is
enough to derive a safe interval-squared-ADC lower bound before reading the
corresponding packed payload block.
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


def unpack_block(raw: np.ndarray, documents: int, width: int) -> np.ndarray:
    packed_width = (width + 3) // 4
    packed = raw.reshape(documents, packed_width)
    levels = np.empty((documents, width), dtype=np.uint8)
    for coordinate in range(width):
        levels[:, coordinate] = (packed[:, coordinate // 4] >>
                                 (2 * (coordinate % 4))) & 3
    return levels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    args.output_root.mkdir(parents=True, exist_ok=False)
    grouped: dict[int, list[dict]] = {}
    for row in layout["blocks"]:
        grouped.setdefault(int(row["tile"]), []).append(row)
    tile_rows = []
    for tile, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: int(row["block"]))
        summary = np.empty((len(rows), int(layout["coords_per_block"])),
                           dtype=np.uint8)
        for block_index, row in enumerate(rows):
            raw = np.fromfile(Path(row["path"]), dtype=np.uint8)
            levels = unpack_block(
                raw, int(row["document_count"]),
                int(row.get("coordinate_count", layout["coords_per_block"])))
            masks = np.zeros(levels.shape[1], dtype=np.uint8)
            for level in range(4):
                masks |= np.any(levels == level, axis=0).astype(np.uint8) << level
            summary[block_index] = masks
        path = args.output_root / f"tile-{tile:05d}.u8"
        summary.tofile(path)
        tile_rows.append({
            "tile": tile,
            "blocks": len(rows),
            "coordinates": int(summary.shape[1]),
            "bytes": int(path.stat().st_size),
            "sha256": sha256(path),
            "path": str(path),
        })
    output = {
        "schema_version": 1,
        "family": "thq_block_min_presence_metadata_v1",
        "source_layout_manifest": str(args.layout_manifest),
        "source_layout_manifest_sha256": sha256(args.layout_manifest),
        "tile_docs": layout["tile_docs"],
        "coords_per_block": layout["coords_per_block"],
        "encoding": "uint8_four_level_presence_mask_per_coordinate",
        "tiles": tile_rows,
        "lower_bound": "sum_i min_{level in S_tile_block_i} interval_squared_adc_i(query, level)",
        "execution_status": "MATERIALIZED_SUMMARIES_PENDING_ORACLE",
        "production_activation": False,
    }
    (args.output_root / "summary-manifest.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
