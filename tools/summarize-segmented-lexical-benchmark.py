#!/usr/bin/env python3
"""Summarize matched segmented lexical benchmark TSV evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import statistics
from collections import defaultdict


TIMING_FIELDS = (
    "environment_ms",
    "adapter_init_ms",
    "write_ms",
    "snapshot_ms",
    "close_ms",
    "reopen_ms",
    "reopen_snapshot_ms",
    "point_update_ms",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--min-repeats", type=int, default=5)
    parser.add_argument("--build-type", default="Release")
    parser.add_argument("--diagnostics", choices=("on", "off"), default="on")
    parser.add_argument("--mdbx-config", default="default")
    args = parser.parse_args()

    raw = args.input.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines(), delimiter="\t"))
    if not rows:
        fail("benchmark TSV is empty")
    keys = [(r["backend"], r["workload"], r["units"], r["repeat"]) for r in rows]
    if len(keys) != len(set(keys)):
        fail("duplicate (backend, workload, units, repeat) rows")
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["backend"], row["workload"], row["units"])].append(row)
    summaries = {}
    for key, values in sorted(grouped.items()):
        repeats = sorted(int(row["repeat"]) for row in values)
        if len(values) < args.min_repeats:
            fail(f"{key} has {len(values)} repeats, need {args.min_repeats}")
        if repeats != list(range(len(values))):
            fail(f"{key} repeats are not contiguous from zero: {repeats}")
        summaries["/".join(key)] = {
            "backend": key[0],
            "workload": key[1],
            "units": int(key[2]),
            "repeats": len(values),
            "metrics": {
                field: {
                    "p50": statistics.median(float(row[field]) for row in values),
                    "min": min(float(row[field]) for row in values),
                    "max": max(float(row[field]) for row in values),
                    "spread": max(float(row[field]) for row in values)
                    - min(float(row[field]) for row in values),
                }
                for field in TIMING_FIELDS
            },
        }
    result = {
        "schema_version": 1,
        "family": "incremental_segmented_point_update_v2",
        "status": "EXECUTED",
        "head": args.head,
        "input": {"path": args.input.name, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows)},
        "environment": {
            "build_type": args.build_type,
            "diagnostics": args.diagnostics,
            "mdbx_config": args.mdbx_config,
        },
        "summaries": summaries,
        "limitations": [
            "timings are user-space wall clock observations, not physical MDBX page writes",
            "hardware and cache state must be recorded by the caller",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
