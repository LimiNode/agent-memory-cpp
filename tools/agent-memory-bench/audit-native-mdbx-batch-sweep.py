#!/usr/bin/env python3
"""Fail-closed audit for bounded MDBX materialization receipts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "unsupported batch schema")
    require(value.get("family") == "native_thq_mdbx_bounded_batch_sweep_v1",
            "unexpected batch family")
    require(value.get("status") == "EXECUTED", "batch sweep is not executed")
    documents = value.get("documents")
    segment_rows = value.get("segment_rows")
    batches = value.get("batch_documents")
    require(isinstance(documents, int) and documents > 0, "document count is invalid")
    require(isinstance(segment_rows, int) and segment_rows > 0, "segment rows are invalid")
    require(isinstance(batches, list) and batches == sorted(set(batches)),
            "batch list is not strict")
    require(all(isinstance(batch, int) and 0 < batch <= documents for batch in batches),
            "batch size is invalid")
    rows = value.get("rows")
    require(isinstance(rows, list) and len(rows) == len(batches), "batch row count differs")
    for batch, row in zip(batches, rows):
        require(row.get("status") == "MATERIALIZED", f"batch {batch} is not materialized")
        require(row.get("batch_documents") == batch, f"batch {batch} identity differs")
        require(row.get("durable_commits") == (documents + batch - 1) // batch,
                f"batch {batch} durable commit count differs")
        require(isinstance(row.get("materialize_ms"), (int, float)) and
                row["materialize_ms"] >= 0, f"batch {batch} timing is invalid")
        require(isinstance(row.get("db_bytes"), int) and row["db_bytes"] > 0,
                f"batch {batch} database size is invalid")
    hashes = value.get("inputs_sha256", {})
    for name in ("thq", "codes", "scales"):
        require(isinstance(hashes.get(name), str) and len(hashes[name]) == 64,
                f"missing input hash: {name}")


def self_test() -> None:
    baseline = {
        "schema_version": 1, "family": "native_thq_mdbx_bounded_batch_sweep_v1",
        "status": "EXECUTED", "documents": 1000, "segment_rows": 128,
        "batch_documents": [256, 1000],
        "inputs_sha256": {name: "0" * 64 for name in ("thq", "codes", "scales")},
        "rows": [{"status": "MATERIALIZED", "batch_documents": 256,
                   "durable_commits": 4, "materialize_ms": 1, "db_bytes": 1},
                  {"status": "MATERIALIZED", "batch_documents": 1000,
                   "durable_commits": 1, "materialize_ms": 1, "db_bytes": 1}],
    }
    audit(baseline)
    for label, mutate in (
        ("wrong commits", lambda x: x["rows"][0].update(durable_commits=1)),
        ("missing hash", lambda x: x["inputs_sha256"].pop("codes")),
        ("duplicate batch", lambda x: x.update(batch_documents=[256, 256])),
    ):
        candidate = json.loads(json.dumps(baseline))
        mutate(candidate)
        try:
            audit(candidate)
        except ValueError:
            continue
        raise ValueError(f"mutation accepted: {label}")
    print("audit-native-mdbx-batch-sweep self-test PASS")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    audit(json.loads(args.result.read_text(encoding="utf-8")))
    print(f"audit-native-mdbx-batch-sweep: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-native-mdbx-batch-sweep: {error}")
