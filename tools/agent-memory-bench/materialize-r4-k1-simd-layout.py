#!/usr/bin/env python3
"""Materialize row-major and page-matched AoSoA INT8 K1 coarse stores."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np


DIMENSIONS = 384
LANES = (8, 10, 16, 32)
PAGE_BYTES = 4096


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked(root: Path, record: dict[str, object], key: str, bytes_key: str,
            sha_key: str) -> Path:
    path = root / str(record[key])
    if not path.is_file() or path.stat().st_size != int(record[bytes_key]):
        raise RuntimeError(f"source artifact differs: {path}")
    if sha256(path) != str(record[sha_key]):
        raise RuntimeError(f"source artifact SHA differs: {path}")
    return path


def write_layout(values: np.ndarray, path: Path, lanes: int) -> dict[str, object]:
    rows = values.shape[0]
    tiles = (rows + lanes - 1) // lanes
    padded = np.zeros((tiles * lanes, DIMENSIONS), dtype=np.int8)
    padded[:rows] = values
    # The innermost dimension is the lane.  This makes a SIMD load consume a
    # contiguous group of addresses for one coordinate.
    tiled = padded.reshape(tiles, lanes, DIMENSIONS).transpose(0, 2, 1)
    tiled.tofile(path)
    physical_bytes = int(path.stat().st_size)
    return {
        "file": path.name,
        "rows": rows,
        "dimensions": DIMENSIONS,
        "lanes": lanes,
        "tiles": tiles,
        "tile_bytes": lanes * DIMENSIONS,
        "tile_pages_4k": (lanes * DIMENSIONS + PAGE_BYTES - 1) // PAGE_BYTES,
        "logical_bytes": rows * DIMENSIONS,
        "physical_bytes": physical_bytes,
        "logical_pages_4k": (rows * DIMENSIONS + PAGE_BYTES - 1) // PAGE_BYTES,
        "physical_pages_4k": (physical_bytes + PAGE_BYTES - 1) // PAGE_BYTES,
        "bytes": physical_bytes,
        "sha256": sha256(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if source.get("family") != "semantic_r4_mean_coarse_int8_materialization_v1":
        raise RuntimeError("unexpected coarse INT8 source family")
    args.output_root.mkdir(parents=True, exist_ok=True)
    seeds: list[dict[str, object]] = []
    for record in source["seeds"]:
        rows = int(record["rows"])
        if int(record["dimensions"]) != DIMENSIONS:
            raise RuntimeError("coarse dimension count differs")
        source_path = checked(args.source_root, record, "code_file", "code_bytes",
                              "code_sha256")
        values = np.memmap(source_path, mode="r", dtype=np.int8,
                           shape=(rows, DIMENSIONS))
        seed_root = args.output_root / f"seed-{int(record['seed'])}"
        seed_root.mkdir(parents=True, exist_ok=True)
        row_path = seed_root / "row-major.i8"
        shutil.copyfile(source_path, row_path)
        layouts: list[dict[str, object]] = [{
            "id": "row_major_int8", "organization": "row_major", "lanes": 1,
            "rows": rows, "dimensions": DIMENSIONS,
            "logical_bytes": rows * DIMENSIONS,
            "physical_bytes": int(row_path.stat().st_size),
            "logical_pages_4k": (rows * DIMENSIONS + PAGE_BYTES - 1) // PAGE_BYTES,
            "physical_pages_4k": (int(row_path.stat().st_size) + PAGE_BYTES - 1) // PAGE_BYTES,
            "file": f"seed-{int(record['seed'])}/row-major.i8",
            "bytes": int(row_path.stat().st_size), "sha256": sha256(row_path),
        }]
        for lanes in LANES:
            path = seed_root / f"aosoa-{lanes}.i8"
            item = write_layout(np.asarray(values), path, lanes)
            item.update({"id": f"aosoa{lanes}_int8", "organization": "aosoa",
                         "file": f"seed-{int(record['seed'])}/aosoa-{lanes}.i8"})
            layouts.append(item)
        scale = checked(args.source_root, record, "scale_file", "scale_bytes",
                        "scale_sha256")
        scale_path = seed_root / "scales.f32le"
        shutil.copyfile(scale, scale_path)
        seeds.append({"seed": int(record["seed"]), "rows": rows,
                      "source_code_sha256": str(record["code_sha256"]),
                      "source_scale_sha256": str(record["scale_sha256"]),
                      "scale_file": f"seed-{int(record['seed'])}/scales.f32le",
                      "scale_bytes": int(scale_path.stat().st_size),
                      "scale_sha256": sha256(scale_path), "layouts": layouts})
    manifest = {
        "schema_version": 1,
        "family": "semantic_r4_k1_simd_layout_materialization_v1",
        "dimensions": DIMENSIONS, "page_bytes": PAGE_BYTES,
        "lanes": list(LANES), "source_manifest_sha256": sha256(args.source_manifest),
        "seeds": seeds,
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")


if __name__ == "__main__":
    main()
