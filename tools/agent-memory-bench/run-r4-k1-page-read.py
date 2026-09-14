#!/usr/bin/env python3
"""Measure logical/file-page reads for K1 layouts and real K1 candidates."""
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


def stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"samples": int(array.size), "mean": float(array.mean()),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "p99": float(np.percentile(array, 99)),
            "min": float(array.min()), "max": float(array.max())}


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def candidate_fixture(raw: dict[str, Any], seed: int, path: Path) -> str:
    rows = [row for row in raw["samples"] if int(row["seed"]) == seed and
            str(row["mode"]) == "row_scalar" and int(row["pass"]) == 0]
    require(len(rows) == QUERIES, f"candidate fixture query count differs: {seed}")
    rows.sort(key=lambda row: int(row["query"]))
    ids: list[int] = []
    for query, row in enumerate(rows):
        require(int(row["query"]) == query and len(row["top128_ids"]) == CANDIDATES,
                f"candidate fixture ordering differs: {seed}/{query}")
        values = [int(value) for value in row["top128_ids"]]
        require(len(set(values)) == CANDIDATES, f"candidate fixture duplicate: {seed}/{query}")
        ids.extend(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(np.asarray(ids, dtype="<u4").tobytes())
    return sha256(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--materialized-root", type=Path, required=True)
    parser.add_argument("--source-raw", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    source_raw = json.loads(args.source_raw.read_text(encoding="utf-8"))
    require(manifest["family"] == "semantic_r4_k1_simd_layout_materialization_v1",
            "page-read materialization family differs")
    require(source_raw["family"] == "semantic_r4_k1_simd_layout_v1",
            "page-read source raw family differs")
    args.work_root.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    native_outputs: list[dict[str, Any]] = []
    candidate_sha: dict[int, str] = {}
    for seed_record in manifest["seeds"]:
        seed = int(seed_record["seed"])
        rows = int(seed_record["rows"])
        candidate_path = args.work_root / f"seed-{seed}-top128.u32le"
        candidate_sha[seed] = candidate_fixture(source_raw, seed, candidate_path)
        layouts = {str(item["id"]): item for item in seed_record["layouts"]}
        for mode, lanes, layout_id in LAYOUTS:
            layout = layouts[layout_id]
            data = args.materialized_root / str(layout["file"])
            require(data.is_file() and data.stat().st_size == int(layout["bytes"]) and
                    sha256(data) == layout["sha256"], f"page-read layout binding differs: {data}")
            for operation in OPERATIONS:
                for invocation in INVOCATIONS:
                    output = args.work_root / (
                        f"seed-{seed}-{mode}-{lanes}-{operation}-run{invocation}.json")
                    command = [str(args.native_executable), "--benchmark", str(data), str(rows),
                               str(lanes), mode, str(candidate_path), str(QUERIES),
                               str(CANDIDATES), str(PASSES), operation, str(output)]
                    subprocess.run(command, check=True, timeout=900,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    result = json.loads(output.read_text(encoding="utf-8"))
                    expected_queries = 1 if operation == "full_scan" else QUERIES
                    require(result["family"] == "semantic_r4_k1_page_read_samples_v1" and
                            len(result["samples"]) == expected_queries * PASSES and
                            int(result["effective_queries"]) == expected_queries,
                            f"page-read sample matrix differs: {seed}/{mode}/{operation}/{invocation}")
                    native_outputs.append({"seed": seed, "mode": mode, "lanes": lanes,
                                           "operation": operation, "invocation": invocation,
                                           "path": str(output), "bytes": output.stat().st_size,
                                           "sha256": sha256(output),
                                           "layout_sha256": layout["sha256"],
                                           "candidate_sha256": candidate_sha[seed]})
                    for row in result["samples"]:
                        samples.append({"seed": seed, "invocation": invocation, **row})
    expected = len(SEEDS) * len(LAYOUTS) * len(INVOCATIONS) * PASSES * (
        1 + QUERIES)
    require(len(samples) == expected, "page-read sample count differs")
    summaries: list[dict[str, Any]] = []
    for mode, lanes, _ in LAYOUTS:
        for operation in OPERATIONS:
            for invocation in INVOCATIONS:
                selected = [row for row in samples if row["mode"] == mode and
                            int(row["lanes"]) == lanes and row["operation"] == operation and
                            int(row["invocation"]) == invocation]
                summaries.append({"mode": mode, "lanes": lanes, "operation": operation,
                                  "invocation": invocation, "samples": len(selected),
                                  "elapsed_ms": stats([float(row["elapsed_ms"]) for row in selected]),
                                  "requested_read_bytes": stats([
                                      float(row["requested_read_bytes"]) for row in selected]),
                                  "unique_file_pages_4k": stats([
                                      float(row["unique_file_pages_4k"]) for row in selected]),
                                  "read_calls": stats([
                                      float(row["read_calls"]) for row in selected])})
    raw = {"schema_version": 1, "family": "semantic_r4_k1_page_read_v1",
           "samples": samples}
    raw_bytes = (json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    receipt = {
        "schema_version": 1, "family": "semantic_r4_k1_page_read_v1",
        "execution_status": "EXECUTED", "production_activation": False,
        "seeds": list(SEEDS), "queries": QUERIES, "candidate_count": CANDIDATES,
        "passes": PASSES, "invocations": list(INVOCATIONS),
        "layouts": [{"mode": mode, "lanes": lanes, "id": layout_id}
                    for mode, lanes, layout_id in LAYOUTS],
        "operations": list(OPERATIONS), "manifest_sha256": sha256(args.manifest),
        "source_raw_sha256": sha256(args.source_raw),
        "native_executable_sha256": sha256(args.native_executable),
        "runner_sha256": sha256(Path(__file__)), "native_outputs": native_outputs,
        "summaries": summaries,
        "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                       "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                       "samples": len(samples)},
        "protocol": {"page_bytes": PAGE_BYTES, "logical_payload_bytes_reported": True,
                     "requested_file_bytes_reported": True,
                     "unique_file_pages_reported": True,
                     "mdbx_pages_measured": False, "os_physical_pages_measured": False,
                     "cache_eviction_controlled": False,
                     "warmup": "one untimed pass per process invocation",
                     "invocation_semantics": "process reopen; OS cache state uncontrolled",
                     "candidate_source": "row-major scalar top128 from K1 SIMD raw",
                     "full_scan_effective_queries": 1},
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
        raise SystemExit(f"run-r4-k1-page-read: {error}")
