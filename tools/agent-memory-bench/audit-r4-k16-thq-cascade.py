#!/usr/bin/env python3
"""Fail-closed audit for the K=16 representative route cascade."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

BUDGETS = (5000, 10000, 20000, 50000)


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
    require(receipt["family"] == "semantic_r4_k16_thq_cascade_v1", "wrong family")
    require(receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False, "execution/activation mismatch")
    require(raw["family"] == receipt["family"], "raw family mismatch")
    require(receipt["route_prefix"] == 16 and receipt["budgets"] == list(BUDGETS), "protocol mismatch")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA mismatch")
    require(receipt["fixture_manifest_sha256"] == sha256(args.thq_manifest), "fixture SHA mismatch")
    require(receipt["r4_manifest_sha256"] == sha256(args.r4_manifest), "R4 SHA mismatch")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw), "raw SHA mismatch")
    require(receipt["raw_output"]["bytes"] == args.raw.stat().st_size, "raw byte count mismatch")
    for record in receipt["input_artifacts"].values(): check_file(Path(record["path"]), record)
    for record in receipt["thq_artifacts"].values(): check_file(Path(record["path"]), record)
    for seed, records in receipt["r4_artifacts"].items():
        for record in records: check_file(args.r4_root / "materialized" / f"seed-{seed}" / record["file"], record)
    rows = raw["rows"]; require(len(rows) == int(receipt["raw_output"]["rows"]), "row count mismatch")
    keys = set()
    for row in rows:
        key = (int(row["query"]), int(row["requested_candidate_budget"]))
        require(key not in keys, f"duplicate row: {key}"); keys.add(key)
        require(int(row["requested_candidate_budget"]) in BUDGETS, "unexpected budget")
        require(row["candidate_count"] >= row["requested_candidate_budget"], "candidate budget not reached")
        require(len(row["thq_top256_ids"]) <= 256 and len(row["exact_top256_ids"]) <= 256, "top-k length mismatch")
    require(len(rows) == len(BUDGETS) * 152, "row grid mismatch")
    for summary in receipt["summaries"]:
        budget = int(summary["requested_candidate_budget"]); selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        require(len(selected) == 152, f"summary row count mismatch {budget}")
        for metric in ("candidate_count", "posting_entries_touched", "postings_touched", "representative_vectors_scored",
                       "candidate_payload_bytes", "exact_payload_bytes", "candidate_teacher_recall", "thq_top256_teacher_recall", "exact_top256_teacher_recall"):
            check_aggregate(summary[metric], [float(row[metric]) for row in selected], f"{budget}/{metric}")
    print(json.dumps({"status": "PASS", "family": receipt["family"], "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__": main()
