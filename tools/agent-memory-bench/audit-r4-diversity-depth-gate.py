#!/usr/bin/env python3
"""Fail-closed audit for the R4 diversity/depth gate receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)), "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)), "max": float(arr.max())}


def require_aggregate(expected: dict[str, Any], values: list[float], label: str) -> None:
    observed = aggregate(values)
    for key, value in observed.items():
        require(abs(float(expected[key]) - value) < 1e-12,
                f"aggregate mismatch for {label}.{key}: {expected[key]} != {value}")


def validate_record(path: Path, record: dict[str, Any]) -> None:
    require(path.stat().st_size == int(record["bytes"]), f"artifact byte size mismatch: {path}")
    require(sha256(path) == record["sha256"], f"artifact SHA mismatch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    args = parser.parse_args()
    receipt: dict[str, Any] = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw: dict[str, Any] = json.loads(args.raw.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    require(isinstance(rows, list), "raw rows missing")
    require(receipt.get("execution_status") == "EXECUTED", "receipt is not executed")
    require(receipt.get("production_activation") is False, "production activation must remain false")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA mismatch")
    require(int(receipt["raw_output"]["rows"]) == len(rows), "raw row count mismatch")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(args.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(args.r4_manifest), "R4 manifest SHA mismatch")
    require(receipt.get("protocol", {}).get("teacher_ids_used_for_index") is False,
            "teacher IDs may not construct the index")
    inputs = receipt.get("input_artifacts")
    require(isinstance(inputs, dict), "receipt input_artifacts missing")
    for record in inputs["frozen"].values():
        validate_record(Path(record["path"]), record)
    r4_records = inputs["r4"]
    if isinstance(r4_records, list):
        r4_records = {str(receipt["seed"]): r4_records}
    for seed, records in r4_records.items():
        root = args.r4_root / "materialized" / f"seed-{seed}"
        for record in records:
            validate_record(root / record["file"], record)
    expected_queries = int(receipt.get("queries") or max(int(row["query"]) for row in rows) + 1)
    require(all(0 <= int(row["query"]) < expected_queries for row in rows),
            "raw query index out of range")

    family = receipt["family"]
    if family == "semantic_r4_depth_gate_v1":
        groups = {(row["arm"], int(row["requested_candidate_budget"])) for row in rows}
        for summary in receipt["summaries"]:
            key = (summary["arm"], int(summary["requested_candidate_budget"]))
            selected = [row for row in rows if (row["arm"], int(row["requested_candidate_budget"])) == key]
            require(len(selected) == expected_queries, f"depth group row count mismatch: {key}")
            for metric in ("actual_unique_candidates", "budget_overshoot",
                           "posting_entries_touched", "postings_touched", "teacher_recall"):
                require_aggregate(summary[metric], [float(row[metric]) for row in selected],
                                  f"depth.{key}.{metric}")
            require_aggregate(summary["teacher_address_rank"],
                              [float(rank) for row in selected for rank in row["teacher_address_ranks"]],
                              f"depth.{key}.teacher_address_rank")
            require(key in groups, f"missing depth group: {key}")
        policy = receipt.get("prefix_policy", {})
        require(int(policy.get("frozen_prefix_length", 0)) == 1024,
                "depth frozen-prefix policy missing")
        require("forced" in policy.get("construction", ""),
                "depth prefix construction is not explicit")
    elif family in ("semantic_r4_seed_union_gate_v1", "semantic_r4_route_fusion_gate_v1"):
        for summary in receipt["summaries"]:
            field = "seeds" if family == "semantic_r4_seed_union_gate_v1" else "routes"
            route_key = tuple(summary[field])
            key = (route_key, int(summary["requested_candidate_budget"]))
            selected = [row for row in rows
                        if tuple(row[field]) == route_key
                        and int(row["requested_candidate_budget"]) == key[1]]
            require(len(selected) == expected_queries, f"union group row count mismatch: {key}")
            for metric in ("actual_unique_candidates", "budget_overshoot",
                           "posting_entries_touched", "postings_touched", "overlap_entries",
                           "duplication_ratio", "teacher_recall"):
                require_aggregate(summary[metric], [float(row[metric]) for row in selected],
                                  f"union.{key}.{metric}")
            require(all(float(row["duplication_ratio"]) >= 1.0 for row in selected),
                    f"union duplication ratio below one: {key}")
    else:
        raise ValueError(f"unsupported receipt family: {family}")
    print(json.dumps({"status": "PASS", "family": family, "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
