#!/usr/bin/env python3
"""Fail-closed audit for compact native MDBX serving receipts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "unsupported MDBX receipt schema")
    require(value.get("family") == "native_thq_mdbx_serving_layout_bakeoff_v1",
            "unexpected MDBX receipt family")
    require(value.get("status") == "EXECUTED", "MDBX receipt is not executed")
    require(value.get("query_count") == 152 and value.get("candidate_width") == 128,
            "MDBX fixture shape differs")
    require(value.get("top_k") == 10 and value.get("repeats", 0) > 0,
            "MDBX benchmark configuration differs")
    inputs = value.get("inputs", {})
    for name in ("thq4_codes_sha256", "int8_linear_sha256",
                 "int8_linear_scales_sha256", "query_fixture_sha256",
                 "candidate_fixture_sha256", "expected_fixture_sha256"):
        digest = inputs.get(name)
        require(isinstance(digest, str) and len(digest) == 64,
                f"missing or malformed input hash: {name}")
    layouts = value.get("layouts", {})
    require(set(layouts) == {"row_kv", "segment_blob"},
            "MDBX layout set differs")
    for name, layout in layouts.items():
        require(layout.get("ordered_top10_parity") == "152/152",
                f"{name} parity is incomplete")
        for metric in ("materialize_ms", "mdbx_bytes", "reopen_first_query_ms",
                       "warm_p50_ms", "warm_p95_ms", "warm_p99_ms"):
            require(isinstance(layout.get(metric), (int, float)) and layout[metric] >= 0,
                    f"{name}.{metric} is invalid")


def self_test() -> None:
    baseline = {
        "schema_version": 1,
        "family": "native_thq_mdbx_serving_layout_bakeoff_v1",
        "status": "EXECUTED", "query_count": 152, "candidate_width": 128,
        "top_k": 10, "repeats": 1,
        "inputs": {name: "0" * 64 for name in (
            "thq4_codes_sha256", "int8_linear_sha256",
            "int8_linear_scales_sha256", "query_fixture_sha256",
            "candidate_fixture_sha256", "expected_fixture_sha256")},
        "layouts": {name: {"ordered_top10_parity": "152/152",
                            "materialize_ms": 1, "mdbx_bytes": 1,
                            "reopen_first_query_ms": 1,
                            "warm_p50_ms": 1, "warm_p95_ms": 1,
                            "warm_p99_ms": 1}
                    for name in ("row_kv", "segment_blob")}}
    audit(baseline)
    for label, mutate in (
        ("wrong parity", lambda x: x["layouts"]["row_kv"].update(ordered_top10_parity="0/152")),
        ("missing hash", lambda x: x["inputs"].pop("query_fixture_sha256")),
        ("wrong shape", lambda x: x.update(candidate_width=64)),
    ):
        candidate = json.loads(json.dumps(baseline))
        mutate(candidate)
        try:
            audit(candidate)
        except ValueError:
            continue
        raise ValueError(f"mutation accepted: {label}")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        print("audit-native-mdbx-serving self-test PASS")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.result.read_text(encoding="utf-8"))
    audit(value)
    print(f"audit-native-mdbx-serving: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-mdbx-serving: {error}")
