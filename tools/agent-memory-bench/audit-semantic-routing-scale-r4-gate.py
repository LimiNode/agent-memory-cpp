#!/usr/bin/env python3
"""Fail-closed audit for semantic scale and R4 comparator receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "max": float(arr.max())}


def compare(expected: dict[str, Any], actual: dict[str, float], label: str) -> None:
    for key, value in actual.items():
        if not math.isclose(float(expected[key]), float(value), rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"receipt aggregate mismatch {label}/{key}: {expected[key]} != {value}")


def verify_file(path: Path, expected_bytes: int, expected_sha: str) -> None:
    if path.stat().st_size != expected_bytes or sha256(path) != expected_sha:
        raise ValueError(f"input artifact mismatch: {path}")


def verify_frozen_inputs(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for role in ("document_vectors", "queries", "teacher_ids"):
        row = manifest["references"][role]
        verify_file(Path(row["path"]), int(row["bytes"]), row["sha256"])


def load_rows(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    raw = receipt["raw_output"]
    raw_path = Path(raw["path"])
    if raw["sha256"] != sha256(raw_path):
        raise ValueError(f"raw SHA mismatch: {raw_path}")
    rows = json.loads(raw_path.read_text(encoding="utf-8")).get("rows", [])
    if len(rows) != int(raw["rows"]):
        raise ValueError("raw row count mismatch")
    return rows


METRICS = ("actual_unique_candidates", "budget_overshoot",
           "posting_entries_touched", "postings_touched", "teacher_recall")


def audit_semantic(receipt: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    expected_rows = (len(receipt["arms"]) * len(receipt["clusters"]) *
                     len(receipt["replications"]) * 2 * len(receipt["budgets"]) * receipt["queries"])
    if len(rows) != expected_rows:
        raise ValueError(f"semantic matrix row count mismatch: {len(rows)} != {expected_rows}")
    for summary in receipt["rows"]:
        selected = [row for row in rows
                    if row["arm"] == summary["arm"] and row["k"] == summary["k"]
                    and row["replication"] == summary["replication"]
                    and row["mode"] == summary["mode"]
                    and row["requested_candidate_budget"] == summary["requested_candidate_budget"]]
        if len(selected) != receipt["queries"]:
            raise ValueError("semantic summary selection count mismatch")
        for metric in METRICS:
            compare(summary[metric], aggregate([float(row[metric]) for row in selected]), metric)
        compare(summary["teacher_cell_rank"], aggregate(
            [float(rank) for row in selected for rank in row["teacher_cell_ranks"]]), "teacher_cell_rank")


def audit_r4(receipt: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    expected_rows = len(receipt["seeds"]) * 2 * len(receipt["budgets"]) * receipt["queries"]
    if len(rows) != expected_rows:
        raise ValueError(f"R4 matrix row count mismatch: {len(rows)} != {expected_rows}")
    for summary in receipt["aggregate_results"]:
        seed_means = []
        for seed in receipt["seeds"]:
            selected = [row for row in rows if row["seed"] == seed
                        and row["mode"] == summary["mode"]
                        and row["requested_candidate_budget"] == summary["requested_candidate_budget"]]
            if len(selected) != receipt["queries"]:
                raise ValueError("R4 per-seed selection count mismatch")
            seed_means.append({metric: aggregate([float(row[metric]) for row in selected])["mean"]
                               for metric in METRICS})
        for metric in METRICS:
            compare(summary[metric], aggregate([value[metric] for value in seed_means]),
                    f"R4/{metric}")
    for row in rows:
        if row["mode"] == "hard_cap" and row["actual_unique_candidates"] > row["requested_candidate_budget"]:
            raise ValueError("R4 hard-cap row overshot its budget")


def audit(receipt_path: Path, runner_path: Path, family: str,
          manifest_path: Path, r4_manifest_path: Path | None = None,
          r4_root: Path | None = None) -> dict[str, object]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("family") != family or receipt.get("execution_status") != "EXECUTED":
        raise ValueError(f"invalid receipt family/status: {receipt_path}")
    if receipt.get("production_activation") or receipt.get("protocol", {}).get("production_activation"):
        raise ValueError(f"production activation must remain false: {receipt_path}")
    if receipt.get("runner_sha256") != sha256(runner_path):
        raise ValueError(f"runner SHA mismatch: {receipt_path}")
    verify_frozen_inputs(manifest_path)
    if receipt.get("fixture_manifest_sha256") != sha256(manifest_path):
        raise ValueError("frozen fixture manifest SHA mismatch")
    rows = load_rows(receipt)
    if family.startswith("semantic_routing_scale"):
        audit_semantic(receipt, rows)
    else:
        if r4_manifest_path is None or r4_root is None:
            raise ValueError("R4 manifest/root are required")
        if receipt.get("r4_manifest_sha256") != sha256(r4_manifest_path):
            raise ValueError("R4 manifest SHA mismatch")
        manifest = json.loads(r4_manifest_path.read_text(encoding="utf-8"))
        for seed in manifest["seeds"]:
            root = r4_root / "materialized" / f"seed-{seed['seed']}"
            for item in seed["mappings"]:
                verify_file(root / item["file"], int(item["bytes"]), item["sha256"])
            shortlist = np.fromfile(root / next(item for item in seed["mappings"]
                                                if item["role"] == "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
            if any(np.unique(row).size != row.size for row in shortlist):
                raise ValueError(f"duplicate R4 shortlist address in seed {seed['seed']}")
        audit_r4(receipt, rows)
    return {"receipt": str(receipt_path), "family": family,
            "raw_rows": len(rows), "runner_sha256": receipt["runner_sha256"],
            "raw_sha256": receipt["raw_output"]["sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantic-receipt", type=Path, required=True)
    parser.add_argument("--r4-receipt", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--semantic-runner", type=Path, required=True)
    parser.add_argument("--r4-runner", type=Path, required=True)
    args = parser.parse_args()
    results = [
        audit(args.semantic_receipt, args.semantic_runner,
              "semantic_routing_scale_r4_gate_v1", args.thq_manifest),
        audit(args.r4_receipt, args.r4_runner,
              "semantic_routing_r4_frozen_comparator_v1", args.thq_manifest,
              args.r4_manifest, args.r4_root),
    ]
    print(json.dumps({"status": "PASS", "audits": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
