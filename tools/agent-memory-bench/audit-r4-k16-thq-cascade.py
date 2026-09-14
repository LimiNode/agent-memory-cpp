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
    teacher_record = receipt["input_artifacts"]["teacher_ids"]
    teacher_values = np.fromfile(Path(teacher_record["path"]), dtype="<i8")
    query_count = int(receipt["queries"])
    require(teacher_values.size == query_count * 10,
            "teacher artifact shape differs from receipt")
    teachers = teacher_values.reshape(query_count, 10)
    document_count = int(receipt["documents"])
    keys = set()
    for row in rows:
        key = (int(row["query"]), int(row["requested_candidate_budget"]))
        require(key not in keys, f"duplicate row: {key}"); keys.add(key)
        query = key[0]
        require(0 <= query < query_count, f"query out of range: {query}")
        require(int(row["requested_candidate_budget"]) in BUDGETS, "unexpected budget")
        require(row["candidate_count"] >= row["requested_candidate_budget"], "candidate budget not reached")
        teacher = teachers[query]
        missed = [int(value) for value in row["candidate_teacher_ids_missed"]]
        require(len(missed) == len(set(missed)), "duplicate candidate-missed teacher id")
        require(set(missed).issubset(set(int(value) for value in teacher)),
                "candidate-missed id is not a teacher id")
        candidate_recall = 1.0 - len(missed) / len(teacher)
        require(abs(float(row["candidate_teacher_recall"]) - candidate_recall) < 1e-12,
                f"candidate recall mismatch: {key}")
        top_sets = {}
        for field in ("thq_top256_ids", "exact_top256_ids"):
            ids = [int(value) for value in row[field]]
            require(len(ids) <= 256 and len(ids) == len(set(ids)),
                    f"top-k length or uniqueness mismatch: {key}/{field}")
            require(all(0 <= value < document_count for value in ids),
                    f"top-k document id out of range: {key}/{field}")
            top_sets[field] = ids
            recomputed = float(np.isin(teacher, np.asarray(ids, dtype=np.int64)).sum() / len(teacher))
            metric = "thq_top256_teacher_recall" if field.startswith("thq") else "exact_top256_teacher_recall"
            require(abs(float(row[metric]) - recomputed) < 1e-12,
                    f"{metric} mismatch: {key}")
        require(abs(float(row["candidate_teacher_recall"]) -
                    float(row["thq_top256_teacher_recall"])) < 1e-12 and
                abs(float(row["candidate_teacher_recall"]) -
                    float(row["exact_top256_teacher_recall"])) < 1e-12,
                f"cascade recall mismatch: {key}")
    require(len(rows) == len(BUDGETS) * 152, "row grid mismatch")
    for summary in receipt["summaries"]:
        budget = int(summary["requested_candidate_budget"]); selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        require(len(selected) == 152, f"summary row count mismatch {budget}")
        for metric in ("candidate_count", "posting_entries_touched", "postings_touched", "representative_vectors_scored",
                       "candidate_payload_bytes", "exact_payload_bytes", "candidate_teacher_recall", "thq_top256_teacher_recall", "exact_top256_teacher_recall"):
            check_aggregate(summary[metric], [float(row[metric]) for row in selected], f"{budget}/{metric}")
    print(json.dumps({"status": "PASS", "family": receipt["family"], "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__": main()
