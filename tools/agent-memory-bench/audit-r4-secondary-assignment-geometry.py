#!/usr/bin/env python3
"""Fail-closed audit for the R4 secondary-assignment geometry experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


M_VALUES = (8, 16, 32, 64)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    import numpy as np
    if not values:
        return {key: 0.0 for key in ("min", "mean", "p05", "p50", "p95", "max")}
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def check_file(path: Path, record: dict[str, Any]) -> None:
    require(path.stat().st_size == int(record["bytes"]), f"size mismatch: {path}")
    require(sha256(path) == record["sha256"], f"SHA mismatch: {path}")


def check_aggregate(expected: dict[str, Any], values: list[float], label: str) -> None:
    observed = aggregate(values)
    for key, value in observed.items():
        require(abs(float(expected[key]) - value) < 1e-12,
                f"aggregate mismatch {label}.{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_secondary_assignment_geometry_v1", "wrong family")
    require(receipt["execution_status"] == "EXECUTED", "receipt is not executed")
    require(receipt["production_activation"] is False, "production activation must be false")
    require(raw["family"] == receipt["family"], "raw family mismatch")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(args.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(args.r4_manifest), "R4 manifest SHA mismatch")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA mismatch")
    require(receipt["raw_output"]["bytes"] == args.raw.stat().st_size, "raw byte count mismatch")
    require(receipt["sample_size_actual"] == len(raw["sample_ids"]), "sample size mismatch")
    require(len(raw["sample_ids"]) == len(set(raw["sample_ids"])), "duplicate sample IDs")
    require(len(receipt["route_seeds"]) == 3, "route seed count mismatch")
    require(set(receipt["route_seeds"]) == {2026082701, 2026082702, 2026082703}, "route seeds mismatch")
    for record in receipt["input_artifacts"].values():
        check_file(Path(record["path"]), record)
    for seed, records in receipt["r4_artifacts"].items():
        for record in records:
            check_file(args.r4_root / "materialized" / f"seed-{seed}" / record["file"], record)
    rows = raw["capacity_rows"]
    expected_rows = receipt["raw_output"]["capacity_rows"]
    require(len(rows) == expected_rows, "capacity row count mismatch")
    seen = set()
    for row in rows:
        key = (int(row["seed"]), int(row["query"]), int(row["teacher"]), int(row["m"]))
        require(key not in seen, f"duplicate capacity row: {key}")
        seen.add(key)
        require(int(row["m"]) in M_VALUES, "unexpected M")
        require(bool(row["recoverable_in_prefix"]) == (row["best_query_rank"] is not None),
                "recoverability/rank mismatch")
        if row["recoverable_in_prefix"]:
            require(row["best_prefix_posting_entries"] is not None, "missing posting cost")
    summaries = receipt["capacity_oracle"]["rows"]
    require(len(summaries) == 3 * len(M_VALUES), "capacity summary count mismatch")
    for summary in summaries:
        seed = int(summary["seed"]); m = int(summary["m"])
        selected = [row for row in rows if int(row["seed"]) == seed and int(row["m"]) == m]
        recoverable = [row for row in selected if row["recoverable_in_prefix"]]
        require(len(selected) == int(summary["miss_pairs"]), f"miss count mismatch {seed}/{m}")
        require(len(recoverable) == int(summary["recoverable_pairs"]), f"recoverable count mismatch {seed}/{m}")
        support = len(recoverable) / len(selected) if selected else 0.0
        require(abs(float(summary["support_over_primary_misses"]) - support) < 1e-12,
                f"support mismatch {seed}/{m}")
        check_aggregate(summary["best_query_rank"], [float(row["best_query_rank"]) for row in recoverable],
                        f"best_query_rank.{seed}.{m}")
        check_aggregate(summary["best_prefix_posting_entries"],
                        [float(row["best_prefix_posting_entries"]) for row in recoverable],
                        f"best_prefix_posting_entries.{seed}.{m}")
    print(json.dumps({"status": "PASS", "family": receipt["family"],
                      "capacity_rows": len(rows), "summaries": len(summaries)}, sort_keys=True))


if __name__ == "__main__":
    main()
