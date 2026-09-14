#!/usr/bin/env python3
"""Independent fail-closed audit for the K1 page/read benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
CANDIDATES = 128
PASSES = 2
INVOCATIONS = (0, 1)
OPERATIONS = ("full_scan", "candidate_gather")
LAYOUTS = (("row_scalar", 1, "row_major_int8"),
           ("aosoa_avx2", 16, "aosoa16_int8"),
           ("aosoa_avx2", 32, "aosoa32_int8"))
DIMENSIONS = 384
PAGE_BYTES = 4096


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"samples": int(array.size), "mean": float(array.mean()),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "p99": float(np.percentile(array, 99)), "min": float(array.min()),
            "max": float(array.max())}


def assert_aggregate(actual: dict[str, Any], values: list[float], label: str) -> None:
    expected = aggregate(values)
    for key, value in expected.items():
        require(math.isclose(float(actual[key]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate differs: {label}/{key}")


def expected_candidate_stats(ids: list[int], rows: int, lanes: int, mode: str,
                             file_bytes: int) -> tuple[int, int, int]:
    ranges: set[int] = set()
    pages: set[int] = set()
    requested = 0
    tile_bytes = lanes * DIMENSIONS
    for value in ids:
        require(0 <= value < rows, "candidate id out of range")
        tile = value if mode == "row_scalar" else value // lanes
        offset = tile * DIMENSIONS if mode == "row_scalar" else tile * tile_bytes
        size = DIMENSIONS if mode == "row_scalar" else tile_bytes
        if offset in ranges:
            continue
        ranges.add(offset)
        requested += size
        pages.update(range(offset // PAGE_BYTES, (offset + size - 1) // PAGE_BYTES + 1))
    require(requested <= file_bytes, "candidate requested bytes exceed layout")
    return requested, len(pages), len(ranges)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--source-raw", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(manifest["family"] == "semantic_r4_k1_simd_layout_materialization_v1" and
            receipt["family"] == "semantic_r4_k1_page_read_v1" and
            raw["family"] == receipt["family"] and receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False, "page-read identity/status differs")
    require(receipt["manifest_sha256"] == sha256(args.manifest) and
            receipt["source_raw_sha256"] == sha256(args.source_raw),
            "page-read source raw binding differs")
    require(receipt["native_executable_sha256"] == sha256(args.native_executable) and
            receipt["raw_output"]["sha256"] == sha256(args.raw),
            "page-read receipt binding differs")
    manifest_by_seed = {int(record["seed"]): record for record in manifest["seeds"]}
    candidates: dict[int, list[int]] = {}
    for seed in SEEDS:
        path = args.work_root / f"seed-{seed}-top128.u32le"
        require(path.is_file() and path.stat().st_size == QUERIES * CANDIDATES * 4,
                f"candidate fixture missing: {seed}")
        values = np.fromfile(path, dtype="<u4").astype(np.int64).tolist()
        require(len(values) == QUERIES * CANDIDATES and sha256(path) == next(
            item["candidate_sha256"] for item in receipt["native_outputs"]
            if int(item["seed"]) == seed), f"candidate fixture binding differs: {seed}")
        candidates[seed] = [int(value) for value in values]
        for query in range(QUERIES):
            row = values[query * CANDIDATES:(query + 1) * CANDIDATES]
            require(len(set(row)) == CANDIDATES, f"candidate duplicate: {seed}/{query}")
    for seed, record in manifest_by_seed.items():
        for layout in record["layouts"]:
            path = args.manifest.parent / str(layout["file"])
            require(path.is_file() and path.stat().st_size == int(layout["bytes"]) and
                    sha256(path) == layout["sha256"], f"layout binding differs: {path}")
    expected = len(SEEDS) * len(LAYOUTS) * len(INVOCATIONS) * PASSES * (1 + QUERIES)
    samples = raw["samples"]
    require(len(samples) == expected, "page-read sample count differs")
    for output in receipt["native_outputs"]:
        path = Path(output["path"])
        require(path.is_file() and path.stat().st_size == int(output["bytes"]) and
                sha256(path) == output["sha256"] and
                output["candidate_sha256"] == sha256(args.work_root /
                                                       f"seed-{int(output['seed'])}-top128.u32le"),
                f"native output binding differs: {path}")
    identities: set[tuple[int, int, str, int, str, int, int]] = set()
    file_sizes: dict[tuple[int, str, int], int] = {}
    for row in samples:
        seed = int(row["seed"]); query = int(row["query"]); mode = str(row["mode"])
        lanes = int(row["lanes"]); operation = str(row["operation"])
        invocation = int(row["invocation"]); measured_pass = int(row["pass"])
        key = (seed, query, mode, lanes, operation, invocation, measured_pass)
        query_limit = 1 if operation == "full_scan" else QUERIES
        require(key not in identities and seed in SEEDS and 0 <= query < query_limit and
                0 <= measured_pass < PASSES and invocation in INVOCATIONS and
                (mode, lanes, next(item[2] for item in LAYOUTS if item[0] == mode and item[1] == lanes))
                in LAYOUTS and operation in OPERATIONS, f"sample identity differs: {key}")
        identities.add(key)
        record = manifest_by_seed[seed]
        layout_id = next(item[2] for item in LAYOUTS if item[0] == mode and item[1] == lanes)
        layout = next(item for item in record["layouts"] if item["id"] == layout_id)
        file_bytes = int(layout["bytes"]); rows = int(record["rows"])
        file_sizes[(seed, mode, lanes)] = file_bytes
        require(int(row["rows"]) == rows and int(row["logical_payload_bytes"]) == rows * DIMENSIONS and
                int(row["file_bytes"]) == file_bytes and float(row["elapsed_ms"]) >= 0.0,
                f"page-read payload metadata differs: {key}")
        if operation == "full_scan":
            expected_bytes = file_bytes
            expected_pages = (file_bytes + PAGE_BYTES - 1) // PAGE_BYTES
            expected_calls = (file_bytes + (1 << 20) - 1) // (1 << 20)
        else:
            first = query * CANDIDATES
            expected_bytes, expected_pages, expected_calls = expected_candidate_stats(
                candidates[seed][first:first + CANDIDATES], rows, lanes, mode, file_bytes)
        require(int(row["requested_read_bytes"]) == expected_bytes and
                int(row["unique_file_pages_4k"]) == expected_pages and
                int(row["read_calls"]) == expected_calls and int(row["read_calls"]) > 0,
                f"page-read accounting differs: {key}")
    require(len(identities) == expected, "page-read matrix incomplete")
    summaries = {(str(row["mode"]), int(row["lanes"]), str(row["operation"]),
                  int(row["invocation"])): row for row in receipt["summaries"]}
    require(set(summaries) == {(mode, lanes, operation, invocation)
                               for mode, lanes, _ in LAYOUTS for operation in OPERATIONS
                               for invocation in INVOCATIONS}, "summary matrix differs")
    for key, summary in summaries.items():
        mode, lanes, operation, invocation = key
        selected = [row for row in samples if row["mode"] == mode and int(row["lanes"]) == lanes
                    and row["operation"] == operation and int(row["invocation"]) == invocation]
        require(int(summary["samples"]) == len(selected), f"summary count differs: {key}")
        assert_aggregate(summary["elapsed_ms"], [float(row["elapsed_ms"]) for row in selected],
                         f"elapsed/{key}")
        for metric in ("requested_read_bytes", "unique_file_pages_4k", "read_calls"):
            assert_aggregate(summary[metric], [float(row[metric]) for row in selected],
                             f"{metric}/{key}")
    print(json.dumps({"family": "semantic_r4_k1_page_read_audit_v1", "status": "PASS",
                      "samples": len(samples), "layouts": len(LAYOUTS),
                      "operations": len(OPERATIONS)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-page-read: {error}")
