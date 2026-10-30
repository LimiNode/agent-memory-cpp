#!/usr/bin/env python3
"""Fail-closed validator for the matched C++ lexical R2 receipt."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(message)


def validate(path: Path) -> None:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read R2 receipt: {exc}")
    if receipt.get("schema_version") != 1 or receipt.get("protocol") != "lexical_retrieval_qualification_r2":
        fail("unsupported R2 receipt schema")
    query_count = receipt.get("query_count")
    if not isinstance(query_count, int) or query_count <= 0:
        fail("invalid R2 query count")
    if receipt.get("candidate_depth") != 10:
        fail("R2 candidate depth must be 10 for the matched smoke")
    tolerance = receipt.get("score_tolerance")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) \
            or not math.isfinite(tolerance) or tolerance <= 0:
        fail("R2 score tolerance is missing or invalid")
    rows = receipt.get("backends")
    if (
        not isinstance(rows, list)
        or len(rows) != 3
        or {row.get("id") for row in rows} != {"bm25", "flat-v2", "segmented-v2"}
    ):
        fail("R2 receipt must contain BM25, flat-v2, and segmented-v2")
    for row in rows:
        if row.get("ranking_parity") is not True or row.get("score_parity") is not True:
            fail(f"logical parity failed for {row.get('id')}")
        for field in ("index_build_ms", "update_ms", "reopen_ms", "index_bytes"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                fail(f"invalid lifecycle field {field} for {row.get('id')}")
            if field == "index_bytes" and row.get("id") != "bm25" and value == 0:
                fail(f"durable index has no bytes for {row.get('id')}")
        quality = row.get("quality")
        if not isinstance(quality, dict):
            fail(f"quality metrics missing for {row.get('id')}")
        for field in ("recall_at_1", "recall_at_5", "ndcg_at_10", "mrr", "no_answer_accuracy"):
            value = quality.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                fail(f"invalid quality metric {field} for {row.get('id')}")
        timing = row.get("latency_ms")
        if not isinstance(timing, dict) or timing.get("sample_count") != query_count \
                or timing.get("observation_unit") != "one_query" \
                or timing.get("statistical_status") != "directional":
            fail(f"R2 timing semantics are incomplete for {row.get('id')}")
        for field in ("p50", "p95", "p99"):
            value = timing.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                fail(f"invalid latency percentile {field} for {row.get('id')}")
    if receipt.get("quality_status") != "QUALITY_INCONCLUSIVE":
        fail("R2 must not claim a quality conclusion without independent qrels")
    if receipt.get("hydration_status") != "NOT_MEASURED_NO_CANONICAL_READ_PATH":
        fail("R2 must not claim canonical hydration evidence")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    validate(args.receipt)
    print(json.dumps({"status": "PASS", "receipt": args.receipt.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
