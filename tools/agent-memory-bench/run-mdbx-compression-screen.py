#!/usr/bin/env python3
"""Z0 lossless-compression screen for canonical packed document payloads.

This is an offline evidence screen.  It deliberately does not change MDBX
layout or claim a serving result: blocks are compressed in memory and only
ratio and codec throughput are measured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
try:
    import zstandard as zstd
except ModuleNotFoundError:  # The CTest contract can still run without the optional wheel.
    zstd = None


CODECS = ("fp32", "int8", "lsq32", "tq1", "tq1-pq8")
WIDTHS = {"fp32": 1536, "int8": 392, "lsq32": 36, "tq1": 52, "tq1-pq8": 64}
DEFAULT_BLOCKS = (4096, 16384, 65536)
DEFAULT_LEVELS = (1, 3)
DOCUMENTS = 1_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def shuffled(block: bytes, inverse: bool = False) -> bytes:
    require(len(block) % 4 == 0, "shuffle block is not FP32-aligned")
    matrix = np.frombuffer(block, dtype=np.uint8).reshape(-1, 4)
    return matrix.T.copy().tobytes() if not inverse else matrix.reshape(4, -1).T.copy().tobytes()


def screen_arm(path: Path, codec: str, block_target: int, level: int,
               transform: str, decode_repeats: int) -> dict:
    if zstd is None:
        raise RuntimeError("zstandard package is required for the Z0 measurement")
    width = WIDTHS[codec]
    require(path.is_file(), f"payload missing: {codec}")
    require(path.stat().st_size == DOCUMENTS * width, f"payload shape differs: {codec}")
    rows_per_block = max(1, block_target // width)
    block_bytes = rows_per_block * width
    compressor = zstd.ZstdCompressor(level=level)
    decompressor = zstd.ZstdDecompressor()
    compressed_bytes = 0
    blocks = 0
    compression_seconds = 0.0
    decompression_seconds = 0.0
    with path.open("rb") as stream:
        while True:
            raw = stream.read(block_bytes)
            if not raw:
                break
            blocks += 1
            source = shuffled(raw) if transform == "byte_shuffle" else raw
            start = time.perf_counter()
            packed = compressor.compress(source)
            compression_seconds += time.perf_counter() - start
            compressed_bytes += len(packed)
            start = time.perf_counter()
            for _ in range(decode_repeats):
                restored = decompressor.decompress(packed)
                if transform == "byte_shuffle":
                    restored = shuffled(restored, inverse=True)
                require(restored == raw, f"round-trip mismatch: {codec}/{block_target}/{level}/{transform}")
            decompression_seconds += time.perf_counter() - start
    raw_bytes = DOCUMENTS * width
    return {
        "codec": codec,
        "transform": transform,
        "zstd_level": level,
        "target_block_bytes": block_target,
        "rows_per_block": rows_per_block,
        "actual_block_bytes": block_bytes,
        "blocks": blocks,
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": raw_bytes / compressed_bytes,
        "saving_fraction": 1.0 - compressed_bytes / raw_bytes,
        "compression_throughput_gib_s": raw_bytes / max(compression_seconds, 1e-12) / (1024 ** 3),
        "decompression_throughput_gib_s": (raw_bytes * decode_repeats) / max(decompression_seconds, 1e-12) / (1024 ** 3),
        "decode_repeats": decode_repeats,
        "carry_forward_if_saving_at_least_5_percent": (1.0 - compressed_bytes / raw_bytes) >= 0.05,
    }


def self_test() -> None:
    if zstd is None:
        print("run-mdbx-compression-screen self-test SKIP (zstandard package unavailable)")
        return
    raw = np.arange(4096, dtype=np.float32).tobytes()
    packed = zstd.ZstdCompressor(level=1).compress(shuffled(raw))
    restored = shuffled(zstd.ZstdDecompressor().decompress(packed), inverse=True)
    require(restored == raw, "byte-shuffle round-trip differs")
    require(WIDTHS["tq1-pq8"] == 64 and WIDTHS["int8"] == 392, "canonical widths differ")
    print("run-mdbx-compression-screen self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--fp32", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--block-bytes", type=int, nargs="+", default=list(DEFAULT_BLOCKS))
    parser.add_argument("--levels", type=int, nargs="+", default=list(DEFAULT_LEVELS))
    parser.add_argument("--decode-repeats", type=int, default=1)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.fixture or not args.fp32 or not args.output:
        parser.error("--fixture, --fp32 and --output are required")
    require(args.decode_repeats >= 1, "decode repeats must be positive")
    manifest_path = args.fixture / "fixture.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("documents") == DOCUMENTS, "fixture document count differs")
    paths = {codec: Path(manifest["payloads"][codec]["path"]) for codec in ("int8", "lsq32", "tq1", "tq1-pq8")}
    paths["fp32"] = args.fp32
    for codec, path in paths.items():
        require(path.is_file() and path.stat().st_size == DOCUMENTS * WIDTHS[codec], f"canonical {codec} source differs")
    rows: list[dict] = []
    for codec in CODECS:
        transforms = ("plain", "byte_shuffle") if codec == "fp32" else ("plain",)
        for block_target in args.block_bytes:
            for level in args.levels:
                for transform in transforms:
                    rows.append(screen_arm(paths[codec], codec, block_target, level, transform, args.decode_repeats))
    result = {
        "schema_version": 1,
        "family": "mdbx_z0_compression_screen_v1",
        "status": "EXECUTED",
        "stage": "Z0_offline_compressibility_screen",
        "documents": DOCUMENTS,
        "fixture_manifest_path": str(manifest_path.resolve()),
        "fixture_manifest_sha256": sha256(manifest_path),
        "source_sha256": {codec: sha256(path) for codec, path in paths.items()},
        "source_paths": {codec: str(path.resolve()) for codec, path in paths.items()},
        "canonical_source_sha256": {codec: manifest["payloads"][codec].get("source_sha256", {}) for codec in ("int8", "lsq32", "tq1", "tq1-pq8")},
        "block_target_bytes": args.block_bytes,
        "zstd_levels": args.levels,
        "saving_filter_fraction": 0.05,
        "rows": rows,
        "limitations": ["in-memory Zstd screen; no MDBX serving timing", "canonical document ordering only", "5% filter is a research carry-forward rule, not a product threshold"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "EXECUTED", "rows": len(rows), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"run-mdbx-compression-screen: {error}")
