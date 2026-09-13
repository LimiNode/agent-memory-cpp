#!/usr/bin/env python3
"""Fail-closed audit for representative-aware R4 capacity oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

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
    if not values:
        return {key: 0.0 for key in ("min", "mean", "p05", "p50", "p95", "max")}
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def check_file(path: Path, record: dict[str, Any]) -> None:
    require(path.stat().st_size == int(record["bytes"]), f"size mismatch: {path}")
    require(sha256(path) == record["sha256"], f"SHA mismatch: {path}")


def check_aggregate(expected: dict[str, Any], values: list[float], label: str) -> None:
    observed = aggregate(values)
    for key, value in observed.items():
        require(abs(float(expected[key]) - value) < 1e-12, f"aggregate mismatch {label}.{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("receipt", "raw", "runner", "thq-manifest", "r4-manifest", "r4-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_representative_assignment_capacity_v1", "wrong family")
    require(receipt["execution_status"] == "EXECUTED", "receipt not executed")
    require(receipt["production_activation"] is False, "production activation must be false")
    require(raw["family"] == receipt["family"], "raw family mismatch")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(args.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(args.r4_manifest), "R4 manifest SHA mismatch")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA mismatch")
    require(receipt["raw_output"]["bytes"] == args.raw.stat().st_size, "raw byte count mismatch")
    require(len(raw["sample_ids"]) == int(receipt["sample_size_actual"]), "sample size mismatch")
    require(len(raw["sample_ids"]) == len(set(raw["sample_ids"])), "duplicate sample IDs")
    for record in receipt["input_artifacts"].values():
        check_file(Path(record["path"]), record)
    for seed, records in receipt["r4_artifacts"].items():
        for record in records:
            check_file(args.r4_root / "materialized" / f"seed-{seed}" / record["file"], record)
    rows = raw["capacity_rows"]
    require(len(rows) == int(receipt["raw_output"]["capacity_rows"]), "capacity row count mismatch")
    keys = set()
    for row in rows:
        key = (int(row["seed"]), int(row["query"]), int(row["teacher"]), int(row["m"]))
        require(key not in keys, f"duplicate row: {key}"); keys.add(key)
        require(row["metric"] == "representative_centroid", "metric mismatch")
        require(int(row["m"]) in M_VALUES, "unexpected M")
        require(bool(row["recoverable_in_prefix"]) == (row["best_query_rank"] is not None), "rank/recoverability mismatch")
    summaries = receipt["capacity_oracle"]["rows"]
    require(len(summaries) == 3 * len(M_VALUES), "summary count mismatch")
    for summary in summaries:
        seed = int(summary["seed"]); m = int(summary["m"])
        selected = [row for row in rows if int(row["seed"]) == seed and int(row["m"]) == m]
        recovered = [row for row in selected if row["recoverable_in_prefix"]]
        require(len(selected) == int(summary["miss_pairs"]), f"miss count mismatch {seed}/{m}")
        require(len(recovered) == int(summary["recoverable_pairs"]), f"recoverable count mismatch {seed}/{m}")
        support = len(recovered) / len(selected) if selected else 0.0
        require(abs(float(summary["support_over_primary_misses"]) - support) < 1e-12, f"support mismatch {seed}/{m}")
        check_aggregate(summary["best_query_rank"], [float(row["best_query_rank"]) for row in recovered], f"rank.{seed}.{m}")
        check_aggregate(summary["best_prefix_posting_entries"], [float(row["best_prefix_posting_entries"]) for row in recovered], f"entries.{seed}.{m}")
    print(json.dumps({"status": "PASS", "family": receipt["family"], "capacity_rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
