#!/usr/bin/env python3
"""Fail-closed audit for the matched native finalist serving receipt."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "unsupported finalist receipt schema")
    require(value.get("family") == "native_matched_finalist_serving_wave_v1",
            "unexpected finalist receipt family")
    require(value.get("status") == "EXECUTED" and value.get("query_count") == 152,
            "finalist receipt status/query count differs")
    require(value.get("candidate_contract") ==
            "frozen R4 candidate stream -> THQ4 top128 -> ordered top10",
            "candidate contract differs")
    expected_widths = {"lsq32": 32, "lsq48": 48, "plsq8x6": 52,
                       "turboquant1": 52, "turboquant2": 100,
                       "rslm1": 52, "rslm3": 148, "rslm4": 196}
    arms = value.get("arms")
    require(isinstance(arms, list) and {a.get("codec") for a in arms} ==
            set(expected_widths),
            "finalist arm set differs")
    sources = value.get("source_sha256", {})
    for name in ("candidate_flat", "candidate_raw", "queries", "thq", "thresholds"):
        digest = sources.get(name)
        require(isinstance(digest, str) and len(digest) == 64,
                f"source hash is invalid: {name}")
    for arm in arms:
        codec = arm.get("codec")
        require(arm.get("ordered_top10_parity") == "152/152",
                f"{codec} parity is incomplete")
        require(arm.get("payload_bytes") == expected_widths[codec],
                f"{codec} payload width is invalid")
        for metric in ("total_p50_ms", "total_p95_ms", "total_p99_ms"):
            require(isinstance(arm.get(metric), (int, float)) and arm[metric] >= 0,
                    f"{codec}.{metric} is invalid")
        require(arm["total_p50_ms"] <= arm["total_p95_ms"] <=
                arm["total_p99_ms"], f"{codec} percentile order is invalid")
        digest = arm.get("payload_sha256")
        require(isinstance(digest, str) and len(digest) == 64,
                f"{codec} payload hash is invalid")


def self_test() -> None:
    baseline = {
        "schema_version": 1,
        "family": "native_matched_finalist_serving_wave_v1",
        "status": "EXECUTED", "query_count": 152,
        "candidate_contract": "frozen R4 candidate stream -> THQ4 top128 -> ordered top10",
        "source_sha256": {name: "0" * 64 for name in
                          ("candidate_flat", "candidate_raw", "queries", "thq", "thresholds")},
        "arms": [{"codec": name, "ordered_top10_parity": "152/152",
                  "payload_bytes": width, "total_p50_ms": 1,
                  "total_p95_ms": 1, "total_p99_ms": 1,
                  "payload_sha256": "0" * 64}
                 for name, width in {"lsq32": 32, "lsq48": 48,
                                     "plsq8x6": 52, "turboquant1": 52,
                                     "turboquant2": 100, "rslm1": 52,
                                     "rslm3": 148, "rslm4": 196}.items()]}
    audit(baseline)
    for label, mutate in (
        ("missing arm", lambda x: x["arms"].pop()),
        ("false parity", lambda x: x["arms"][0].update(ordered_top10_parity="0/152")),
        ("bad hash", lambda x: x["arms"][1].update(payload_sha256="bad")),
        ("wrong width", lambda x: x["arms"][2].update(payload_bytes=1)),
        ("bad percentiles", lambda x: x["arms"][3].update(total_p99_ms=0)),
    ):
        candidate = copy.deepcopy(baseline)
        mutate(candidate)
        try:
            audit(candidate)
        except ValueError:
            continue
        raise ValueError(f"mutation accepted: {label}")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        print("audit-native-finalist-serving-wave self-test PASS")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    audit(json.loads(args.result.read_text(encoding="utf-8")))
    print(f"audit-native-finalist-serving-wave: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-finalist-serving-wave: {error}")
