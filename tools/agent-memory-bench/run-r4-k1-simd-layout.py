#!/usr/bin/env python3
"""Benchmark scalar row-major versus AVX2 AoSoA K1 coarse scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


SEEDS = (2026082701, 2026082702, 2026082703)
MODES = ("row_scalar", "aosoa_avx2")
LANES = (8, 10, 16, 32)
QUERIES = 152
PASSES = 3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"samples": int(array.size), "mean": float(array.mean()),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "p99": float(np.percentile(array, 99)),
            "min": float(array.min()), "max": float(array.max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--materialized-root", type=Path, required=True)
    parser.add_argument("--queries-root", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    require(manifest["family"] == "semantic_r4_k1_simd_layout_materialization_v1",
            "K1 SIMD layout manifest family differs")
    args.work_root.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    native_outputs: list[dict[str, Any]] = []
    for seed_record in manifest["seeds"]:
        seed = int(seed_record["seed"])
        rows = int(seed_record["rows"])
        scales = args.materialized_root / str(seed_record["scale_file"])
        queries = args.queries_root / f"seed-{seed}" / "queries.f32le"
        require(scales.is_file() and sha256(scales) == seed_record["scale_sha256"],
                f"K1 scale binding differs: {scales}")
        require(queries.is_file() and queries.stat().st_size == QUERIES * 384 * 4,
                f"K1 query fixture differs: {queries}")
        query_sha = sha256(queries)
        layouts = {str(item["id"]): item for item in seed_record["layouts"]}
        modes = [("row_scalar", 1, layouts["row_major_int8"])]
        modes.extend(("aosoa_avx2", lanes, layouts[f"aosoa{lanes}_int8"])
                     for lanes in LANES)
        for mode, lanes, layout in modes:
            data = args.materialized_root / str(layout["file"])
            require(data.is_file() and data.stat().st_size == int(layout["bytes"]) and
                    sha256(data) == layout["sha256"], f"K1 layout binding differs: {data}")
            output = args.work_root / f"seed-{seed}-{mode}-{lanes}.json"
            command = [str(args.native_executable), "--benchmark", str(data), str(rows),
                       str(scales), str(queries), str(QUERIES), str(lanes), mode,
                       str(PASSES), str(output)]
            subprocess.run(command, check=True, timeout=900,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            result = json.loads(output.read_text(encoding="utf-8"))
            require(result["family"] == "semantic_r4_k1_simd_layout_samples_v1" and
                    len(result["samples"]) == QUERIES * PASSES,
                    f"K1 native sample matrix differs: {seed}/{mode}/{lanes}")
            native_outputs.append({"seed": seed, "mode": mode, "lanes": lanes,
                                   "path": str(output), "bytes": output.stat().st_size,
                                   "sha256": sha256(output), "query_sha256": query_sha,
                                   "layout_sha256": layout["sha256"]})
            for row in result["samples"]:
                samples.append({"seed": seed, **row})
    scalar = {(row["seed"], row["query"], row["pass"]): row
              for row in samples if row["mode"] == "row_scalar"}
    scalar_prefix = {(row["seed"], row["query"]): row for row in samples
                     if row["mode"] == "row_scalar" and "prefix_ids" in row}
    prefix_overlaps: dict[tuple[int, int, str, int], dict[str, float]] = {}
    for row in samples:
        key = (row["seed"], row["query"], row["pass"])
        baseline = scalar[key]
        row["top128_identity"] = bool(row["top128_checksum"] == baseline["top128_checksum"])
        row["score_checksum_identity"] = bool(row["score_checksum"] == baseline["score_checksum"])
        row["top128_overlap"] = float(len(set(row["top128_ids"]) &
                                         set(baseline["top128_ids"])) / 128.0)
        if "prefix_ids" in row:
            reference = scalar_prefix[(row["seed"], row["query"])]
            row["prefix_overlaps"] = {
                prefix: float(len(set(row["prefix_ids"][prefix]) &
                                  set(reference["prefix_ids"][prefix])) /
                              float(int(prefix)))
                for prefix in ("8192", "16384")}
            prefix_overlaps[(row["seed"], row["query"], row["mode"],
                             row["lanes"])] = row["prefix_overlaps"]
        else:
            row["prefix_overlaps"] = None
        row["prefix_set_identity"] = {
            prefix: bool(row["prefix_set_checksums"][prefix] ==
                         baseline["prefix_set_checksums"][prefix])
            for prefix in ("128", "8192", "16384")}
        require(row["top128_overlap"] >= 0.99,
                f"K1 top-128 overlap differs: {key}/{row['mode']}/{row['lanes']}")
        if row["mode"] != "row_scalar" and "prefix_ids" in row:
            require(min(row["prefix_overlaps"].values()) >= 0.999,
                    f"K1 prefix overlap differs: {key}/{row['mode']}/{row['lanes']}")
    for row in samples:
        if row["prefix_overlaps"] is None:
            if row["mode"] == "row_scalar":
                row["prefix_overlaps"] = {"8192": 1.0, "16384": 1.0}
            else:
                row["prefix_overlaps"] = prefix_overlaps[
                    (row["seed"], row["query"], row["mode"], row["lanes"])]
    summaries = []
    for mode, lanes in (("row_scalar", 1), *(('aosoa_avx2', value) for value in LANES)):
        selected = [row for row in samples if row["mode"] == mode and row["lanes"] == lanes]
        summaries.append({"mode": mode, "lanes": lanes,
                          "samples": len(selected),
                          "elapsed_ms": stats([float(row["elapsed_ms"]) for row in selected]),
                          "footprint_by_seed": [{
                              "seed": seed,
                              "logical_bytes": int(next(row for row in selected
                                                         if row["seed"] == seed)["logical_bytes"]),
                              "physical_bytes": int(next(row for row in selected
                                                          if row["seed"] == seed)["physical_bytes"]),
                              "logical_pages_4k": int(next(row for row in selected
                                                            if row["seed"] == seed)["logical_pages_4k"]),
                              "physical_pages_4k": int(next(row for row in selected
                                                             if row["seed"] == seed)["physical_pages_4k"]),
                          } for seed in SEEDS],
                          "tile_bytes": int(selected[0]["tile_bytes"]),
                          "tile_pages_4k": int(selected[0]["tile_pages_4k"]),
                          "top128_identity": all(row["top128_identity"] for row in selected),
                          "minimum_top128_overlap": float(min(row["top128_overlap"] for row in selected)),
                          "prefix_set_identity": {prefix: all(
                              row["prefix_set_identity"][prefix] for row in selected)
                              for prefix in ("128", "8192", "16384")},
                          "minimum_prefix_overlap": {prefix: float(min(
                              row["prefix_overlaps"][prefix] for row in selected
                              if np.isfinite(row["prefix_overlaps"][prefix])))
                              for prefix in ("8192", "16384")},
                          "score_checksum_identity_fraction": float(
                              np.mean([row["score_checksum_identity"] for row in selected]))})
    raw = {"schema_version": 1, "family": "semantic_r4_k1_simd_layout_v1",
           "samples": samples}
    raw_bytes = (json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    receipt = {
        "schema_version": 1, "family": "semantic_r4_k1_simd_layout_v1",
        "execution_status": "EXECUTED", "production_activation": False,
        "seeds": list(SEEDS), "queries": QUERIES, "passes": PASSES,
        "modes": [{"mode": "row_scalar", "lanes": 1},
                  *[{"mode": "aosoa_avx2", "lanes": value} for value in LANES]],
        "manifest_sha256": sha256(args.manifest),
        "native_executable_sha256": sha256(args.native_executable),
        "runner_sha256": sha256(Path(__file__)), "native_outputs": native_outputs,
        "summaries": summaries,
        "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                       "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                       "samples": len(samples)},
        "protocol": {"page_bytes": 4096, "physical_page_bytes_measured": False,
                      "logical_payload_bytes_reported": True,
                      "os_page_cache_controlled": False,
                      "quality_identity": "top128 checksum against row-major scalar",
                      "teacher_ids_used": False},
        "environment": {"platform": platform.platform(),
                        "python": platform.python_version()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"run-r4-k1-simd-layout: {error}")
