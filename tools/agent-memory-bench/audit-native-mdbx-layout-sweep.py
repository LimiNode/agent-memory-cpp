#!/usr/bin/env python3
"""Fail-closed audit for the 1M-document MDBX segment sweep receipt."""

from __future__ import annotations

import argparse
import array
import json
import sys
from pathlib import Path


EXPECTED_SEGMENTS = (16, 32, 64, 128, 256, 512, 1024, 4096)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def percentile(values: list[float], fraction: float) -> float:
    require(values, "empty timing samples")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(1e-5, abs(expected) * 1e-5)


def expected_checksum(values: list[int], repeats: int) -> int:
    checksum = 0
    for _ in range(repeats):
        for value in values:
            checksum = (checksum * 1315423911 + value) & ((1 << 64) - 1)
    return checksum


def audit(value: dict, expected_values: list[int] | None = None) -> None:
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
        samples = benchmark.get("samples_ms")
        require(isinstance(samples, list) and
                len(samples) == 152 * benchmark.get("repeats", 0) and
                all(isinstance(sample, (int, float)) and sample >= 0 for sample in samples),
                f"segment-{expected} raw timing samples are invalid")
        for field, fraction in (("p50_ms", .5), ("p95_ms", .95), ("p99_ms", .99)):
            require(close_enough(float(benchmark[field]), percentile(samples, fraction)),
                    f"segment-{expected} {field} does not match raw samples")
        if expected_values is not None:
            require(benchmark.get("checksum") ==
                    expected_checksum(expected_values, benchmark["repeats"]),
                    f"segment-{expected} checksum does not replay expected IDs")
        for metric in ("materialize_ms", "db_bytes"):
            require(isinstance(materialize.get(metric), (int, float)) and
                    materialize[metric] >= 0,
                    f"segment-{expected} materialization metric is invalid")
        if "batch_documents" in materialize:
            require(isinstance(materialize["batch_documents"], int) and
                    0 < materialize["batch_documents"] <= value["documents"],
                    f"segment-{expected} batch size is invalid")
            require(isinstance(materialize.get("durable_commits"), int) and
                    materialize["durable_commits"] >= 1,
                    f"segment-{expected} durable commit count is invalid")
        for metric in ("p50_ms", "p95_ms", "p99_ms", "reopen_first_query_ms"):
            require(isinstance(benchmark.get(metric), (int, float)) and
                    benchmark[metric] >= 0,
                    f"segment-{expected} benchmark metric is invalid")
        stage_fields = (
            "read_decode_p50_ms", "read_decode_p95_ms", "read_decode_p99_ms",
            "score_p50_ms", "score_p95_ms", "score_p99_ms",
            "topk_p50_ms", "topk_p95_ms", "topk_p99_ms",
        )
        if any(field in benchmark for field in stage_fields):
            require(all(isinstance(benchmark.get(field), (int, float)) and
                        benchmark[field] >= 0 for field in stage_fields),
                    f"segment-{expected} stage timing fields are incomplete")


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
                           "p99_ms": 1, "reopen_first_query_ms": 1,
                           "repeats": 5, "samples_ms": [1] * (152 * 5)},
        } for segment in EXPECTED_SEGMENTS],
    }
    audit(baseline)
    for label, mutate in (
        ("wrong parity", lambda x: x["rows"][0]["benchmark"].update(parity=0)),
        ("missing hash", lambda x: x["inputs_sha256"].pop("queries")),
        ("wrong segment set", lambda x: x.update(segment_rows=[16])),
        ("wrong raw percentile", lambda x: x["rows"][0]["benchmark"].update(p95_ms=2)),
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
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    expected_values = None
    if args.expected:
        raw = array.array("I")
        raw.fromfile(args.expected.open("rb"), args.expected.stat().st_size // raw.itemsize)
        expected_values = list(raw)
        require(len(expected_values) == 152 * 10,
                "expected fixture shape differs")
    audit(json.loads(args.result.read_text(encoding="utf-8")), expected_values)
    print(f"audit-native-mdbx-layout-sweep: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-mdbx-layout-sweep: {error}")
