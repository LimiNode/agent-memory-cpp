#!/usr/bin/env python3
"""Fail-closed validator for the numeric flat canonical benchmark receipt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any


FAMILY = "mdbx_canonical_flat_benchmark_v1"
SCALES = [64, 256, 1024]
REPEATS = 5
WORKLOADS = ["point_block", "bounded_subtree", "single_block_edit", "full_materialization"]
PHYSICAL_READ_MODEL = {
    "point_block": "structure_plus_one_block",
    "bounded_subtree": "full_revision_read_then_reference_navigation",
    "single_block_edit": "full_revision_rewrite",
    "full_materialization": "full_revision_read",
}
HEADER = [
    "scale",
    "repeat",
    "workload",
    "elapsed_ms",
    "source_blocks",
    "result_blocks",
    "physical_bytes",
    "result_text_bytes",
]


def fail(message: str) -> None:
    raise ValueError(message)


def finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{field} is not numeric")
    if not math.isfinite(float(value)):
        fail(f"{field} is not finite")
    return float(value)


def non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{field} is not a non-negative integer")
    return value


def read_raw(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot read raw observations: {error}")
    if len(lines) < 2 or not lines[0].startswith("# family="):
        fail("raw observations are missing the provenance header")
    rows = list(csv.DictReader(lines[1:], delimiter="\t"))
    if rows and list(rows[0]) != HEADER:
        fail("raw observation header is invalid")
    if not rows:
        fail("raw observations are empty")
    parsed: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for row in rows:
        try:
            scale = int(row["scale"])
            repeat = int(row["repeat"])
        except (KeyError, ValueError):
            fail("raw scale/repeat is invalid")
        workload = row.get("workload", "")
        if scale not in SCALES or repeat < 0 or repeat >= REPEATS or workload not in WORKLOADS:
            fail("raw matrix coordinates are invalid")
        key = (scale, repeat, workload)
        if key in seen:
            fail("raw matrix contains a duplicate case")
        seen.add(key)
        parsed.append(
            {
                "scale": scale,
                "repeat": repeat,
                "workload": workload,
                "elapsed_ms": float(row["elapsed_ms"]),
                "source_blocks": int(row["source_blocks"]),
                "result_blocks": int(row["result_blocks"]),
                "physical_bytes": int(row["physical_bytes"]),
                "result_text_bytes": int(row["result_text_bytes"]),
            }
        )
    expected = {(scale, repeat, workload) for scale in SCALES for repeat in range(REPEATS) for workload in WORKLOADS}
    if seen != expected:
        fail("raw matrix does not contain exactly the required cases")
    for row in parsed:
        finite_number(row["elapsed_ms"], "raw elapsed_ms")
        if row["elapsed_ms"] <= 0.0:
            fail("raw elapsed_ms must be positive")
        for field in ("source_blocks", "result_blocks", "physical_bytes", "result_text_bytes"):
            non_negative_int(row[field], f"raw {field}")
    return parsed


def median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def validate(raw_path: Path, receipt_path: Path) -> None:
    rows = read_raw(raw_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read receipt: {error}")
    if receipt.get("schema_version") != 1 or receipt.get("family") != FAMILY:
        fail("receipt schema or family is invalid")
    if receipt.get("execution_status") != "completed" or receipt.get("decision") != "evidence_only":
        fail("receipt execution status is invalid")
    if receipt.get("seed") != 20261119 or receipt.get("repeats") != REPEATS:
        fail("receipt matrix provenance is invalid")
    if receipt.get("scales") != SCALES or receipt.get("workloads") != WORKLOADS:
        fail("receipt matrix declaration is invalid")
    if receipt.get("metric_provenance") != {
        "elapsed_ms": "steady_clock_wall_time",
        "physical_bytes": "codec_payload_size_estimate",
        "cpu_time_ms": None,
        "allocations": None,
    }:
        fail("receipt metric provenance is invalid")
    if receipt.get("physical_read_model") != PHYSICAL_READ_MODEL:
        fail("receipt physical read model is invalid")
    raw = receipt.get("raw")
    if not isinstance(raw, dict) or raw.get("path") != raw_path.as_posix():
        fail("receipt raw path is invalid")
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if raw.get("sha256") != digest or raw.get("rows") != len(rows):
        fail("receipt raw binding is invalid")
    declared = receipt.get("p50")
    if not isinstance(declared, list) or len(declared) != len(SCALES) * len(WORKLOADS):
        fail("receipt p50 matrix is incomplete")
    by_key = {(row["scale"], row["workload"]): row for row in rows}
    seen: set[tuple[int, str]] = set()
    for item in declared:
        if not isinstance(item, dict):
            fail("receipt p50 entry is invalid")
        key = (item.get("scale"), item.get("workload"))
        if key in seen or key not in by_key:
            fail("receipt p50 coordinates are invalid")
        seen.add(key)
        samples = [row for row in rows if (row["scale"], row["workload"]) == key]
        expected_time = median([row["elapsed_ms"] for row in samples])
        actual_time = finite_number(item.get("elapsed_ms"), "receipt elapsed_ms")
        if not math.isclose(actual_time, expected_time, rel_tol=1e-7, abs_tol=1e-6):
            fail("receipt elapsed_ms does not match raw observations")
        for field in ("source_blocks", "result_blocks", "physical_bytes", "result_text_bytes"):
            value = non_negative_int(item.get(field), f"receipt {field}")
            if value != samples[0][field] or any(row[field] != value for row in samples):
                fail(f"receipt {field} does not match raw observations")
    if seen != set(by_key):
        fail("receipt p50 matrix has missing cases")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw = root / "raw.tsv"
        receipt = root / "receipt.json"
        rows: list[str] = ["# family=mdbx_canonical_flat_benchmark_v1\tseed=20261119\trepeats=5", "\t".join(HEADER)]
        for scale in SCALES:
            for repeat in range(REPEATS):
                for workload in WORKLOADS:
                    rows.append(f"{scale}\t{repeat}\t{workload}\t1.000000\t{scale}\t1\t2\t3")
        raw.write_text("\n".join(rows) + "\n", encoding="utf-8")
        # Build the valid receipt through the same rules used by the external gate.
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        p50 = [
            {
                "scale": scale,
                "workload": workload,
                "elapsed_ms": 1.0,
                "source_blocks": scale,
                "result_blocks": 1,
                "physical_bytes": 2,
                "result_text_bytes": 3,
            }
            for scale in SCALES
            for workload in WORKLOADS
        ]
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "family": FAMILY,
                    "execution_status": "completed",
                    "decision": "evidence_only",
                    "seed": 20261119,
                    "repeats": REPEATS,
                    "scales": SCALES,
                    "workloads": WORKLOADS,
                    "metric_provenance": {
                        "elapsed_ms": "steady_clock_wall_time",
                        "physical_bytes": "codec_payload_size_estimate",
                        "cpu_time_ms": None,
                        "allocations": None,
                    },
                    "physical_read_model": PHYSICAL_READ_MODEL,
                    "raw": {"path": raw.as_posix(), "sha256": digest, "rows": len(rows) - 2},
                    "p50": p50,
                }
            ),
            encoding="utf-8",
        )
        validate(raw, receipt)
        forged = json.loads(receipt.read_text(encoding="utf-8"))
        forged["p50"][0]["elapsed_ms"] = 3.0
        receipt.write_text(json.dumps(forged), encoding="utf-8")
        try:
            validate(raw, receipt)
        except ValueError:
            pass
        else:
            fail("self-test accepted a forged aggregate")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.raw and args.receipt:
        validate(args.raw, args.receipt)
    else:
        parser.error("--raw and --receipt are required unless --self-test is used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
