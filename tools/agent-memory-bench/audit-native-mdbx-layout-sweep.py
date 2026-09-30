#!/usr/bin/env python3
"""Fail-closed audit for the 1M-document MDBX segment sweep receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXPECTED_SEGMENTS = (16, 32, 64, 128, 256, 512, 1024, 4096)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "unsupported sweep schema")
    require(value.get("family") == "native_thq_mdbx_segment_sweep_v1",
            "unexpected sweep family")
    require(value.get("status") == "EXECUTED", "sweep is not executed")
    require(value.get("documents") == 1_000_000, "document count differs")
    require(tuple(value.get("segment_rows", ())) == EXPECTED_SEGMENTS,
            "segment sweep set differs")
    hashes = value.get("inputs_sha256", {})
    for name in ("thq", "codes", "scales", "queries", "candidates", "expected"):
        digest = hashes.get(name)
        require(isinstance(digest, str) and len(digest) == 64,
                f"missing or malformed input hash: {name}")
    rows = value.get("rows", [])
    require(len(rows) == len(EXPECTED_SEGMENTS), "row count differs")
    for expected, row in zip(EXPECTED_SEGMENTS, rows):
        require(row.get("segment_rows") == expected, "row order differs")
        materialize = row.get("materialize", {})
        benchmark = row.get("benchmark", {})
        require(materialize.get("status") == "MATERIALIZED",
                f"segment-{expected} was not materialized")
        require(benchmark.get("status") == "EXECUTED",
                f"segment-{expected} benchmark was not executed")
        require(benchmark.get("parity") == 152 and
                benchmark.get("parity_total") == 152,
                f"segment-{expected} parity is incomplete")
        for metric in ("materialize_ms", "db_bytes"):
            require(isinstance(materialize.get(metric), (int, float)) and
                    materialize[metric] >= 0,
                    f"segment-{expected} materialization metric is invalid")
        for metric in ("p50_ms", "p95_ms", "p99_ms", "reopen_first_query_ms"):
            require(isinstance(benchmark.get(metric), (int, float)) and
                    benchmark[metric] >= 0,
                    f"segment-{expected} benchmark metric is invalid")


def self_test() -> None:
    baseline = {
        "schema_version": 1,
        "family": "native_thq_mdbx_segment_sweep_v1",
        "status": "EXECUTED",
        "documents": 1_000_000,
        "segment_rows": list(EXPECTED_SEGMENTS),
        "inputs_sha256": {name: "0" * 64 for name in
                           ("thq", "codes", "scales", "queries", "candidates", "expected")},
        "rows": [{
            "segment_rows": segment,
            "materialize": {"status": "MATERIALIZED", "materialize_ms": 1,
                             "db_bytes": 1},
            "benchmark": {"status": "EXECUTED", "parity": 152,
                           "parity_total": 152, "p50_ms": 1, "p95_ms": 1,
                           "p99_ms": 1, "reopen_first_query_ms": 1},
        } for segment in EXPECTED_SEGMENTS],
    }
    audit(baseline)
    for label, mutate in (
        ("wrong parity", lambda x: x["rows"][0]["benchmark"].update(parity=0)),
        ("missing hash", lambda x: x["inputs_sha256"].pop("queries")),
        ("wrong segment set", lambda x: x.update(segment_rows=[16])),
    ):
        candidate = json.loads(json.dumps(baseline))
        mutate(candidate)
        try:
            audit(candidate)
        except ValueError:
            continue
        raise ValueError(f"mutation accepted: {label}")
    print("audit-native-mdbx-layout-sweep self-test PASS")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    audit(json.loads(args.result.read_text(encoding="utf-8")))
    print(f"audit-native-mdbx-layout-sweep: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-mdbx-layout-sweep: {error}")
