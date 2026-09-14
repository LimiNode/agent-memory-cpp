#!/usr/bin/env python3
"""Materialize per-dimension INT8 mean-coarse stores from a frozen manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


DIMENSIONS = 384


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked(root: Path, row: dict[str, object]) -> Path:
    path = root / str(row["file"])
    if not path.is_file() or path.stat().st_size != int(row["bytes"]):
        raise RuntimeError(f"coarse source differs: {path}")
    if sha256(path) != str(row["sha256"]):
        raise RuntimeError(f"coarse source SHA differs: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if source["family"] != "semantic_r4_mean_coarse_materialization_v1":
        raise RuntimeError("coarse source family differs")
    args.output_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for seed in source["seeds"]:
        rows = int(seed["rows"])
        if int(seed["dimensions"]) != DIMENSIONS or int(seed["k"]) != 16:
            raise RuntimeError("coarse source shape differs")
        values = np.memmap(checked(args.source_root, seed), mode="r", dtype="<f4",
                           shape=(rows, DIMENSIONS))
        scales = np.max(np.abs(values), axis=0).astype(np.float32) / np.float32(127.0)
        scales[scales == 0.0] = 1.0
        codes = np.rint(values / scales[None, :]).clip(-127, 127).astype(np.int8)
        code_path = args.output_root / f"seed-{int(seed['seed'])}-mean-k16.i8"
        scale_path = args.output_root / f"seed-{int(seed['seed'])}-mean-k16.scales.f32le"
        codes.tofile(code_path)
        np.asarray(scales, dtype="<f4").tofile(scale_path)
        records.append({
            "seed": int(seed["seed"]), "rows": rows, "dimensions": DIMENSIONS,
            "k": 16, "code_file": code_path.name, "code_bytes": code_path.stat().st_size,
            "code_sha256": sha256(code_path), "scale_file": scale_path.name,
            "scale_bytes": scale_path.stat().st_size, "scale_sha256": sha256(scale_path),
            "source_file": str(seed["file"]), "source_sha256": str(seed["sha256"]),
        })
    manifest = {
        "schema_version": 1,
        "family": "semantic_r4_mean_coarse_int8_materialization_v1",
        "encoding": "signed_int8_per_dimension_symmetric",
        "dimensions": DIMENSIONS, "k": 16,
        "source_manifest_sha256": sha256(args.source_manifest), "seeds": records,
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")


if __name__ == "__main__":
    main()
