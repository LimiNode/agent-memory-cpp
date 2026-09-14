#!/usr/bin/env python3
"""Fail-closed audit for the exact representative top-R oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


R_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192)
BUDGETS = (5_000, 10_000, 20_000, 50_000)
QUERIES = 152
TEACHERS = 10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--thq-manifest", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_representative_top_r_oracle_v1" and
            receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False,
            "top-R oracle receipt differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw),
            "top-R oracle raw SHA differs")
    require(raw["family"] == receipt["family"] and raw["schema_version"] == 1,
            "top-R oracle raw schema differs")
    rows = raw["rows"]
    require(len(rows) == len(R_VALUES) * len(BUDGETS) * QUERIES,
            "top-R oracle row count differs")
    thq = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    teachers = np.memmap(Path(thq["references"]["teacher_ids"]["path"]), mode="r",
                         dtype="<i8", shape=(QUERIES, TEACHERS))
    identities: set[tuple[int, int, int]] = set()
    for row in rows:
        qi = int(row["query"]); r = int(row["representative_hits"])
        budget = int(row["requested_candidate_budget"])
        identity = (qi, r, budget)
        require(0 <= qi < QUERIES and r in R_VALUES and budget in BUDGETS,
                f"top-R row identity differs: {identity}")
        require(identity not in identities, f"duplicate top-R row: {identity}")
        identities.add(identity)
        missed = np.asarray(row["candidate_teacher_ids_missed"], dtype=np.int64)
        teacher = np.asarray(teachers[qi], dtype=np.int64)
        require(np.unique(missed).size == missed.size and np.all(np.isin(missed, teacher)),
                f"top-R missed IDs are not teacher IDs: {identity}")
        expected = 1.0 - float(len(missed)) / TEACHERS
        require(abs(expected - float(row["candidate_teacher_recall"])) < 1e-9,
                f"top-R recall is not independently reproducible: {identity}")
        require(int(row["candidate_count"]) >= 0 and
                int(row["postings_touched"]) >= 0 and
                int(row["posting_entries_touched"]) >= int(row["postings_touched"]),
                f"top-R work counters invalid: {identity}")
        require(int(row["parent_addresses_in_prefix"]) >= int(row["postings_touched"]),
                f"top-R prefix counter invalid: {identity}")
    require(len(identities) == len(rows), "top-R row matrix incomplete")
    require(len(receipt["summaries"]) == len(R_VALUES) * len(BUDGETS),
            "top-R summary matrix incomplete")
    print(json.dumps({"family": "semantic_r4_representative_top_r_oracle_audit_v1",
                      "status": "PASS", "rows": len(rows),
                      "summaries": len(receipt["summaries"])}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-representative-top-r-oracle: {error}")
