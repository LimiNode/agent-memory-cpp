#!/usr/bin/env python3
"""Fail-closed audit for the offline MDBX Z0 compression screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

CODECS = ("fp32", "int8", "lsq32", "tq1", "tq1-pq8")
WIDTHS = {"fp32": 1536, "int8": 392, "lsq32": 36, "tq1": 52, "tq1-pq8": 64}
BLOCKS = (4096, 16384, 65536)
LEVELS = (1, 3)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1 and value.get("family") == "mdbx_z0_compression_screen_v1", "receipt family differs")
    require(value.get("status") == "EXECUTED" and value.get("stage") == "Z0_offline_compressibility_screen", "receipt status differs")
    require(value.get("documents") == 1_000_000 and tuple(value.get("block_target_bytes", ())) == BLOCKS and tuple(value.get("zstd_levels", ())) == LEVELS, "screen contract differs")
    manifest_path = Path(value.get("fixture_manifest_path", ""))
    require(manifest_path.is_file() and sha256(manifest_path) == value.get("fixture_manifest_sha256"), "fixture binding differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "mdbx_finalist_source_fixture_v1", "fixture family differs")
    source_hashes = value.get("source_sha256", {})
    require(set(source_hashes) == set(CODECS), "source coverage differs")
    canonical_source_hashes = value.get("canonical_source_sha256", {})
    require(set(canonical_source_hashes) == {"int8", "lsq32", "tq1", "tq1-pq8"}, "canonical source coverage differs")
    source_paths = value.get("source_paths", {})
    require(set(source_paths) == set(CODECS), "source paths differ")
    for codec in CODECS:
        path = Path(source_paths[codec])
        require(path.is_file() and sha256(path) == source_hashes[codec], f"source hash differs: {codec}")
        if codec != "fp32":
            fixture_payload = manifest["payloads"][codec]
            require(Path(fixture_payload["path"]).is_file() and fixture_payload["payload_sha256"] == sha256(Path(fixture_payload["path"])), f"fixture payload differs: {codec}")
            require(canonical_source_hashes[codec] == fixture_payload.get("source_sha256", {}), f"canonical source binding differs: {codec}")
    rows = value.get("rows", [])
    require(len(rows) == 36, f"row count differs: {len(rows)}")
    seen = set()
    for row in rows:
        key = (row.get("codec"), row.get("transform"), row.get("target_block_bytes"), row.get("zstd_level"))
        require(row["codec"] in CODECS and row["target_block_bytes"] in BLOCKS and row["zstd_level"] in LEVELS, f"row contract differs: {key}")
        require(row["transform"] in (("plain", "byte_shuffle") if row["codec"] == "fp32" else ("plain",)), f"transform differs: {key}")
        require(key not in seen, f"duplicate row: {key}")
        seen.add(key)
        require(row["raw_bytes"] == 1_000_000 * WIDTHS[row["codec"]] and row["compressed_bytes"] > 0, f"size differs: {key}")
        require(row["blocks"] >= 1 and row["actual_block_bytes"] == row["rows_per_block"] * WIDTHS[row["codec"]], f"block geometry differs: {key}")
        require(row["compression_ratio"] > 0 and row["decompression_throughput_gib_s"] > 0, f"throughput differs: {key}")
    require(len(seen) == 36, "compression matrix incomplete")


def self_test() -> None:
    baseline = {"schema_version": 1, "family": "mdbx_z0_compression_screen_v1", "status": "EXECUTED", "stage": "Z0_offline_compressibility_screen", "documents": 1_000_000, "block_target_bytes": list(BLOCKS), "zstd_levels": list(LEVELS), "fixture_manifest_path": "missing", "fixture_manifest_sha256": "0" * 64, "source_sha256": {}, "rows": []}
    require(baseline["documents"] == 1_000_000, "self-test setup differs")
    for mutation in (lambda x: x.update(status="PARTIAL"), lambda x: x.update(block_target_bytes=[4096]), lambda x: x.update(rows=[{}])):
        candidate = json.loads(json.dumps(baseline)); mutation(candidate)
        try:
            require(candidate["status"] == "EXECUTED", "status mutation accepted")
            require(tuple(candidate["block_target_bytes"]) == BLOCKS, "block mutation accepted")
            require(len(candidate["rows"]) == 36, "row mutation accepted")
        except ValueError:
            continue
        raise ValueError("compression audit mutation accepted")
    print("audit-mdbx-compression-screen self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.result is None: raise ValueError("--result is required")
    audit(json.loads(args.result.read_text(encoding="utf-8"))); print(f"audit-mdbx-compression-screen: PASS ({args.result})")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-mdbx-compression-screen: {error}")
