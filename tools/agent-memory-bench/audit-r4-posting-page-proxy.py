#!/usr/bin/env python3
"""Fail-closed audit for the logical R4 posting/page proxy."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()), "p05": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)),
            "max": float(a.max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8")); raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == raw["family"] == "semantic_r4_posting_page_proxy_v1" and
            receipt["execution_status"] == "EXECUTED" and receipt["production_activation"] is False,
            "proxy identity differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw) and
            receipt["thq_manifest_sha256"] == sha256(args.thq_manifest) and
            receipt["r4_layout_manifest_sha256"] == sha256(args.r4_layout_manifest) and
            receipt["native_receipt_sha256"] == sha256(args.native_receipt) and
            receipt["runner_sha256"] == sha256(args.runner), "proxy provenance differs")
    rows = raw["rows"]
    require(len(rows) == QUERIES * len(BUDGETS), "proxy row count differs")
    identities = set()
    for row in rows:
        identity = (int(row["query"]), int(row["requested_candidate_budget"]))
        require(identity not in identities and 0 <= identity[0] < QUERIES and identity[1] in BUDGETS,
                f"proxy identity differs: {identity}")
        identities.add(identity)
        require(int(row["candidate_count"]) >= identity[1] and
                int(row["posting_entries_touched"]) >= int(row["postings_touched"]) >= 0 and
                int(row["posting_pages"]) >= 0 and int(row["thq_document_pages"]) >= 0 and
                int(row["union_pages"]) == int(row["posting_pages"]) + int(row["thq_document_pages"]),
                f"proxy accounting differs: {identity}")
    summary_map = {int(row["requested_candidate_budget"]): row for row in receipt["summaries"]}
    require(set(summary_map) == set(BUDGETS), "proxy summary grid differs")
    for budget in BUDGETS:
        selected = [row for row in rows if int(row["requested_candidate_budget"]) == budget]
        require(int(summary_map[budget]["query_count"]) == len(selected), f"proxy summary count differs: {budget}")
        for field in ("candidate_count", "postings_touched", "posting_entries_touched", "posting_pages",
                      "thq_document_pages", "union_pages"):
            for key, value in aggregate([float(row[field]) for row in selected]).items():
                require(math.isclose(float(summary_map[budget][field][key]), value, abs_tol=1e-9),
                        f"proxy aggregate differs: {budget}/{field}/{key}")
    print(json.dumps({"family": "semantic_r4_posting_page_proxy_audit_v1", "status": "PASS",
                      "rows": len(rows), "summaries": len(summary_map)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-posting-page-proxy: {error}")
