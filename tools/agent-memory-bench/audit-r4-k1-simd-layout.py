#!/usr/bin/env python3
"""Fail-closed audit for the K1 SIMD/page-matched benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152
PASSES = 3
MODES = (("row_scalar", 1), ("aosoa_avx2", 8), ("aosoa_avx2", 10),
         ("aosoa_avx2", 16), ("aosoa_avx2", 32))


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


def assert_aggregate(actual: dict[str, float], values: list[float], label: str) -> None:
    expected = aggregate(values)
    for key, value in expected.items():
        require(math.isclose(float(actual[key]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate differs: {label}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(manifest["family"] == "semantic_r4_k1_simd_layout_materialization_v1" and
            receipt["family"] == "semantic_r4_k1_simd_layout_v1" and
            raw["family"] == receipt["family"] and receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False, "K1 SIMD identity/status differs")
    require(receipt["manifest_sha256"] == sha256(args.manifest) and
            receipt["native_executable_sha256"] == sha256(args.native_executable) and
            receipt["raw_output"]["sha256"] == sha256(args.raw), "K1 SIMD receipt binding differs")
    manifest_by_seed = {int(record["seed"]): record for record in manifest["seeds"]}
    for record in manifest["seeds"]:
        scale = args.manifest.parent / str(record["scale_file"])
        require(scale.is_file() and scale.stat().st_size == int(record["scale_bytes"]) and
                sha256(scale) == record["scale_sha256"], f"K1 scale artifact differs: {scale}")
        for layout in record["layouts"]:
            path = args.manifest.parent / str(layout["file"])
            require(path.is_file() and path.stat().st_size == int(layout["bytes"]) and
                    sha256(path) == layout["sha256"], f"K1 layout artifact differs: {path}")
    require(len(receipt["native_outputs"]) == len(SEEDS) * len(MODES),
            "K1 native output matrix differs")
    for output in receipt["native_outputs"]:
        path = Path(output["path"])
        require(path.is_file() and path.stat().st_size == int(output["bytes"]) and
                sha256(path) == output["sha256"], f"K1 native output binding differs: {path}")
        seed = int(output["seed"])
        mode = str(output["mode"])
        lanes = int(output["lanes"])
        expected = next(item for item in manifest_by_seed[seed]["layouts"]
                        if (mode == "row_scalar" and item["id"] == "row_major_int8") or
                        (mode == "aosoa_avx2" and item["id"] == f"aosoa{lanes}_int8"))
        require(output["layout_sha256"] == expected["sha256"],
                f"K1 native layout binding differs: {seed}/{mode}/{lanes}")
        require(output["query_sha256"] ==
                "abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191",
                "K1 query fixture binding differs")
    samples = raw["samples"]
    expected = len(SEEDS) * QUERIES * PASSES * len(MODES)
    require(len(samples) == expected, "K1 SIMD sample count differs")
    rows_by_seed = {int(record["seed"]): int(record["rows"])
                    for record in manifest["seeds"]}
    identities: set[tuple[int, int, int, str, int]] = set()
    scalar: dict[tuple[int, int, int], dict[str, object]] = {}
    scalar_prefix: dict[tuple[int, int], dict[str, object]] = {}
    for row in samples:
        key = (int(row["seed"]), int(row["query"]), int(row["pass"]),
               str(row["mode"]), int(row["lanes"]))
        require(key not in identities and key[0] in SEEDS and 0 <= key[1] < QUERIES and
                0 <= key[2] < PASSES and (key[3], key[4]) in MODES,
                f"K1 SIMD sample identity differs: {key}")
        identities.add(key)
        expected_layout = next(item for item in manifest_by_seed[key[0]]["layouts"]
                               if (key[3] == "row_scalar" and item["id"] == "row_major_int8") or
                               (key[3] == "aosoa_avx2" and item["id"] == f"aosoa{key[4]}_int8"))
        require(int(row["logical_bytes"]) == rows_by_seed[key[0]] * 384 and
                int(row["physical_bytes"]) == int(expected_layout["physical_bytes"]),
                "K1 logical bytes missing")
        require(int(row["physical_pages_4k"]) ==
                (int(row["physical_bytes"]) + 4095) // 4096 and
                int(row["logical_pages_4k"]) ==
                (int(row["logical_bytes"]) + 4095) // 4096,
                f"K1 page accounting differs: {key}")
        require(float(row["elapsed_ms"]) >= 0.0, f"K1 elapsed time differs: {key}")
        if key[3] == "row_scalar":
            scalar[key[:3]] = row
            if "prefix_ids" in row:
                scalar_prefix[(key[0], key[1])] = row
    require(len(identities) == expected and len(scalar) == len(SEEDS) * QUERIES * PASSES,
            "K1 SIMD matrix incomplete")
    for row in samples:
        key = (int(row["seed"]), int(row["query"]), int(row["pass"]))
        baseline = scalar[key]
        ids = [int(value) for value in row["top128_ids"]]
        baseline_ids = [int(value) for value in baseline["top128_ids"]]
        overlap = len(set(ids) & set(baseline_ids)) / 128.0
        require(len(ids) == 128 and len(set(ids)) == 128 and overlap >= 0.99 and
                math.isclose(float(row["top128_overlap"]), overlap,
                             rel_tol=0.0, abs_tol=1e-12),
                f"K1 top-128 overlap differs: {key}/{row['mode']}/{row['lanes']}")
        for prefix in ("128", "8192", "16384"):
            identity = int(row["prefix_set_checksums"][prefix]) == int(
                baseline["prefix_set_checksums"][prefix])
            require(bool(row["prefix_set_identity"][prefix]) == identity,
                    f"K1 prefix-set flag differs: {key}/{prefix}/{row['mode']}/{row['lanes']}")
        if "prefix_ids" in row:
            reference = scalar_prefix[(key[0], key[1])]
            for prefix in ("8192", "16384"):
                overlap = len(set(row["prefix_ids"][prefix]) &
                              set(reference["prefix_ids"][prefix])) / float(int(prefix))
                require(overlap >= 0.999 and
                        math.isclose(float(row["prefix_overlaps"][prefix]), overlap,
                                     rel_tol=0.0, abs_tol=1e-12),
                        f"K1 prefix overlap differs: {key}/{prefix}/{row['mode']}/{row['lanes']}")
        elif row["mode"] != "row_scalar":
            require(float(row["prefix_overlaps"]["8192"]) >= 0.999 and
                    float(row["prefix_overlaps"]["16384"]) >= 0.999,
                    f"K1 carried prefix overlap differs: {key}/{row['mode']}/{row['lanes']}")
        require(bool(row["score_checksum_identity"]) ==
                (int(row["score_checksum"]) == int(baseline["score_checksum"])),
                f"K1 score checksum flag differs: {key}/{row['mode']}/{row['lanes']}")
    summaries = {(str(row["mode"]), int(row["lanes"])): row
                 for row in receipt["summaries"]}
    require(set(summaries) == set(MODES), "K1 summary matrix differs")
    for mode, lanes in MODES:
        selected = [row for row in samples if row["mode"] == mode and int(row["lanes"]) == lanes]
        summary = summaries[(mode, lanes)]
        require(int(summary["samples"]) == len(selected) and
                float(summary["minimum_top128_overlap"]) >= 0.99 and
                float(summary["minimum_prefix_overlap"]["8192"]) >= 0.999 and
                float(summary["minimum_prefix_overlap"]["16384"]) >= 0.999,
                f"K1 summary identity differs: {mode}/{lanes}")
        assert_aggregate(summary["elapsed_ms"], [float(row["elapsed_ms"]) for row in selected],
                         f"elapsed/{mode}/{lanes}")
        footprint = {int(row["seed"]): row for row in summary["footprint_by_seed"]}
        require(set(footprint) == set(SEEDS), f"K1 summary footprint matrix differs: {mode}/{lanes}")
        for seed in SEEDS:
            sample = next(row for row in selected if int(row["seed"]) == seed)
            require(int(footprint[seed]["logical_bytes"]) == int(sample["logical_bytes"]) and
                    int(footprint[seed]["physical_bytes"]) == int(sample["physical_bytes"]) and
                    int(footprint[seed]["logical_pages_4k"]) == int(sample["logical_pages_4k"]) and
                    int(footprint[seed]["physical_pages_4k"]) == int(sample["physical_pages_4k"]),
                    f"K1 summary footprint differs: {mode}/{lanes}/{seed}")
    print(json.dumps({"family": "semantic_r4_k1_simd_layout_audit_v1", "status": "PASS",
                      "samples": len(samples), "modes": len(MODES)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-simd-layout: {error}")
