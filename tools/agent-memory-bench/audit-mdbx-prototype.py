#!/usr/bin/env python3
"""Fail-closed audit for the post-freeze MDBX prototype receipt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1, "unsupported schema")
    require(value.get("family") == "native_mdbx_packed_int8_prototype_v1", "unexpected family")
    require(value.get("status") == "EXECUTED", "prototype did not execute")
    require(value.get("metric") == "reconstructed_cosine_exact", "metric is not exact cosine")
    require(value.get("documents") == 1_000_000 and value.get("queries") == 305, "fixture shape differs")
    require(value.get("warmups") == 1 and value.get("repeats") == 5, "timing contract differs")
    require(value.get("candidate_k") == 128 and value.get("candidate_source", "").startswith("exact FP32"), "candidate isolation contract differs")
    for digest_name in ("fixture_receipt_sha256", "runner_source_sha256"):
        digest = value.get(digest_name)
        require(isinstance(digest, str) and len(digest) == 64, f"missing {digest_name}")
    rows = value.get("rows")
    require(isinstance(rows, list) and {row.get("layout") for row in rows} == {"row_kv", "segment_blob"}, "layout set differs")
    for row in rows:
        require(row.get("ordered_parity") == "305/305", f"{row.get('layout')} parity incomplete")
        require(row.get("db_bytes", 0) > 0 and row.get("materialize_ms", -1) >= 0 and row.get("durable_commits", 0) > 0, "materialization receipt is invalid")
        for name in ("reopen_first_query_ms", "p50_ms", "p95_ms", "p99_ms", "read_decode_p50_ms", "score_p50_ms"):
            require(isinstance(row.get(name), (int, float)) and row[name] >= 0, f"{row.get('layout')}.{name} is invalid")
    limitations = value.get("limitations")
    require(isinstance(limitations, list) and any("not Prototype-IVF" in item for item in limitations), "prototype limitation is missing")


def self_test() -> None:
    baseline = {"schema_version": 1, "family": "native_mdbx_packed_int8_prototype_v1", "status": "EXECUTED", "metric": "reconstructed_cosine_exact", "documents": 1_000_000, "queries": 305, "warmups": 1, "repeats": 5, "candidate_k": 128, "candidate_source": "exact FP32 top-k", "fixture_receipt_sha256": "0" * 64, "runner_source_sha256": "1" * 64, "rows": [{"layout": name, "ordered_parity": "305/305", "db_bytes": 1, "materialize_ms": 1, "durable_commits": 1, "reopen_first_query_ms": 1, "p50_ms": 1, "p95_ms": 1, "p99_ms": 1, "read_decode_p50_ms": 1, "score_p50_ms": 1} for name in ("row_kv", "segment_blob")], "limitations": ["not Prototype-IVF"]}
    audit(baseline)
    baseline["rows"][0]["ordered_parity"] = "0/305"
    try:
        audit(baseline)
    except ValueError:
        print("audit-mdbx-prototype self-test PASS")
        return
    raise ValueError("negative parity mutation was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.result is None:
        parser.error("--result is required")
    audit(json.loads(args.result.read_text(encoding="utf-8")))
    print(f"audit-mdbx-prototype: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
