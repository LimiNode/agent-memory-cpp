#!/usr/bin/env python3
"""Fail-closed audit for the R4 representative-prefix sweep."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

PREFIXES = (1, 4, 8, 16, 32)
BUDGETS = (5000, 10000, 20000, 50000, 100000)


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
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()), "p05": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def check_file(path: Path, record: dict[str, Any]) -> None:
    require(path.stat().st_size == int(record["bytes"]), f"size mismatch: {path}")
    require(sha256(path) == record["sha256"], f"SHA mismatch: {path}")


def check_aggregate(expected: dict[str, Any], values: list[float], label: str) -> None:
    for key, value in aggregate(values).items():
        require(abs(float(expected[key]) - value) < 1e-12, f"aggregate mismatch {label}.{key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("receipt", "raw", "runner", "thq-manifest", "r4-manifest", "r4-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8")); raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_representative_prefix_sweep_v1", "wrong family")
    require(receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False, "execution/activation mismatch")
    require(raw["family"] == receipt["family"], "raw family mismatch")
    require(receipt["prefixes"] == list(PREFIXES) and receipt["budgets"] == list(BUDGETS), "sweep grid mismatch")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(args.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(args.r4_manifest), "R4 manifest SHA mismatch")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA mismatch")
    require(receipt["raw_output"]["bytes"] == args.raw.stat().st_size, "raw byte count mismatch")
    for record in receipt["input_artifacts"].values(): check_file(Path(record["path"]), record)
    for seed, records in receipt["r4_artifacts"].items():
        for record in records: check_file(args.r4_root / "materialized" / f"seed-{seed}" / record["file"], record)
    rows = raw["rows"]; require(len(rows) == int(receipt["raw_output"]["rows"]), "row count mismatch")
    require(len(receipt["arms"]) == 20, "arm count mismatch")
    for arm in receipt["arms"]:
        selected_arm = [row for row in rows if row["arm"] == arm]
        require(len(selected_arm) == 152 * len(BUDGETS), f"arm row count mismatch {arm}")
        for budget in BUDGETS:
            selected = [row for row in selected_arm if int(row["requested_candidate_budget"]) == budget]
            require(len(selected) == 152, f"budget row count mismatch {arm}/{budget}")
            summary = next(row for row in receipt["summaries"] if row["arm"] == arm and int(row["requested_candidate_budget"]) == budget)
            for metric in ("actual_unique_candidates", "posting_entries_touched", "postings_touched", "duplication_ratio", "teacher_recall", "representative_vectors_scored"):
                check_aggregate(summary[metric], [float(row[metric]) for row in selected], f"{arm}/{budget}/{metric}")
            for row in selected:
                require(row["actual_unique_candidates"] >= budget or row["actual_unique_candidates"] == 1000000,
                        f"budget not reached {arm}/{budget}")
    print(json.dumps({"status": "PASS", "family": receipt["family"], "rows": len(rows), "arms": receipt["arms"]}, sort_keys=True))


if __name__ == "__main__": main()
