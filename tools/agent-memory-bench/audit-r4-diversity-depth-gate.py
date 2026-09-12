#!/usr/bin/env python3
"""Fail-closed audit for the R4 diversity/depth gate receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
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
            observed = sum(float(row["teacher_recall"]) for row in selected) / len(selected)
            require(abs(observed - float(summary["teacher_recall"]["mean"])) < 1e-12,
                    f"depth recall aggregate mismatch: {key}")
            require(key in groups, f"missing depth group: {key}")
        require(receipt.get("prefix_parity", {}).get("passed") is True,
                "depth prefix parity did not pass")
    elif family == "semantic_r4_seed_union_gate_v1":
        for summary in receipt["summaries"]:
            seed_key = tuple(int(seed) for seed in summary["seeds"])
            key = (seed_key, int(summary["requested_candidate_budget"]))
            selected = [row for row in rows
                        if tuple(int(seed) for seed in row["seeds"]) == seed_key
                        and int(row["requested_candidate_budget"]) == key[1]]
            require(len(selected) == expected_queries, f"union group row count mismatch: {key}")
            observed = sum(float(row["teacher_recall"]) for row in selected) / len(selected)
            require(abs(observed - float(summary["teacher_recall"]["mean"])) < 1e-12,
                    f"union recall aggregate mismatch: {key}")
            require(all(float(row["duplication_ratio"]) >= 1.0 for row in selected),
                    f"union duplication ratio below one: {key}")
    else:
        raise ValueError(f"unsupported receipt family: {family}")
    print(json.dumps({"status": "PASS", "family": family, "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
