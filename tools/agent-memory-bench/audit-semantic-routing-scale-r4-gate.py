#!/usr/bin/env python3
"""Fail-closed audit for the semantic scale and R4 comparator receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audit(receipt_path: Path, runner_path: Path, expected_family: str,
          expected_rows: int | None = None) -> dict[str, object]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("family") != expected_family or receipt.get("execution_status") != "EXECUTED":
        raise ValueError(f"invalid receipt family/status: {receipt_path}")
    if receipt.get("production_activation") or receipt.get("protocol", {}).get("production_activation"):
        raise ValueError(f"production activation must remain false: {receipt_path}")
    if receipt.get("runner_sha256") != sha256(runner_path):
        raise ValueError(f"runner SHA mismatch: {receipt_path}")
    raw = receipt.get("raw_output", {})
    raw_path = Path(raw["path"])
    if raw.get("sha256") != sha256(raw_path):
        raise ValueError(f"raw SHA mismatch: {receipt_path}")
    rows = json.loads(raw_path.read_text(encoding="utf-8")).get("rows", [])
    if len(rows) != int(raw.get("rows", -1)):
        raise ValueError(f"raw row count mismatch: {receipt_path}")
    if expected_rows is not None and len(rows) != expected_rows:
        raise ValueError(f"unexpected raw row count: {len(rows)} != {expected_rows}")
    if expected_family.startswith("semantic_routing_scale"):
        expected = (len(receipt["arms"]) * len(receipt["clusters"]) *
                    len(receipt["replications"]) * 2 * len(receipt["budgets"]) * receipt["queries"])
        if len(rows) != expected:
            raise ValueError(f"semantic matrix row count mismatch: {len(rows)} != {expected}")
    else:
        expected = len(receipt["seeds"]) * 2 * len(receipt["budgets"]) * receipt["queries"]
        if len(rows) != expected:
            raise ValueError(f"R4 matrix row count mismatch: {len(rows)} != {expected}")
        for row in rows:
            if row["mode"] == "hard_cap" and row["actual_unique_candidates"] < row["requested_candidate_budget"]:
                continue  # exhausted shortlist is a valid, explicit outcome
            if row["mode"] == "hard_cap" and row["actual_unique_candidates"] != row["requested_candidate_budget"]:
                raise ValueError("hard-cap row overshot its budget")
    return {"receipt": str(receipt_path), "family": expected_family,
            "raw_rows": len(rows), "runner_sha256": receipt["runner_sha256"],
            "raw_sha256": raw["sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantic-receipt", type=Path, required=True)
    parser.add_argument("--r4-receipt", type=Path, required=True)
    parser.add_argument("--semantic-runner", type=Path, required=True)
    parser.add_argument("--r4-runner", type=Path, required=True)
    args = parser.parse_args()
    results = [
        audit(args.semantic_receipt, args.semantic_runner,
              "semantic_routing_scale_r4_gate_v1"),
        audit(args.r4_receipt, args.r4_runner,
              "semantic_routing_r4_frozen_comparator_v1"),
    ]
    print(json.dumps({"status": "PASS", "audits": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
