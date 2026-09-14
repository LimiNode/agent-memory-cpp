#!/usr/bin/env python3
"""Materialize mean-of-K16 FP32 coarse vectors for each semantic R4 seed."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


DIMENSIONS = 384
K = 16


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def checked(root: Path, row: dict[str, Any]) -> Path:
    path = root / str(row["file"])
    require(path.is_file() and path.stat().st_size == int(row["bytes"]),
            f"source artifact differs: {path}")
    require(sha256(path) == row["sha256"], f"source SHA differs: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codec-manifest", type=Path, required=True)
    parser.add_argument("--codec-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.codec_manifest.read_text(encoding="utf-8"))
    seeds: list[dict[str, Any]] = []
    args.output_root.mkdir(parents=True, exist_ok=True)
    for record in manifest["seeds"]:
        seed = int(record["seed"])
        root = args.codec_root / f"seed-{seed}"
        mappings = {str(row["role"]): row for row in record["mappings"]}
        counts_path = checked(root, mappings["address_counts"])
        offsets_path = checked(root, mappings["address_offsets"])
        counts = np.fromfile(counts_path, dtype=np.uint8)
        offsets = np.fromfile(offsets_path, dtype="<u4").astype(np.int64)
        clipped = np.minimum(counts, K).astype(np.int64)
        require(np.all(clipped > 0), f"empty address count in seed {seed}")
        fp32 = next(row for row in record["representations"] if row["id"] == "fp32")
        fp32_path = checked(root, fp32)
        records = np.memmap(fp32_path, mode="r", dtype="<f4",
                            shape=(int(record["representative_count"]), DIMENSIONS))
        target = args.output_root / f"seed-{seed}-mean-k{K}.f32le"
        with target.open("wb") as stream:
            for start in range(0, len(clipped), 1_024):
                stop = min(start + 1_024, len(clipped))
                positions = np.concatenate([
                    offsets[address] + np.arange(int(clipped[address]), dtype=np.int64)
                    for address in range(start, stop)])
                values = np.asarray(records[positions], dtype=np.float32)
                boundaries = np.cumsum(np.concatenate(([0], clipped[start:stop])))
                means = (np.add.reduceat(values, boundaries[:-1], axis=0) /
                         clipped[start:stop, None]).astype("<f4")
                stream.write(means.tobytes(order="C"))
        seeds.append({"seed": seed, "rows": int(len(clipped)), "dimensions": DIMENSIONS,
                      "k": K, "file": target.name, "bytes": target.stat().st_size,
                      "sha256": sha256(target), "source_counts_sha256": sha256(counts_path),
                      "source_offsets_sha256": sha256(offsets_path),
                      "source_fp32_sha256": sha256(fp32_path)})
    output = {"schema_version": 1, "family": "semantic_r4_mean_coarse_materialization_v1",
              "execution_status": "EXECUTED", "k": K, "dimensions": DIMENSIONS,
              "codec_manifest_sha256": sha256(args.codec_manifest), "seeds": seeds}
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"materialize-r4-mean-coarse: {error}")
