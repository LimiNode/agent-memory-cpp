#!/usr/bin/env python3
"""Materialize the canonical packed ordinal THQ4 table from bound E5 inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

DOCUMENTS, DIMENSIONS, THRESHOLDS_PER_DIMENSION = 1_000_000, 384, 3
PACKED_BYTES = DOCUMENTS * DIMENSIONS // 4


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_packer():
    path = Path(__file__).with_name("thq-packed-codecs.py")
    spec = importlib.util.spec_from_file_location("canonical_thq_packer", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["canonical_thq_packer"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=50_000)
    args = parser.parse_args()
    if args.chunk_rows <= 0:
        parser.error("--chunk-rows must be positive")
    if args.documents.stat().st_size != DOCUMENTS * DIMENSIONS * 4:
        raise RuntimeError("documents must be exactly 1M float32x384 rows")
    if args.thresholds.stat().st_size != DIMENSIONS * THRESHOLDS_PER_DIMENSION * 4:
        raise RuntimeError("thresholds must be exactly 384x3 float32 values")
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(DIMENSIONS, THRESHOLDS_PER_DIMENSION)
    if not np.isfinite(thresholds).all() or not np.all(thresholds[:, 0] < thresholds[:, 1]) or not np.all(thresholds[:, 1] < thresholds[:, 2]):
        raise RuntimeError("thresholds must be finite and strictly increasing")
    packer = load_packer()
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(DOCUMENTS, DIMENSIONS))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with args.output.open("wb") as stream:
        for start in range(0, DOCUMENTS, args.chunk_rows):
            block = np.asarray(documents[start:start + args.chunk_rows], dtype=np.float32)
            levels = np.sum(block[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
            packed = packer.pack_symbols(levels, 2)
            if packed.shape != (len(block), DIMENSIONS // 4):
                raise RuntimeError("packed THQ4 shape differs")
            raw = packed.tobytes()
            stream.write(raw)
            digest.update(raw)
    if args.output.stat().st_size != PACKED_BYTES:
        raise RuntimeError("packed THQ4 output size differs")
    receipt = {
        "schema_version": 1,
        "family": "canonical_packed_ordinal_thq4_materialization_v1",
        "status": "EXECUTED",
        "dimensions": DIMENSIONS,
        "levels": 4,
        "bits_per_dimension": 2,
        "records": DOCUMENTS,
        "record_bytes": DIMENSIONS // 4,
        "input_hashes": {"documents": sha(args.documents), "thresholds": sha(args.thresholds)},
        "runner_sha256": sha(Path(__file__)),
        "packer_sha256": sha(Path(__file__).with_name("thq-packed-codecs.py")),
        "output": {"bytes": args.output.stat().st_size, "sha256": digest.hexdigest()},
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt["output"], sort_keys=True))


if __name__ == "__main__":
    main()
